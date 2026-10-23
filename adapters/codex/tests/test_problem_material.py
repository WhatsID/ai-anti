"""Caller-visible bundle safety and strict result behavior."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/claude-review/scripts'
sys.path.insert(0, str(SCRIPTS))
from problem_material import load_bundle, strict_json
from problem_result import validate_result
from review_material import MaterialError


def bundle(root, evidence=True):
    root.mkdir()
    (root / 'problem.txt').write_text('User asks why playback stopped. Expected continuous playback.\n')
    (root / 'analysis.txt').write_text('ANALYSIS-SECRET: candidate transport failure; E1 is incomplete.\n')
    (root / 'event.txt').write_text('10:01 playback stopped; no transport status\n')
    manifest = {'schema_version': 1, 'task_id': 'problem-task', 'revision': 1,
                'problem_file': 'problem.txt', 'analysis_file': 'analysis.txt',
                'evidence': [{'id': 'E1', 'file': 'event.txt', 'source': 'device log',
                              'locator': '10:01 lines 3-4', 'kind': 'raw_excerpt',
                              'coverage': 'one event; previous context missing'}] if evidence else [],
                'limitations': ['No full trace'], 'domain_checks': ['Check timestamp alignment'],
                'environment': 'synthetic device v1; time 2026-10-23; other details unknown'}
    save(root, manifest)
    return manifest


def save(root, manifest):
    (root / 'manifest.json').write_text(json.dumps(manifest))


def result():
    return {'assessment': 'partially_supported', 'findings': [
        {'id': 'F1', 'claim': 'Transport failed', 'assessment': 'partially_supported',
         'evidence_ids': ['E1'], 'reason': 'Missing preceding context', 'alternative': None,
         'next_check': {'needed': 'Full trace', 'purpose': 'Separate alternatives',
                        'distinguishing_observation': 'Failure before stop'}}],
        'coverage': 'One event', 'limitations': ['Partial log']}


class MaterialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / 'bundle'
        self.manifest = bundle(self.root)
    def tearDown(self):
        self.temp.cleanup()
    def bad(self):
        save(self.root, self.manifest)
        with self.assertRaises((MaterialError, ValueError)):
            load_bundle(str(self.root))
    def test_declared_only_and_no_git(self):
        (self.root / '.env').write_text('must never read')
        _, manifest, records, _ = load_bundle(str(self.root))
        self.assertEqual([r['origin'] for r in records], ['problem.txt', 'analysis.txt', 'event.txt'])
        self.assertEqual(manifest['task_id'], 'problem-task')
    def test_path_attacks(self):
        for path in ('../event.txt', '/etc/passwd', '.env', 'manifest.json', 'problem.txt', '.git/config'):
            with self.subTest(path=path):
                self.manifest['analysis_file'] = path
                self.bad()
    def test_alias_paths_and_ids(self):
        self.manifest['evidence'].append(copy.deepcopy(self.manifest['evidence'][0]))
        self.bad()
        self.manifest['evidence'][1]['id'] = 'E2'
        self.bad()
    def test_symlink_directory_and_file_and_fifo(self):
        target = self.root / 'event.txt'
        target.unlink()
        target.symlink_to(self.root / 'problem.txt')
        self.bad()
        target.unlink()
        target.mkdir()
        self.bad()
        target.rmdir()
        os.mkfifo(target)
        self.bad()
    def test_missing_bad_utf8_binary_longline_and_size(self):
        target = self.root / 'event.txt'
        target.unlink()
        self.bad()
        for data in (b'\xff', b'\x00', b'x' * 2001, (b'x\n') * (1024 * 1024 + 1)):
            with self.subTest(size=len(data)):
                target.write_bytes(data)
                self.bad()
    def test_stage_limit(self):
        data = ('x' * 1000 + '\n') * 1100
        (self.root / 'problem.txt').write_text(data)
        (self.root / 'analysis.txt').write_text(data)
        self.bad()
    def test_required_schema(self):
        for key in tuple(self.manifest):
            with self.subTest(key=key):
                changed = dict(self.manifest)
                del changed[key]
                save(self.root, changed)
                with self.assertRaises(MaterialError): load_bundle(str(self.root))
        for value in (True, 0, '1'):
            self.manifest['revision'] = value
            self.bad()
    def test_strict_json(self):
        for raw in ('{"id": 1,"id":2}', '{"x":NaN}'):
            with self.assertRaises(MaterialError): strict_json(raw)
    def test_result_schema(self):
        self.assertEqual(validate_result(json.dumps(result()), ['E1'])['findings'][0]['id'], 'F1')
        mutations = []
        v=result(); v['extra']=1; mutations.append(v)
        v=result(); v['findings'][0]['evidence_ids']=['unknown']; mutations.append(v)
        v=result(); v['findings'].append(copy.deepcopy(v['findings'][0])); mutations.append(v)
        v=result(); v['assessment']='proven'; mutations.append(v)
        v=result(); v['findings'][0]['next_check']['command']='rm'; mutations.append(v)
        for value in mutations:
            with self.assertRaises(MaterialError): validate_result(json.dumps(value), ['E1'])
        for raw in ('```json\n{}\n```', '{} trailing', '[]'):
            with self.assertRaises((ValueError, MaterialError)): validate_result(raw, ['E1'])
    def test_empty_evidence_cannot_claim_support(self):
        with self.assertRaises(MaterialError): validate_result(json.dumps(result()), [])
        v=result(); v['assessment']='insufficient_evidence'; v['findings']=[]
        self.assertEqual(validate_result(json.dumps(v), [])['assessment'], 'insufficient_evidence')
