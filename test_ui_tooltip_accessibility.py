"""Exercise tooltip transitions that must preserve control names on focus."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TestTooltipAccessibility(unittest.TestCase):
    def test_focused_icon_names_and_newer_react_attributes_survive_tooltips(self):
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('ui_prototype/app.jsx', 'utf8');
const component = source.split('function TooltipLayer(){')[1].split('\n  useEffect(() => {')[0];
const sandbox = {
  useState: () => [null, () => {}],
  useRef: () => ({ current: null }),
  useCallback: fn => fn,
  window: { innerWidth: 1440, innerHeight: 900 },
};
vm.runInNewContext(
  `(function(){${component}; this.tooltip = { showTooltipFor, hideTooltip }; }).call(this)`,
  sandbox,
);
const { showTooltipFor, hideTooltip } = sandbox.tooltip;
function control(attrs = {}, text = '', imageName = false) {
  const attributes = new Map(Object.entries({ title: '대기열에서 제거', ...attrs }));
  return {
    dataset: {}, textContent: text,
    getAttribute: key => attributes.get(key) ?? null,
    hasAttribute: key => attributes.has(key),
    setAttribute: (key, value) => attributes.set(key, value),
    removeAttribute: key => attributes.delete(key),
    matches: () => true,
    querySelector: () => imageName ? {} : null,
    closest: () => null,
    getBoundingClientRect: () => ({ left: 20, right: 52, top: 80, bottom: 112, width: 32 }),
  };
}

// Keyboard focus shows the custom tooltip without dropping the button's name.
const icon = control();
showTooltipFor(icon);
assert.equal(icon.getAttribute('title'), null);
assert.equal(icon.getAttribute('aria-label'), '대기열에서 제거');
showTooltipFor(icon);
assert.equal(icon.getAttribute('aria-label'), '대기열에서 제거');
hideTooltip();
assert.equal(icon.getAttribute('title'), '대기열에서 제거');
assert.equal(icon.getAttribute('aria-label'), null);
assert.deepEqual(icon.dataset, {});

// Explicit labels and visible text remain the control's authoritative name.
for (const target of [
  control({ 'aria-label': '삭제' }),
  control({ 'aria-labelledby': 'remove-label' }),
  control({}, '삭제'),
  control({}, '', true),
]) {
  const originalLabel = target.getAttribute('aria-label');
  showTooltipFor(target);
  assert.equal(target.getAttribute('aria-label'), originalLabel);
  hideTooltip();
  assert.equal(target.getAttribute('aria-label'), originalLabel);
}

// Moving focus cleans up the previous button, and later React changes win.
showTooltipFor(icon);
const next = control({ title: '파일 추가' });
showTooltipFor(next);
assert.equal(icon.getAttribute('title'), '대기열에서 제거');
assert.equal(icon.getAttribute('aria-label'), null);
next.setAttribute('title', '새 파일 추가');
next.setAttribute('aria-label', '새 파일 선택');
hideTooltip();
assert.equal(next.getAttribute('title'), '새 파일 추가');
assert.equal(next.getAttribute('aria-label'), '새 파일 선택');
assert.deepEqual(next.dataset, {});
'''
        subprocess.run(["node", "-e", script], cwd=PROJECT_ROOT, check=True)


if __name__ == "__main__":
    unittest.main()
