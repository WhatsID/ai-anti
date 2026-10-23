"""Exercise real installer reports with isolated homes, sources and Git fixtures."""
import hashlib
import json
import shutil
import os
from pathlib import Path
import subprocess
import sys
import unittest
from test_global_entry import EntryTests


class HealthTests(unittest.TestCase):
    def setUp(self):
        EntryTests.setUp(self)
        if os.environ.get("AI_ANTI_HEALTH_OLD_INSTALLER"):
            shutil.copyfile(os.environ["AI_ANTI_HEALTH_OLD_INSTALLER"], self.repo / "install.sh")
        fake = self.base / 'fakebin'
        (fake / 'claude').write_text('#!/bin/sh\necho 2.1.280\n')
        (fake / 'codex').write_text('#!/bin/sh\necho 0.156.0\n')
        self.project = self.base / 'project with "quote'
        self.project.mkdir()

    tearDown = EntryTests.tearDown
    snapshot = EntryTests.snapshot

    def tree_snapshot(self, root):
        return {str(p.relative_to(root)): os.readlink(p) if p.is_symlink()
                else hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file()
                else "directory-or-special" for p in root.rglob("*")}

    def run_install(self, *args, ok=True):
        result = subprocess.run(['bash', str(self.repo / 'install.sh'), *args], env=self.env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=20)
        self.assertEqual(result.returncode == 0, ok, result.stdout.decode())
        return result.stdout.decode()

    def config(self, data):
        path = self.home / '.config/ai-anti/config.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def status(self):
        return self.run_install('--status', '--project', str(self.project))

    def git(self, *args):
        env = {k:v for k,v in self.env.items() if not k.startswith('GIT_')}
        return subprocess.run(['git', '-C', str(self.project), *args], env=env, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)

    def test_status_dots_and_summary(self):
        installed = self.run_install('--with-codex', '--with-hook')
        self.assertFalse(any(dot in installed for dot in ('🟢', '🟡', '🔴')))
        before = self.snapshot()
        healthy = self.status()
        self.assertIn('🟢 状态：全部正常', healthy)
        self.assertIn('🟢 Codex entry: OK', healthy)
        self.assertIn('🟢 CLAUDE.md 标记块', healthy)
        self.assertIn('🟢 协作模式：rules', healthy)
        self.assertIn('🟡 Git：非 Git', healthy)
        self.assertNotIn('Codex 检查标志', healthy)
        self.assertNotIn('\x1b', healthy)
        self.assertEqual(before, self.snapshot())
        (self.base / 'fakebin/codex').write_text('#!/bin/sh\necho 0.999.0\n')
        warning = self.status()
        self.assertIn('🟡   警告 codex', warning)
        self.assertIn('🟡 状态：有 1 项需要留意', warning)
        self.target.write_text('missing marker')
        failure = self.status()
        self.assertIn('🔴 ai-anti: Codex entry:', failure)
        self.assertIn('🔴 状态：有 2 项需要留意', failure)
        self.manifest.unlink()
        self.assertIn('🔴 manifest：不存在', self.status())

    def test_missing_source_false_health(self):
        self.run_install('--with-codex')
        (self.repo / 'core/TESTING.md').unlink()
        before = self.snapshot()
        source = self.tree_snapshot(self.repo)
        project = self.tree_snapshot(self.project)
        output = self.run_install('--status')
        self.assertIn('TESTING.md', output)
        self.assertNotIn('状态：全部正常', output)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(source, self.tree_snapshot(self.repo))
        self.assertEqual(project, self.tree_snapshot(self.project))
        self.run_install('--uninstall')

    def test_problem_review_managed_lifecycle_and_health(self):
        self.run_install('--with-codex')
        skill = self.home / '.agents/skills/bug-review'
        command = self.home / '.claude/commands/bug-review.md'
        self.assertTrue(skill.is_symlink())
        self.assertTrue(command.is_symlink())
        self.assertIn('状态：全部正常', self.status())
        (self.repo / 'core/BUG_REVIEW.md').unlink()
        self.assertIn('BUG_REVIEW.md', self.status())
        self.run_install('--uninstall')
        self.assertFalse(skill.is_symlink())
        self.assertFalse(command.is_symlink())

    def legacy_review_install(self):
        # Recreate the just-replaced public layout using the same installer ownership
        # mechanism, then rename sources in place so installed old links dangle.
        pairs = [('adapters/codex/skills/bug-review', 'adapters/codex/skills/problem-review'),
                 ('adapters/claude-code/commands/bug-review.md', 'adapters/claude-code/commands/problem-review.md'),
                 ('adapters/codex/skills/claude-review/scripts/bug_review.py', 'adapters/codex/skills/claude-review/scripts/problem_review.py'),
                 ('core/BUG_REVIEW.md', 'core/PROBLEM_REVIEW.md'),
                 ('docs/BUG_REVIEW.md', 'docs/PROBLEM_REVIEW.md')]
        installer = self.repo / 'install.sh'
        candidate = installer.read_text()
        for new, old in pairs:
            (self.repo / new).rename(self.repo / old)
        installer.write_text(candidate.replace('bug-review', 'problem-review').replace('BUG_REVIEW', 'PROBLEM_REVIEW'))
        self.run_install('--with-codex')
        for new, old in pairs:
            (self.repo / old).rename(self.repo / new)
        installer.write_text(candidate)
        return (self.home / '.agents/skills/problem-review',
                self.home / '.claude/commands/problem-review.md')

    def test_review_rename_migrates_owned_dangling_links(self):
        old_skill, old_command = self.legacy_review_install()
        for path in (old_skill, old_command):
            self.assertTrue(path.is_symlink())
            self.assertFalse(path.exists())
        self.run_install()
        for path in (old_skill, old_command):
            self.assertFalse(path.is_symlink())
        skill = self.home / '.agents/skills/bug-review'
        command = self.home / '.claude/commands/bug-review.md'
        self.assertTrue((skill / 'SKILL.md').is_file())
        self.assertTrue(command.is_file())
        self.assertIn('状态：全部正常', self.status())
        self.run_install('--uninstall')
        self.assertFalse(skill.is_symlink())
        self.assertFalse(command.is_symlink())

    def test_review_rename_preserves_user_replacements(self):
        old_skill, old_command = self.legacy_review_install()
        old_skill.unlink()
        old_command.unlink()
        outside = self.home / 'user-skill'
        outside.mkdir()
        (outside / 'owned.txt').write_text('user content')
        old_skill.symlink_to(outside)
        old_command.write_text('user command')
        self.run_install()
        self.assertEqual(old_skill.resolve(), outside)
        self.assertEqual(old_command.read_text(), 'user command')
        self.assertTrue((self.home / '.agents/skills/bug-review/SKILL.md').is_file())
        self.run_install('--uninstall')
        self.assertEqual(old_skill.resolve(), outside)
        self.assertEqual(old_command.read_text(), 'user command')
        self.assertEqual((outside / 'owned.txt').read_text(), 'user content')

    def test_source_preflight(self):
        path = self.repo / 'core/DEBUGGING.md'
        for kind in ('empty', 'bad-utf8', 'fifo', 'missing'):
            with self.subTest(kind=kind):
                if path.exists(): path.unlink()
                if kind == 'empty': path.write_text('')
                elif kind == 'bad-utf8': path.write_bytes(b'\xff')
                elif kind == 'fifo': os.mkfifo(path)
                before = self.snapshot()
                self.run_install('--with-codex', ok=False)
                self.assertEqual(before, self.snapshot())

    def test_planning_source_preflight(self):
        path = self.repo / 'core/PLANNING.md'
        for kind in ('missing', 'empty', 'bad-utf8', 'fifo', 'directory'):
            with self.subTest(kind=kind):
                if path.is_dir(): path.rmdir()
                elif path.exists(): path.unlink()
                if kind == 'empty': path.write_text('')
                elif kind == 'bad-utf8': path.write_bytes(b'\xff')
                elif kind == 'fifo': os.mkfifo(path)
                elif kind == 'directory': path.mkdir()
                before = self.snapshot()
                output = self.run_install('--with-codex', ok=False)
                self.assertIn('PLANNING.md', output)
                self.assertEqual(before, self.snapshot())

    def test_planning_source_status_and_uninstall(self):
        self.run_install('--with-codex')
        path = self.repo / 'core/PLANNING.md'
        for kind in ('missing', 'empty', 'bad-utf8', 'fifo', 'directory'):
            with self.subTest(kind=kind):
                if path.is_dir(): path.rmdir()
                elif path.exists(): path.unlink()
                if kind == 'empty': path.write_text('')
                elif kind == 'bad-utf8': path.write_bytes(b'\xff')
                elif kind == 'fifo': os.mkfifo(path)
                elif kind == 'directory': path.mkdir()
                before = self.snapshot()
                source = self.tree_snapshot(self.repo)
                project = self.tree_snapshot(self.project)
                output = self.status()
                self.assertIn('PLANNING.md', output)
                self.assertNotIn('状态：全部正常', output)
                self.assertEqual(before, self.snapshot())
                self.assertEqual(source, self.tree_snapshot(self.repo))
                self.assertEqual(project, self.tree_snapshot(self.project))
        path.rmdir()  # Leave the required source missing for uninstall.
        self.run_install('--uninstall')
        self.assertFalse(self.manifest.exists())

    def test_design_source_preflight(self):
        path = self.repo / 'core/DESIGN.md'
        for kind in ('missing', 'empty', 'bad-utf8', 'fifo', 'directory'):
            with self.subTest(kind=kind):
                if path.is_dir(): path.rmdir()
                elif path.exists(): path.unlink()
                if kind == 'empty': path.write_text('')
                elif kind == 'bad-utf8': path.write_bytes(b'\xff')
                elif kind == 'fifo': os.mkfifo(path)
                elif kind == 'directory': path.mkdir()
                before = self.snapshot()
                output = self.run_install('--with-codex', ok=False)
                self.assertIn('DESIGN.md', output)
                self.assertEqual(before, self.snapshot())

    def test_design_source_status_and_uninstall(self):
        self.run_install('--with-codex')
        path = self.repo / 'core/DESIGN.md'
        for kind in ('missing', 'empty', 'bad-utf8', 'fifo', 'directory'):
            with self.subTest(kind=kind):
                if path.is_dir(): path.rmdir()
                elif path.exists(): path.unlink()
                if kind == 'empty': path.write_text('')
                elif kind == 'bad-utf8': path.write_bytes(b'\xff')
                elif kind == 'fifo': os.mkfifo(path)
                elif kind == 'directory': path.mkdir()
                before = self.snapshot()
                source = self.tree_snapshot(self.repo)
                project = self.tree_snapshot(self.project)
                output = self.status()
                self.assertIn('DESIGN.md', output)
                self.assertNotIn('状态：全部正常', output)
                self.assertEqual(before, self.snapshot())
                self.assertEqual(source, self.tree_snapshot(self.repo))
                self.assertEqual(project, self.tree_snapshot(self.project))
        path.rmdir()  # Leave the required source missing for uninstall.
        self.run_install('--uninstall')
        self.assertFalse(self.manifest.exists())

    def test_retro_source_preflight(self):
        path = self.repo / 'core/RETRO.md'
        for kind in ('missing', 'empty', 'bad-utf8', 'fifo', 'directory'):
            with self.subTest(kind=kind):
                if path.is_dir(): path.rmdir()
                elif path.exists(): path.unlink()
                if kind == 'empty': path.write_text('')
                elif kind == 'bad-utf8': path.write_bytes(b'\xff')
                elif kind == 'fifo': os.mkfifo(path)
                elif kind == 'directory': path.mkdir()
                before = self.snapshot()
                output = self.run_install('--with-codex', ok=False)
                self.assertIn('RETRO.md', output)
                self.assertEqual(before, self.snapshot())

    def test_retro_source_status_and_uninstall(self):
        self.run_install('--with-codex')
        path = self.repo / 'core/RETRO.md'
        for kind in ('missing', 'empty', 'bad-utf8', 'fifo', 'directory'):
            with self.subTest(kind=kind):
                if path.is_dir(): path.rmdir()
                elif path.exists(): path.unlink()
                if kind == 'empty': path.write_text('')
                elif kind == 'bad-utf8': path.write_bytes(b'\xff')
                elif kind == 'fifo': os.mkfifo(path)
                elif kind == 'directory': path.mkdir()
                before = self.snapshot()
                source = self.tree_snapshot(self.repo)
                project = self.tree_snapshot(self.project)
                output = self.status()
                self.assertIn('RETRO.md', output)
                self.assertNotIn('状态：全部正常', output)
                self.assertEqual(before, self.snapshot())
                self.assertEqual(source, self.tree_snapshot(self.repo))
                self.assertEqual(project, self.tree_snapshot(self.project))
        path.rmdir()  # Leave the required source missing for uninstall.
        self.run_install('--uninstall')
        self.assertFalse(self.manifest.exists())

    def test_claude_block_and_wrong_link(self):
        self.run_install()
        md = self.home / '.claude/CLAUDE.md'
        original = md.read_text()
        for changed in (original.replace('CLAUDE.global.md', 'wrong.md'), original*2,
                        original.replace('<!-- ai-anti:end -->', ''),
                        '<!-- ai-anti:end -->\n<!-- ai-anti:begin -->'):
            md.write_text(changed)
            before = self.snapshot()
            self.assertNotIn('状态：全部正常', self.run_install('--status'))
            self.assertEqual(before, self.snapshot())
        md.write_text(original)
        link = self.home / '.claude/agents/implementer.md'
        link.unlink()
        link.symlink_to(self.repo / 'core/TESTING.md')
        self.assertIn('托管链接目标错误', self.run_install('--status'))

    def test_manifest_corruption_and_legacy(self):
        self.run_install('--with-codex')
        original = self.manifest.read_text()
        for changed in ('{', '[]', '{"links":4}', '{"hook":{"injected":[]}}',
                        '{"plugins":[4]}', '{"links":[{}]}', '{"codex_skills":{"installed":true},"codex_entry":{"path":4}}'):
            self.manifest.write_text(changed)
            out = self.run_install('--status')
            self.assertIn('manifest 损坏', out)
            self.assertNotIn('Traceback', out)
        self.manifest.write_text('{"schema_version":1,"repo":' + json.dumps(str(self.repo)) + '}')
        self.assertIn('legacy manifest', self.run_install('--status'))
        self.manifest.unlink()
        os.mkfifo(self.manifest)
        self.assertIn("manifest 损坏", self.run_install("--status"))
        self.manifest.unlink()
        self.manifest.write_text(original)
        data = json.loads(original)
        data['links'] = []
        self.manifest.write_text(json.dumps(data))
        self.assertIn('托管路由未登记', self.run_install('--status'))

    def test_enabled_hook_source(self):
        self.run_install('--with-hook')
        source = self.repo / 'adapters/claude-code/hooks/session-start.sh'
        settings = self.home / ".claude/settings.json"
        original = settings.read_bytes()
        settings.unlink()
        os.mkfifo(settings)
        self.assertIn("settings.json not a regular file", self.run_install("--status"))
        settings.unlink()
        settings.write_bytes(original)
        source.unlink()
        self.assertIn('SessionStart hook：', self.run_install('--status'))
        self.assertNotIn('状态：全部正常', self.run_install('--status'))

    def test_project_git_roots_and_readonly(self):
        self.run_install()
        self.assertIn('非 Git 目录', self.status())
        self.assertIn('完整接力缺少基线', self.status())
        self.git('init', '-b', 'other')
        self.assertIn('无 HEAD', self.status())
        self.assertIn('完整接力缺少基线', self.status())
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture.invalid', 'commit', '--allow-empty', '-m', 'fixture')
        for branch in ('feature/task', 'master'):
            self.git('branch', '-m', branch)
            with self.subTest(branch=branch):
                out = self.status()
                self.assertIn('🟢 Git：已有 HEAD', out)
                self.assertNotIn('local main', out)
                self.assertNotIn('完整接力缺少基线', out)
        self.git('checkout', '--detach', 'HEAD')
        out = self.status()
        self.assertIn('🟢 Git：已有 HEAD', out)
        self.assertNotIn('local main', out)
        self.assertNotIn('完整接力缺少基线', out)
        sentinel = self.project / 'sentinel'
        script = self.base / 'fsmonitor'
        script.write_text('#!/bin/sh\ntouch "' + str(sentinel) + '"\n')
        script.chmod(0o755)
        self.git('config', 'core.fsmonitor', str(script))
        sub = self.project / 'sub'
        sub.mkdir()
        self.env['GIT_DIR'] = '/nonexistent'
        self.env['GIT_WORK_TREE'] = '/nonexistent'
        out = self.run_install('--status', '--project', str(sub))
        self.assertIn('项目根：' + str(self.project), out)
        self.assertIn('🟢 Git：已有 HEAD', out)
        self.assertFalse(sentinel.exists())
        before = self.snapshot()
        source = self.tree_snapshot(self.repo)
        project = self.tree_snapshot(self.project)
        self.status()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(source, self.tree_snapshot(self.repo))
        self.assertEqual(project, self.tree_snapshot(self.project))

    def test_modes_hook_parity_and_scale(self):
        self.run_install()
        modefile = self.project / '.claude/relay-mode.local'
        hook = self.repo / 'adapters/claude-code/hooks/session-start.sh'
        fixtures = [({}, None), ({'relay':{'default_mode':'auto'}}, None),
                    ({'relay':{'default_mode':'required'}}, 'solo'),
                    ({'relay':{'default_mode':None}}, None), ({'relay':[]}, None),
                    ({}, ''), ({}, 'force'), ({}, 'rules')]
        for cfg, mode in fixtures:
            self.config(cfg)
            if modefile.exists(): modefile.unlink()
            if mode is not None:
                modefile.parent.mkdir(exist_ok=True)
                modefile.write_text(mode)
            out = self.status()
            result = subprocess.run(['bash', str(hook)], cwd=self.project, env=self.env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
            if result.returncode:
                self.assertIn('协作模式无效', out)
            else:
                expected = result.stdout.decode().split('] ')[1].split('（')[0]
                self.assertIn('协作模式：' + expected, out)
        for scale, expected in ((None,'未声明部署规模'), ('personal','personal'), (4,'无法确认'), (' ','无法确认')):
            self.config({'defaults':{'deployment_scale':scale}})
            self.assertIn(expected, self.status())
        modefile.unlink()
        for mode in ('solo', 'auto', 'rules', 'force'):
            self.config({'relay':{'default_mode':mode}})
            self.assertIn('协作模式：'+mode, self.status())
        config_path = self.home / '.config/ai-anti/config.json'
        config_path.write_text('{')
        self.assertIn('全局配置无法读取', self.status())
        modefile.write_text('solo')
        self.assertIn('协作模式：solo', self.status())
        self.config({'defaults':[]})
        self.assertIn('全局部署规模无法确认', self.status())
        self.config({'relay':{'default_mode':'rules'},'secret':'DO_NOT_PRINT_SENTINEL'})
        secrets = self.home / '.config/ai-anti/secrets.json'
        os.mkfifo(secrets)
        self.assertNotIn('DO_NOT_PRINT_SENTINEL', self.status())

    def test_arguments_and_symlink_project(self):
        for args in (('--project',), ('--project', str(self.project)),
                     ('--status','--uninstall','--project',str(self.project)),
                     ('--uninstall','--status','--project',str(self.project)),
                     ('--status','--project',str(self.base/'absent'))):
            before = self.snapshot()
            self.run_install(*args, ok=False)
            self.assertEqual(before, self.snapshot())
        link = self.base / 'project-link'
        link.symlink_to(self.project, target_is_directory=True)
        self.assertIn('项目根：'+str(self.project), self.run_install('--project',str(link),'--status'))


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(HealthTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
