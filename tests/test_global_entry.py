"""Real installer lifecycle checks; every subprocess has an isolated user environment."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='entry-test-')
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / 'source with space and "quote'
        shutil.copytree(SOURCE, self.repo, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        self.home = self.base / 'home'
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), CODEX_HOME=str(self.home / 'codex "home'),
                        CLAUDE_CONFIG_DIR=str(self.home / '.claude'), PYTHONDONTWRITEBYTECODE='1')
        fakebin = self.base / 'fakebin'
        fakebin.mkdir()
        for cli in ('claude', 'codex'):
            script = fakebin / cli
            script.write_text('#!/bin/sh\necho 0.0.0\n')
            script.chmod(0o755)
        self.env['PATH'] = str(fakebin) + os.pathsep + self.env['PATH']
        self.target = Path(self.env['CODEX_HOME']) / 'AGENTS.md'
        self.manifest = self.home / '.claude/ai-anti.manifest.json'

    def tearDown(self):
        self.temp.cleanup()

    def run_install(self, *args, ok=True):
        result = subprocess.run(['bash', str(self.repo / 'install.sh'), *args], env=self.env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(result.returncode == 0, ok, result.stdout.decode())
        return result.stdout.decode()

    def snapshot(self):
        return {str(p.relative_to(self.home)): ('link', os.readlink(p)) if p.is_symlink()
                else ('file', hashlib.sha256(p.read_bytes()).hexdigest()) if p.is_file()
                else ('dir', '') for p in self.home.rglob('*')}

    def user_text(self, text):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_bytes(text)

    def test_bytes_idempotence_and_uninstall_recorded_path(self):
        for text in (b'Chinese preference no newline', b'preference\n\n\n', b'', b'\r\n'):
            with self.subTest(text=text):
                self.user_text(text)
                self.run_install('--with-codex')
                first = self.target.read_bytes()
                self.assertIn(json.dumps(str(self.repo / 'adapters/codex/AGENTS.global.md')).encode(), first)
                self.run_install()
                self.assertEqual(first, self.target.read_bytes())
                self.env['CODEX_HOME'] = str(self.home / 'different')
                self.run_install('--uninstall')
                self.assertEqual(text, self.target.read_bytes())
                self.env['CODEX_HOME'] = str(self.target.parent)

    def test_created_file_removal_and_legacy_upgrade(self):
        self.run_install('--with-codex')
        data = json.loads(self.manifest.read_text())
        data.pop('codex_entry')
        data['schema_version'] = 2
        self.target.unlink()
        self.manifest.write_text(json.dumps(data))
        self.run_install()
        self.assertEqual(json.loads(self.manifest.read_text())['schema_version'], 3)
        self.run_install('--without-codex')
        self.assertFalse(self.target.exists())
        self.run_install('--uninstall')
        self.assertFalse(self.manifest.exists())

    def test_plain_install_does_not_read_codex(self):
        self.target.parent.mkdir()
        (self.target.parent / 'AGENTS.override.md').symlink_to('/nonexistent')
        self.run_install()
        self.assertFalse(self.target.exists())

    def test_fail_closed_inputs(self):
        for kind in ('override', 'override-link', 'target-link', 'broken', 'duplicate', 'reverse', 'parent-link'):
            with self.subTest(kind=kind):
                case = self.home / kind
                case.mkdir()
                self.env['CODEX_HOME'] = str(case)
                target = case / 'AGENTS.md'
                if kind == 'override':
                    (case / 'AGENTS.override.md').write_text('override')
                elif kind == 'override-link':
                    (case / 'AGENTS.override.md').symlink_to('/nonexistent')
                elif kind == 'target-link':
                    target.symlink_to('/nonexistent')
                elif kind == 'parent-link':
                    link = self.home / 'linked-parent'
                    link.symlink_to(case, target_is_directory=True)
                    self.env['CODEX_HOME'] = str(link)
                else:
                    begin, end = '<!-- ai-anti:codex:begin -->', '<!-- ai-anti:codex:end -->'
                    target.write_text(begin if kind == 'broken' else (begin + end) * 2 if kind == 'duplicate' else end + begin)
                before = self.snapshot()
                self.run_install('--with-codex', ok=False)
                self.assertEqual(before, self.snapshot())

    def test_home_change_and_status_damage(self):
        self.run_install('--with-codex')
        original_home = self.env['CODEX_HOME']
        self.env['CODEX_HOME'] = str(self.home / 'other')
        before = self.snapshot()
        self.run_install(ok=False)
        self.assertEqual(before, self.snapshot())
        self.env['CODEX_HOME'] = original_home
        self.target.write_bytes(self.target.read_bytes().replace(b'AGENTS.global.md', b'WRONG.md'))
        before = self.snapshot()
        self.assertIn('differs from current source', self.run_install('--status'))
        self.assertEqual(before, self.snapshot())

    def test_status_missing_shared_source(self):
        self.run_install('--with-codex')
        (self.repo / 'core/WORKFLOW_ENTRY.md').unlink()
        before = self.snapshot()
        self.assertIn('unreadable Codex source', self.run_install('--status'))
        self.assertEqual(before, self.snapshot())

    def test_source_move_refresh(self):
        self.run_install('--with-codex')
        moved = self.base / 'moved source'
        self.repo.rename(moved)
        self.repo = moved
        self.run_install()
        self.assertIn(str(moved).encode(), self.target.read_bytes())
        self.run_install('--uninstall')
        self.assertFalse(self.target.exists())

    def test_override_guard_mutation(self):
        installer = self.repo / 'install.sh'
        source = installer.read_text()
        self.assertIn('if override.exists() and override.stat().st_size:', source)
        installer.write_text(source.replace('if override.exists() and override.stat().st_size:', 'if False:'))
        self.target.parent.mkdir()
        (self.target.parent / 'AGENTS.override.md').write_text('shadow')
        # The lifecycle assertion must reject the mutated installer, proving this guard matters.
        with self.assertRaises(AssertionError):
            self.run_install('--with-codex', ok=False)


if __name__ == '__main__':
    unittest.main()
