from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


NODE_SETUP = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('ui_prototype/app.jsx', 'utf8');
const start = source.indexOf('  const sourcePageHeading =');
const end = source.indexOf('  const problemsById =', start);
assert.ok(start >= 0 && end > start, 'source page heading formatter is missing');
const sandbox = { session: null };
vm.createContext(sandbox);
vm.runInContext(source.slice(start, end) + '\nglobalThis.sourcePageHeading = sourcePageHeading;', sandbox);
"""


def run_node(script: str) -> None:
    subprocess.run(["node", "-e", script], cwd=PROJECT_ROOT, check=True)


class TestUiSourcePageHeading(unittest.TestCase):
    def test_batch_pages_map_to_only_the_input_file_with_the_same_upload_digest(self) -> None:
        run_node(NODE_SETUP + r"""
        for (const size of [40, 64]) {
          const first = 'a'.repeat(size), second = 'b'.repeat(size);
          sandbox.session = { input_files: [
            '/tmp/' + first + '_국어_012345abcd.pdf',
            '/tmp/' + second + '_수학 자료_abcdef1234.pdf',
          ] };
          assert.equal(sandbox.sourcePageHeading({ sourceImagePath: '/tmp/' + second + '_page_002.png' }, 1),
            '수학 자료.pdf · 원본 2쪽', 'a batch page must match its own uploaded file');
          assert.equal(sandbox.sourcePageHeading({ sourceImageUri: '/api/file?path='
            + encodeURIComponent('/tmp/' + first + '_page_001.png') }, 0), '국어.pdf · 원본 1쪽');
          assert.equal(sandbox.sourcePageHeading({ source_image_uri: '/api/file?path='
            + encodeURIComponent('/tmp/' + second.toUpperCase() + '_page_002.png') }, 1), '수학 자료.pdf · 원본 2쪽');
          assert.equal(sandbox.sourcePageHeading({ sourceFileName: '직접 지정.hwp',
            sourceImagePath: '/tmp/' + second + '_page_003.png' }, 2), '직접 지정.hwp · 원본 3쪽');
        }
        """)

    def test_batch_pages_do_not_guess_a_name_when_the_image_digest_is_missing_or_ambiguous(self) -> None:
        run_node(NODE_SETUP + r"""
        const digest = 'c'.repeat(40);
        sandbox.session = { input_files: ['/tmp/' + digest + '_국어.pdf', '/tmp/' + digest + '_수학.pdf'] };
        assert.equal(sandbox.sourcePageHeading({ sourceImagePath: '/tmp/' + digest + '_page_003.png' }, 2), '원본 3쪽');
        sandbox.session = { input_files: ['/tmp/' + digest + '_국어.pdf', '/tmp/' + 'd'.repeat(40) + '_수학.pdf'] };
        assert.equal(sandbox.sourcePageHeading({ sourceImagePath: '/tmp/page_003.png' }, 2), '원본 3쪽');
        assert.equal(sandbox.sourcePageHeading({ sourceImagePath: '/tmp/' + 'e'.repeat(40) + '_page_003.png' }, 2), '원본 3쪽');
        assert.equal(sandbox.sourcePageHeading({ sourceImagePath: '/tmp/' + digest + '_page_003.png',
          sourceImageUri: '/tmp/' + 'd'.repeat(40) + '_page_003.png' }, 2), '원본 3쪽',
          'conflicting path and URI digests must not choose either input');
        """)

    def test_only_uploaded_names_lose_the_generated_safe_filename_suffix(self) -> None:
        run_node(NODE_SETUP + r"""
        for (const size of [40, 64]) {
          const prefix = 'a'.repeat(size) + '_';
          assert.equal(sandbox.sourcePageHeading({ sourceFileName: prefix + '수학 자료_012345abcd.pdf' }, 0),
            '수학 자료.pdf · 원본 1쪽');
          assert.equal(sandbox.sourcePageHeading({ sourceFileName: prefix + '교재_012345abcd_abcdef1234.pdf' }, 0),
            '교재_012345abcd.pdf · 원본 1쪽', 'remove exactly one generated suffix');
          assert.equal(sandbox.sourcePageHeading({ sourceFileName: prefix + '시험_123456789.pdf' }, 0),
            '시험_123456789.pdf · 원본 1쪽', 'a different suffix length is part of the source name');
        }
        assert.equal(sandbox.sourcePageHeading({ sourceFileName: '교재_012345abcd.pdf' }, 0),
          '교재_012345abcd.pdf · 원본 1쪽', 'unprefixed user filenames stay intact');
        """)

    def test_source_names_are_readable_across_upload_and_path_formats(self) -> None:
        run_node(NODE_SETUP + r"""
        const digest = 'a'.repeat(64);
        assert.equal(sandbox.sourcePageHeading({ sourceFileName: digest + '_수학.pdf', pageNumber: 2 }, 0), '수학.pdf · 원본 2쪽');
        assert.equal(sandbox.sourcePageHeading({ source_file_name: 'file:///tmp/' + 'b'.repeat(40) + '_' + encodeURIComponent('국어 자료.pdf') }, 2), '국어 자료.pdf · 원본 3쪽');
        assert.equal(sandbox.sourcePageHeading({ metadata: { source_hwp_path: 'C:\\uploads\\시험.hwp', original_source_path: '/tmp/fallback.pdf' } }, 0), '시험.hwp · 원본 1쪽');
        assert.equal(sandbox.sourcePageHeading({ metadata: { original_source_path: '/tmp/원본.pdf', source_pdf_path: '/tmp/fallback.pdf' } }, 0), '원본.pdf · 원본 1쪽');
        assert.equal(sandbox.sourcePageHeading({ sourceFileName: '100%자료.pdf' }, 0), '100%자료.pdf · 원본 1쪽', 'malformed percent escapes must not break review rendering');
        """)

    def test_original_page_number_wins_over_rendered_subset_number(self) -> None:
        run_node(NODE_SETUP + r"""
        assert.equal(sandbox.sourcePageHeading({ metadata: { original_page_index: 17 }, sourcePageNumber: 9, pageNumber: 1 }, 0), '원본 17쪽');
        assert.equal(sandbox.sourcePageHeading({ sourcePageNumber: 9, pageNumber: 1 }, 0), '원본 9쪽');
        assert.equal(sandbox.sourcePageHeading({ source_page_number: 6, page_number: 1 }, 0), '원본 6쪽');
        assert.equal(sandbox.sourcePageHeading({ metadata: { original_page_index: 0 }, sourcePageNumber: -1, pageNumber: '4' }, 0), '원본 4쪽');
        assert.equal(sandbox.sourcePageHeading({ pageNumber: 'invalid' }, 2), '원본 3쪽');
        """)

    def test_single_input_fallback_does_not_mislabel_batch_or_digest_only_pages(self) -> None:
        run_node(NODE_SETUP + r"""
        sandbox.session = { input_files: ['/tmp/' + 'f'.repeat(40) + '_과학.pdf'] };
        assert.equal(sandbox.sourcePageHeading({}, 0), '과학.pdf · 원본 1쪽');
        sandbox.session = { input_files: ['/tmp/과학.pdf', '/tmp/국어.pdf'] };
        assert.equal(sandbox.sourcePageHeading({}, 1), '원본 2쪽', 'a batch page must not guess the first input');
        assert.equal(sandbox.sourcePageHeading({ sourceFileName: 'e'.repeat(64) + '_page_003.png' }, 2), '원본 3쪽');
        sandbox.session = null;
        assert.equal(sandbox.sourcePageHeading({ sourceFileName: 'd'.repeat(40) + '.pdf' }, 0), '원본 1쪽');
        """)


if __name__ == '__main__':
    unittest.main()
