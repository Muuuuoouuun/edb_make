from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


NODE_SETUP = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const Babel = require('./scripts/vendor/babel.min.js');
const source = fs.readFileSync('ui_prototype/app.jsx', 'utf8');
function section(start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from + start.length);
  assert.ok(from >= 0 && to > from, `missing section ${start}`);
  return source.slice(from, to);
}
const finiteNumber = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
// Values built inside the vm context carry its prototypes; compare data only.
const plain = value => JSON.parse(JSON.stringify(value));
function maskWithInk(width, height, inkRects) {
  const dark = new Uint8Array(width * height);
  inkRects.forEach(([x0, y0, x1, y1]) => {
    for (let y = y0; y < y1; y += 1) for (let x = x0; x < x1; x += 1) dark[y * width + x] = 1;
  });
  return { width, height, dark };
}
"""


def run_node(script: str) -> None:
    subprocess.run(["node", "-e", NODE_SETUP + script], cwd=PROJECT_ROOT, check=True)


class TestUiInkTrim(unittest.TestCase):
    def ink_sandbox(self) -> str:
        return r"""
        const sandbox = { finiteNumber, Math, Number, Uint8Array, Map, Promise };
        vm.createContext(sandbox);
        vm.runInContext(section('function adjustOriginalBox(', 'function OriginalNumberInput('), sandbox);
        """

    def test_trim_drops_blank_rows_above_and_below_ink_but_keeps_width(self) -> None:
        run_node(self.ink_sandbox() + r"""
        const page = { width: 100, height: 200 };
        const mask = maskWithInk(100, 200, [[10, 50, 80, 60], [90, 150, 96, 156]]);
        const box = { left: 0, top: 20, width: 80, height: 120 };
        const trimmed = sandbox.inkTrimmedBoxRows(mask, box, page);
        assert.deepEqual(plain(trimmed), { left: 0, top: 44, width: 80, height: 22 },
          'six pixels stay around the ink and ink outside the box columns is ignored');
        assert.equal(sandbox.inkTrimmedBoxRows(mask, trimmed, page), trimmed, 'a tight box is left alone');
        const empty = { left: 0, top: 70, width: 80, height: 40 };
        assert.equal(sandbox.inkTrimmedBoxRows(mask, empty, page), empty, 'a box without ink is left alone');
        const tinted = maskWithInk(100, 200, [[0, 0, 100, 200]]);
        assert.equal(sandbox.inkTrimmedBoxRows(tinted, box, page), box, 'a tinted scan has no blank rows to drop');
        const tall = maskWithInk(100, 200, [[10, 50, 80, 150]]);
        const almost = { left: 0, top: 42, width: 80, height: 116 };
        assert.equal(sandbox.inkTrimmedBoxRows(tall, almost, page), almost, 'trims of 4% or less are skipped');
        assert.deepEqual(plain(sandbox.inkTrimmedBoxRows(tall, { left: 0, top: 30, width: 80, height: 140 }, page)),
          { left: 0, top: 44, width: 80, height: 112 });
        """)

    def test_trim_maps_a_downscaled_mask_back_to_page_pixels(self) -> None:
        run_node(self.ink_sandbox() + r"""
        const page = { width: 200, height: 400 };
        const mask = maskWithInk(100, 200, [[10, 50, 80, 60]]);
        const trimmed = sandbox.inkTrimmedBoxRows(mask, { left: 0, top: 20, width: 200, height: 260 }, page);
        assert.deepEqual(plain(trimmed), { left: 0, top: 94, width: 200, height: 32 });
        """)

    def test_mask_uses_the_server_luminance_threshold(self) -> None:
        run_node(self.ink_sandbox() + r"""
        const data = new Uint8ClampedArray([231, 231, 231, 255, 232, 232, 232, 255, 0, 0, 0, 255, 255, 255, 255, 255]);
        const mask = sandbox.inkMaskFromImageData(data, 4, 1);
        assert.deepEqual(plain(Array.from(mask.dark)), [1, 0, 1, 0]);
        """)

    def test_page_mask_loads_once_and_failed_loads_are_retried(self) -> None:
        run_node(self.ink_sandbox() + r"""
        let loads = 0, fail = false;
        sandbox.filePreviewUrl = (url, max) => `${url}&previewMax=${max}`;
        sandbox.Image = class {
          set src(value) {
            loads += 1;
            this.naturalWidth = 4; this.naturalHeight = 2;
            setTimeout(() => fail ? this.onerror() : this.onload(), 0);
          }
        };
        const pixels = new Uint8ClampedArray(4 * 2 * 4).fill(255);
        pixels.set([0, 0, 0, 255], 4 * 5);
        sandbox.document = { createElement: () => ({ getContext: () => ({
          fillRect() {}, drawImage() {}, getImageData: () => ({ data: pixels }),
        }) }) };
        (async () => {
          const page = { sourceImageUri: '/api/file?path=p1', width: 4, height: 2 };
          const [first, second] = await Promise.all([sandbox.loadPageInkMask(page), sandbox.loadPageInkMask(page)]);
          assert.equal(loads, 1, 'concurrent requests share one image load');
          assert.equal(first, second);
          assert.deepEqual(plain(Array.from(first.dark)), [0, 0, 0, 0, 0, 1, 0, 0]);
          fail = true;
          const other = { sourceImageUri: '/api/file?path=p2', width: 4, height: 2 };
          assert.equal(await sandbox.fitBoxRowsToInk(other, { left: 0, top: 0, width: 4, height: 2 }), null);
          fail = false;
          assert.ok(await sandbox.loadPageInkMask(other), 'a failed load is not cached');
          assert.equal(await sandbox.loadPageInkMask({ width: 4, height: 2 }), null, 'pages without an image are skipped');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_fit_to_ink_button_applies_the_trimmed_box_or_reports_why_not(self) -> None:
        run_node(r"""
        const hooks = { slots: [], index: 0 };
        const sandbox = {
          React: { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) },
          useState: initial => {
            const index = hooks.index++;
            if (!(index in hooks.slots)) hooks.slots[index] = initial;
            return [hooks.slots[index], update => { hooks.slots[index] = typeof update === 'function' ? update(hooks.slots[index]) : update; }];
          },
          useRef: initial => { const index = hooks.index++; if (!(index in hooks.slots)) hooks.slots[index] = { current: initial }; return hooks.slots[index]; },
          useEffect() {},
          finiteNumber,
        };
        vm.createContext(sandbox);
        vm.runInContext(Babel.transform(section('function adjustOriginalBox(', 'function OriginalCornerLoupe('), { presets: ['react'] }).code, sandbox);
        const walk = value => !value ? [] : Array.isArray(value) ? value.flatMap(walk)
          : typeof value === 'object' ? [value, ...walk(value.props?.children)] : [];
        const text = value => !value ? '' : Array.isArray(value) ? value.map(text).join('')
          : typeof value === 'object' ? text(value.props?.children) : String(value);
        const page = { width: 100, height: 200, sourceImageUri: '/p.png' };
        const box = { left: 0, top: 20, width: 80, height: 120 };
        const fitted = { left: 0, top: 44, width: 80, height: 22 };
        const changes = [];
        let result = fitted;
        sandbox.fitBoxRowsToInk = async () => result;
        const render = () => { hooks.index = 0; return sandbox.OriginalGeometryControls({ page, box, originalBox: box, onChange: next => changes.push(next) }); };
        const button = tree => walk(tree).find(node => node.props?.className === 'btn original-fit-ink');
        (async () => {
          let tree = render();
          assert.deepEqual(plain(walk(tree).filter(node => node.type === 'button' && node.props.className === 'btn').map(text)),
            ['원래 영역', '살짝', '넉넉히', '주변 포함']);
          assert.equal(text(button(tree)), '글자에 맞춤');
          await button(tree).props.onClick();
          await new Promise(resolve => setTimeout(resolve, 0));
          assert.deepEqual(plain(changes), [fitted]);
          result = box;
          tree = render();
          await button(tree).props.onClick();
          await new Promise(resolve => setTimeout(resolve, 0));
          tree = render();
          assert.ok(text(tree).includes('이미 글자에 맞춰져 있어요'));
          result = null;
          await button(tree).props.onClick();
          await new Promise(resolve => setTimeout(resolve, 0));
          tree = render();
          assert.ok(text(tree).includes('원본 이미지를 읽지 못해'));
          assert.equal(changes.length, 1);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


class TestUiClipboardPaste(unittest.TestCase):
    def test_clipboard_files_keep_real_names_and_rename_generic_screenshots(self) -> None:
        run_node(r"""
        const sandbox = { File, Array, String, Date };
        vm.createContext(sandbox);
        vm.runInContext(section('const UPLOAD_FILE_EXTENSION_PATTERN', 'function appendUniquePendingFiles('), sandbox);
        const now = new Date(2026, 9, 19, 9, 30, 5);
        const shot = new File(['png'], 'image.png', { type: 'image/png', lastModified: 11 });
        const pdf = new File(['pdf'], '중간고사.pdf', { type: 'application/pdf', lastModified: 12 });
        const text = new File(['txt'], 'notes.txt', { type: 'text/plain' });
        const item = file => ({ kind: 'file', getAsFile: () => file });
        let result = sandbox.clipboardUploadFiles({ items: [item(shot), item(pdf), item(text), { kind: 'string' }] }, now);
        assert.deepEqual(plain(result.files.map(file => file.name)), ['붙여넣은 이미지 20261019-093005.png', '중간고사.pdf']);
        assert.equal(result.files[0].type, 'image/png');
        assert.equal(result.files[0].lastModified, 11);
        assert.equal(result.files[1], pdf, 'a copied file keeps its own name');
        assert.equal(result.rejected, 1);
        const second = new File(['jpg'], 'image.jpg', { type: 'image/jpeg' });
        result = sandbox.clipboardUploadFiles({ items: [], files: [shot, second] }, now);
        assert.deepEqual(plain(result.files.map(file => file.name)),
          ['붙여넣은 이미지 20261019-093005.png', '붙여넣은 이미지 20261019-093005-2.jpg'], 'falls back to clipboard files');
        const unnamed = new File(['webp'], 'clipboard', { type: 'image/webp' });
        assert.equal(sandbox.clipboardUploadFiles({ files: [unnamed] }, now).files[0].name, '붙여넣은 이미지 20261019-093005.webp');
        assert.deepEqual(plain(sandbox.clipboardUploadFiles({ items: [{ kind: 'string' }] }, now)), { files: [], rejected: 0 });
        assert.deepEqual(plain(sandbox.clipboardUploadFiles(null, now)), { files: [], rejected: 0 });
        """)

    def test_paste_adds_files_outside_text_fields_and_dialogs(self) -> None:
        run_node(r"""
        const listeners = new Map();
        const toasts = [];
        const added = [];
        let dialogOpen = false;
        const sandbox = {
          File, Array, String, Date,
          useEffect: effect => { effect(); },
          isEditableKeyboardTarget: target => ['input', 'textarea'].includes(String(target?.tagName || '').toLowerCase()),
          document: { activeElement: null, querySelector: () => dialogOpen ? {} : null },
          window: { addEventListener: (type, fn) => listeners.set(type, fn), removeEventListener() {} },
          showToast: message => toasts.push(message),
          handleFilesRef: { current: files => { added.push(...files); return files.length; } },
        };
        vm.createContext(sandbox);
        vm.runInContext(section('const UPLOAD_FILE_EXTENSION_PATTERN', 'function appendUniquePendingFiles(')
          + section('  // Ctrl/Cmd+V adds a copied screenshot', '\n  useEffect(() => {\n    if (recognitionReview?.kind'), sandbox);
        const paste = (files, target = { tagName: 'DIV' }) => {
          const event = { target, defaultPrevented: false, prevented: false,
            clipboardData: { items: files.map(file => ({ kind: 'file', getAsFile: () => file })) },
            preventDefault() { this.prevented = true; } };
          listeners.get('paste')(event);
          return event;
        };
        const shot = new File(['png'], 'image.png', { type: 'image/png' });
        let event = paste([shot]);
        assert.equal(event.prevented, true);
        assert.equal(added.length, 1);
        assert.match(added[0].name, /^붙여넣은 이미지 \d{8}-\d{6}\.png$/);
        assert.deepEqual(toasts, ['붙여넣은 파일을 추가했어요']);
        event = paste([shot], { tagName: 'INPUT' });
        assert.equal(event.prevented, false, 'text fields keep their own paste');
        dialogOpen = true;
        event = paste([shot]);
        assert.equal(event.prevented, false, 'an open dialog keeps the paste');
        dialogOpen = false;
        event = paste([new File(['x'], 'notes.txt', { type: 'text/plain' })]);
        assert.equal(event.prevented, true);
        assert.equal(toasts.at(-1), '이미지, PDF, HWP 파일만 붙여넣을 수 있어요');
        event = paste([]);
        assert.equal(event.prevented, false, 'plain text paste is ignored');
        assert.equal(added.length, 1);
        """)


if __name__ == "__main__":
    unittest.main()
