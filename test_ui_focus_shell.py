"""Layout contracts for the opt-in desktop focus editor shell."""

import re
import unittest
from pathlib import Path


FOCUS_CSS = Path(__file__).parent / "ui_prototype" / "editor-focus.css"


class FocusShellStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = FOCUS_CSS.read_text(encoding="utf-8")

    def test_focus_rules_do_not_restyle_the_home_or_basic_editor(self):
        css = re.sub(r"/\*.*?\*/", "", self.css, flags=re.S)
        for selector, _ in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            if selector.lstrip().startswith("@"):
                continue
            self.assertIn(".editor-focus-mode", selector)
        self.assertNotRegex(css, r"(?:^|\})\s*:root\s*\{")

    def test_inspector_gutter_is_reserved_outside_pointer_surface(self):
        self.assertIn("--focus-panel-gutter: calc(var(--focus-panel-width) + 32px)", self.css)
        self.assertIn(
            "padding: 12px calc(var(--focus-panel-gutter) + 20px) 20px 20px",
            self.css,
        )
        self.assertIn("width: var(--focus-panel-width)", self.css)
        self.assertIn("top: 76px", self.css, "inspector starts below the single toolbar")
        self.assertIn("view-transition-name: none", self.css, "toolbar overlays must escape the center stacking context")
        self.assertNotIn("--focus-dock-space", self.css)

    def test_collapsing_the_inspector_reclaims_width_without_destroying_panel_state(self):
        self.assertRegex(
            self.css,
            r"focus-panel-collapsed:not\(\.home-mode\)\s*\{\s*--focus-panel-gutter:\s*0px",
        )
        self.assertRegex(
            self.css,
            r"focus-panel-collapsed\s*>\s*\.col\.right\s*\{[^}]*visibility:\s*hidden;[^}]*pointer-events:\s*none",
        )

    def test_document_geometry_is_not_overridden_by_the_chrome_styles(self):
        self.assertNotRegex(self.css, r"(?:\.stage-content|\.stage-tile|\.page-band|\.page-head)\b")
        self.assertNotRegex(self.css, r"\b(?:transform|zoom):")
        self.assertIn("min-height: 0", self.css)

    def test_keyboard_focus_and_reduced_motion_are_explicit(self):
        self.assertIn(":focus-visible", self.css)
        self.assertIn("outline: 2px solid var(--accent-ink)", self.css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.css)

    def test_narrow_panes_release_the_inspector_gutter_and_absolute_position(self):
        narrow = self.css.split('@media (max-width: 900px)', 1)[1]
        self.assertIn('--focus-panel-gutter: 0px', narrow)
        panel = narrow.split('.main.editor-focus-mode > .col.right', 1)[1].split('}', 1)[0]
        self.assertIn('position: static', panel)
        self.assertIn('inset: auto', panel)
        self.assertIn('width: auto', panel)
        active = narrow.split('focus-panel-collapsed[data-mobile-pane="panel"]', 1)[1].split('}', 1)[0]
        self.assertIn('visibility: visible', active)
        self.assertIn('pointer-events: auto', active)

    def test_controls_use_existing_tokens_and_laptop_breakpoints(self):
        sizes = re.findall(r"font-size:\s*([^;]+)", self.css)
        self.assertTrue(sizes)
        self.assertTrue(all(value.startswith("var(--text-") for value in sizes))
        self.assertIn("@media (max-width: 1200px)", self.css)
        self.assertNotRegex(self.css, r"@media[^{}]*max-width:\s*(?:[1-8]\d\d|\d\d)px")
        self.assertIn("background: var(--surface)", self.css)


if __name__ == "__main__":
    unittest.main()
