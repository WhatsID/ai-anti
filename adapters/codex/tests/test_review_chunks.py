"""Block provenance, complete-read and stage-isolation contracts."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import test_thin_review as thin

sys.path.insert(0, str(thin.ENTRY.parent))
from review_chunks import MAX_BLOCK_BYTES, MAX_BLOCK_LINES, chunk_stage, physical_lines
from review_material import MaterialError, read_text, sha


class BlockUnitTests(unittest.TestCase):
    def test_lossless_spans_boundaries_and_names(self):
        fixtures = [b'', b'x', b'x\n', b'a\r\nb\r\n', b'\n' * 121,
                    ('中文\r\n' * 300 + '尾无换行').encode(),
                    (('x' * 1999 + '\n') * 40).encode(), b'x\x0by\x0cz\n' * 125]
        for data in fixtures:
            with self.subTest(size=len(data)), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                source = root / 'repo/中文 with space.txt'
                source.parent.mkdir()
                source.write_bytes(data)
                mapping = chunk_stage(root)['repo/中文 with space.txt']
                self.assertEqual(mapping['sha256'], sha(data))
                restored, expected_start = [], 1
                original_lines = physical_lines(data)
                for block in mapping['blocks']:
                    content = (root / block['path']).read_bytes()
                    self.assertLessEqual(len(content), MAX_BLOCK_BYTES)
                    self.assertLessEqual(len(physical_lines(content)), MAX_BLOCK_LINES)
                    self.assertEqual(block['sha256'], sha(content))
                    self.assertEqual(block['bytes'], len(content))
                    self.assertEqual(block['start_line'], expected_start)
                    self.assertEqual(content, b''.join(original_lines[expected_start - 1:block['end_line']]))
                    expected_start = block['end_line'] + 1
                    restored.append(content)
                self.assertEqual(b''.join(restored), data)
                self.assertEqual(source.exists(), len(mapping['blocks']) == 1)

    def test_lf_line_validation_does_not_hide_long_unicode_separators(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / 'source'
            path.write_text(('x' * 1000 + '\u2028') * 3)
            with self.assertRaisesRegex(MaterialError, 'line exceeds'):
                read_text(path, 'source')


class ChunkContracts(unittest.TestCase):
    # Reuse the black-box fixture without re-running its inherited test methods.
    setUp = thin.ThinReviewTests.setUp
    tearDown = thin.ThinReviewTests.tearDown
    git = thin.ThinReviewTests.git
    write = thin.ThinReviewTests.write
    run_review = thin.ThinReviewTests.run_review
    calls = thin.ThinReviewTests.calls
    manifest = thin.ThinReviewTests.manifest
    assert_success = thin.ThinReviewTests.assert_success

    def restore(self, out, stage, name):
        mapping = self.manifest(out)['sources'][stage][name]
        return b''.join((out / 'materials' / stage / block['path']).read_bytes()
                        for block in mapping['blocks'])

    def test_full_css_over_old_limit_and_large_path_inventory(self):
        css = '.must-retain-selector {\n' + '  --value: 12345678901234567890;\n' * 9000 + '}\n'
        self.write('样式 full.css', css)
        names = []
        for index in range(3716):
            names.append('paths/' + str(index).zfill(5) + '_' + 'a' * 40 + '.txt')
        empty = self.git('hash-object', '-w', '--stdin').strip()
        import subprocess
        subprocess.run(['git', '-C', str(self.repo), 'update-index', '--index-info'],
                       input=''.join('100644 ' + empty + '\t' + name + '\n' for name in names),
                       text=True, check=True, capture_output=True)
        self.git('commit', '-qm', 'large tracked inventory')
        # Sparse physical fixture: missing path bodies intentionally appear in status.
        r, out = self.run_review(files=['样式 full.css'], extra=['--max-output-bytes', '8388608'])
        self.assert_success(r, out)
        self.assertGreater(len(css.encode()), 256 * 1024)
        self.assertEqual(self.restore(out, 'review', 'repo/样式 full.css'), css.encode())
        paths = self.restore(out, 'review', 'tracked-paths.txt').decode()
        self.assertIn(names[1947], paths)
        self.assertIn(names[3632], paths)
        self.assertGreater(len(paths.encode()), 200000)
        sources = self.manifest(out)['sources']['review']
        expected = {b['path'] for s in sources.values() for b in s['blocks']}
        self.assertEqual(set(self.calls()[0]['files']), expected)
        self.assertFalse((out / 'materials/review/tracked-paths.txt').exists())

    def test_missing_middle_block_reports_source_and_fails(self):
        self.write('chunk source.py', 'x = 1\n' * 360)
        r, out = self.run_review(files=['chunk source.py'], fake='missing_chunk')
        self.assertNotEqual(r.returncode, 0)
        status = json.loads((out / 'status.json').read_text())
        self.assertEqual(status['failure_kind'], 'incomplete_reads')
        self.assertIn('repo/chunk source.py (lines 121-240;', status['error'])
        self.assertFalse((out / 'report.md').exists())

    def test_tampered_block_snapshot_and_forged_evidence_fail(self):
        self.write('chunk.py', 'x = 1\n' * 360)
        for fake, message in [('tamper_chunk', 'snapshot changed'),
                              ('bad_chunk_evidence', 'invalid Read evidence'),
                              ('tool_error', 'tool error')]:
            with self.subTest(fake=fake):
                r, out = self.run_review(files=['chunk.py'], fake=fake)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(message, json.loads((out / 'status.json').read_text())['error'])
                self.assertFalse((out / 'report.md').exists())

    def test_chunked_plan_request_inventory_isolated_and_report_original(self):
        self.plan.write_bytes(b'PLAN_ONLY_SECRET\r\n' * 300 + b'plan-tail')
        self.request.write_bytes(b'REQUEST_ONLY\r\n' * 300 + b'request-tail')
        r, out = self.run_review('plan', fake='large_inventory')
        self.assert_success(r, out)
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertNotIn('PLAN_ONLY_SECRET', ''.join(calls[0]['files'].values()))
        inventory = ('INVENTORY 原文保持\r\n' * 500 + 'inventory-tail').encode()
        self.assertEqual(self.restore(out, 'review', 'inventory.md'), inventory)
        self.assertEqual(self.restore(out, 'review', 'plan.md'), self.plan.read_bytes())
        self.assertEqual(self.restore(out, 'inventory', 'request.md'), self.request.read_bytes())
        second_prompt = calls[1]['argv'][calls[1]['argv'].index('-p') + 1]
        self.assertNotIn('INVENTORY 原文保持', second_prompt)
        self.assertIn(inventory, (out / 'report.md').read_bytes())
        first_stage = out / 'materials/inventory'
        self.assertFalse(any(b'PLAN_ONLY_SECRET' in p.read_bytes() for p in first_stage.rglob('*') if p.is_file()))

    def test_large_patch_and_deleted_source_are_all_required(self):
        self.write('old.txt', 'old-content\n' * 400)
        self.git('add', 'old.txt')
        self.git('commit', '-qm', 'large deleted source')
        (self.repo / 'old.txt').unlink()
        patch = self.git('diff', 'HEAD', '--', 'old.txt').encode()
        r, out = self.run_review(files=['old.txt'])
        self.assert_success(r, out)
        patch_name = 'diff/' + sha(b'old.txt') + '.patch'
        self.assertEqual(self.restore(out, 'review', patch_name), patch)
        self.assertEqual(self.restore(out, 'review', 'deleted/old.txt'), b'old-content\n' * 400)
        sources = self.manifest(out)['sources']['review']
        for name in (patch_name, 'deleted/old.txt'):
            self.assertGreater(len(sources[name]['blocks']), 1)
            self.assertTrue(all(block['path'] in self.calls()[0]['files'] for block in sources[name]['blocks']))

    def test_oversized_input_fails_before_any_call(self):
        self.write('oversized', 'x\n' * (1024 * 1024 + 1))
        r, out = self.run_review(files=['oversized'])
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.calls())

    def test_aggregate_stage_limit_includes_metadata(self):
        self.write('almostfull', 'x\n' * (1024 * 1024 - 10))
        r, out = self.run_review(files=['almostfull'])
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.calls())

    def test_post_inventory_long_line_and_total_limit_prevent_second_call(self):
        for fake in ('long_inventory', 'overflow_inventory'):
            with self.subTest(fake=fake):
                self.audit.unlink(missing_ok=True)
                if fake == 'overflow_inventory':
                    self.request.write_text(('r' * 1000 + '\n') * 1200)
                r, out = self.run_review('plan', fake=fake, extra=['--max-output-bytes', '8388608'])
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(len(self.calls()), 1)
                self.assertFalse((out / 'report.md').exists())
                error = json.loads((out / 'status.json').read_text())['error']
                self.assertIn('line exceeds' if fake == 'long_inventory' else 'stage snapshot exceeds', error)


if __name__ == '__main__':
    unittest.main()
