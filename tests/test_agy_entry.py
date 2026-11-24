"""Exercise AGY plugin ownership using an isolated home and a fake agy."""
import json
import os
import subprocess
import unittest

import test_global_entry


class AgyEntryTests(unittest.TestCase):
    def setUp(self):
        test_global_entry.EntryTests.setUp(self)
        fakebin = self.base / 'fakebin'
        self.version_calls = self.base / 'agy-calls.txt'
        self.env.update(AGY_TEST_VERSION='1.5.13', AGY_TEST_CALLS=str(self.version_calls))
        for name, script in {
            'agy': ('#!/bin/sh\n'
                    'test "$#" = 1 && test "$1" = --version || exit 99\n'
                    'echo "$1" >> "$AGY_TEST_CALLS"\n'
                    'echo "agy $AGY_TEST_VERSION"\n'),
            'claude': '#!/bin/sh\necho "2.1.280"\n',
            'codex': '#!/bin/sh\necho "codex-cli 0.169.1"\n',
        }.items():
            (fakebin / name).write_text(script)
            (fakebin / name).chmod(0o755)
        self.gemini = self.home / '.gemini'
        self.plugin = self.gemini / 'config/plugins/ai-anti'
        self.source = self.repo / 'adapters/agy/plugin'

    tearDown = test_global_entry.EntryTests.tearDown
    snapshot = test_global_entry.EntryTests.snapshot

    def run_install(self, *args, ok=True):
        result = subprocess.run(['/bin/bash', str(self.repo / 'install.sh'), *args],
                                env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, timeout=20)
        self.assertEqual(result.returncode == 0, ok, result.stdout)
        return result.stdout

    def record(self):
        return json.loads(self.manifest.read_text())['agy_plugin']

    def test_opt_in_reinstall_inheritance_and_explicit_removal(self):
        self.run_install('--with-agy')
        self.assertEqual(os.readlink(self.plugin), str(self.source))
        self.assertEqual(json.loads(self.manifest.read_text())['schema_version'], 3)
        expected = {'installed': True, 'path': str(self.plugin), 'target': str(self.source),
                    'created_dirs': [str(p) for p in (self.gemini, self.gemini / 'config', self.plugin.parent)]}
        self.assertEqual(self.record(), expected)
        plugin_info = json.loads((self.plugin / 'plugin.json').read_text())
        self.assertEqual((plugin_info['name'], plugin_info['version']), ('ai-anti', '0.1.0'))
        self.run_install()
        self.assertEqual(self.record(), expected)
        self.assertEqual(os.readlink(self.plugin), str(self.source))
        self.assertFalse(self.version_calls.exists())
        self.run_install('--without-agy')
        self.assertFalse(self.gemini.exists())
        self.assertNotIn('agy_plugin', json.loads(self.manifest.read_text()))
        self.run_install('--uninstall')
        self.assertEqual(list(self.home.iterdir()), [])

    def test_uninstall_removes_only_installer_created_empty_directories(self):
        self.run_install('--with-agy')
        self.run_install('--uninstall')
        self.assertEqual(list(self.home.iterdir()), [])
        self.gemini.mkdir()
        self.run_install('--with-agy')
        self.assertEqual(self.record()['created_dirs'], [str(self.gemini / 'config'), str(self.plugin.parent)])
        self.run_install('--uninstall')
        self.assertEqual(list(self.gemini.iterdir()), [])
        self.assertTrue(self.gemini.is_dir())
        self.run_install('--with-agy')
        user_file = self.plugin.parent / 'user.plugin'
        user_file.write_text('user data')
        self.run_install('--without-agy')
        self.assertFalse(self.plugin.is_symlink())
        self.assertEqual(user_file.read_text(), 'user data')

    def test_exact_existing_link_can_be_adopted(self):
        self.plugin.parent.mkdir(parents=True)
        self.plugin.symlink_to(self.source)
        self.run_install('--with-agy')
        self.assertEqual(self.record()['created_dirs'], [])
        self.run_install('--uninstall')
        self.assertFalse(self.plugin.is_symlink())
        self.assertTrue(self.plugin.parent.is_dir())

    def test_first_install_rejects_other_targets_without_changes(self):
        self.plugin.parent.mkdir(parents=True)
        for target in (self.repo / 'adapters/codex', self.base / 'unrelated',
                       str(self.source.parent) + '/./plugin', str(self.source) + '\n'):
            with self.subTest(target=target):
                self.plugin.symlink_to(target)
                try:
                    baseline = self.snapshot()
                    result = self.run_install('--with-agy', '--with-codex', ok=False)
                    self.assertIn('AGY 插件路径无法精确认领', result)
                    self.assertEqual(self.snapshot(), baseline)
                finally:
                    self.plugin.unlink(missing_ok=True)
        self.plugin.mkdir()
        baseline = self.snapshot()
        self.run_install('--with-agy', ok=False)
        self.assertEqual(self.snapshot(), baseline)

    def test_replacement_link_survives_disable_and_uninstall(self):
        for removal in ('--without-agy', '--uninstall'):
            with self.subTest(removal=removal):
                self.run_install('--with-agy')
                other = self.repo / 'adapters/codex'
                self.plugin.unlink()
                self.plugin.symlink_to(other)
                baseline = self.snapshot()
                self.run_install(ok=False)
                self.assertEqual(self.snapshot(), baseline)
                self.run_install(removal)
                self.assertEqual(os.readlink(self.plugin), str(other))
                self.plugin.unlink()

    def test_symlink_and_non_directory_ancestors_reject_before_writes(self):
        elsewhere = self.base / 'elsewhere'
        elsewhere.mkdir()
        for ancestor in (self.gemini, self.gemini / 'config', self.plugin.parent):
            with self.subTest(ancestor=ancestor):
                ancestor.parent.mkdir(parents=True, exist_ok=True)
                ancestor.symlink_to(elsewhere)
                baseline = self.snapshot()
                self.assertIn('AGY 插件祖先目录', self.run_install('--with-agy', ok=False))
                self.assertEqual(self.snapshot(), baseline)
                self.assertEqual(list(elsewhere.iterdir()), [])
                ancestor.unlink()
        self.plugin.parent.write_text('a file')
        baseline = self.snapshot()
        self.run_install('--with-agy', ok=False)
        self.assertEqual(self.snapshot(), baseline)

    def test_uninstall_does_not_follow_replaced_ancestor(self):
        self.run_install('--with-agy')
        moved = self.base / 'moved plugins'
        self.plugin.parent.rename(moved)
        self.plugin.parent.symlink_to(moved)
        self.run_install('--uninstall')
        self.assertTrue(self.plugin.parent.is_symlink())
        self.assertEqual(os.readlink(moved / self.plugin.name), str(self.source))

    def test_repository_move_refreshes_only_the_recorded_target(self):
        self.run_install('--with-agy')
        new_repo = self.base / 'moved checkout with space'
        self.repo.rename(new_repo)
        self.repo = new_repo
        new_source = self.repo / 'adapters/agy/plugin'
        self.assertTrue(self.plugin.is_symlink())
        self.assertFalse(self.plugin.exists())
        self.assertEqual(os.readlink(self.plugin), str(self.source))
        self.run_install()
        self.assertEqual(os.readlink(self.plugin), str(new_source))
        self.assertEqual(self.record()['target'], str(new_source))
        self.run_install('--uninstall')
        self.assertFalse(self.gemini.exists())

    def test_codex_option_does_not_opt_in_to_agy(self):
        self.run_install('--with-codex')
        self.assertFalse(self.gemini.exists())
        self.assertNotIn('agy_plugin', json.loads(self.manifest.read_text()))
        self.assertNotIn('警告 agy', self.run_install('--status'))
        self.assertFalse(self.version_calls.exists())
        baseline = self.snapshot()
        opts = ('--with-agy', '--without-agy')
        for ordered in (opts, opts[::-1]):
            self.run_install(*ordered, ok=False)
            self.assertEqual(self.snapshot(), baseline)

    def test_status_checks_enabled_link_sources_and_version(self):
        self.run_install('--with-agy')
        baseline = self.snapshot()
        status = self.run_install('--status')
        self.assertIn('🟢 AGY 插件：已安装', status)
        self.assertRegex(status, r'OK\s+agy\s+1[.]5[.]13')
        self.assertEqual(self.version_calls.read_text(), '--version\n')
        self.assertEqual(self.snapshot(), baseline)
        self.env['AGY_TEST_VERSION'] = '9.0.0'
        self.assertRegex(self.run_install('--status'), r'警告 agy\s+9[.]0[.]0')
        self.plugin.unlink()
        missing = self.source / 'skills/codex-review/SKILL.md'
        missing.unlink()
        baseline = self.snapshot()
        broken = self.run_install('--status')
        self.assertIn('AGY 插件链接目标错误或缺失', broken)
        self.assertIn('skills/codex-review/SKILL.md', broken)
        self.assertEqual(self.snapshot(), baseline)

    def test_sources_are_checked_before_first_install(self):
        sources = [self.source.parent / rel for rel in (
            'plugin/plugin.json', 'plugin/rules/AGENTS.md', 'plugin/skills/agy-relay/SKILL.md',
            'plugin/skills/codex-review/SKILL.md', 'scripts/codex_review.py', 'scripts/claude_plan.py')]
        sources.append(self.repo / 'apps/macos/backend/relay_routes.py')
        for path in sources:
            rel = str(path.relative_to(self.repo))
            hidden = path.with_suffix('.saved')
            with self.subTest(source=rel):
                path.rename(hidden)
                self.assertIn(rel, self.run_install('--with-agy', ok=False))
                self.assertEqual(list(self.home.iterdir()), [])
                hidden.rename(path)


if __name__ == '__main__':
    unittest.main()
