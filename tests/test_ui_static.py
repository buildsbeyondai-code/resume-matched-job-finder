"""UI conformance checks that can run without a browser: theme-token contrast (both themes), labels,
landmarks, skip link, target sizes, no innerHTML, no gradients, reflow rules, forced colours."""
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
CSS = (STATIC / "app.css").read_text()
HTML = (STATIC / "index.html").read_text()
JS = (STATIC / "app.js").read_text()


def tokens(marker):
    block = CSS.split(f"/* tokens:{marker} */", 1)[1].split("}", 1)[0]
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", block))


def lum(hexc):
    r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


TEXT_PAIRS = [("text", "bg"), ("text", "surface"), ("muted", "bg"), ("muted", "surface"), ("muted", "surface-2"),
              ("primary", "surface"), ("primary", "bg"), ("on-primary", "primary"), ("header-text", "header-bg"),
              ("nav-text", "nav-bg"), ("danger", "surface"), ("on-danger", "danger"), ("warn-text", "warn-bg"),
              ("text", "error-bg"), ("text", "info-bg"), ("primary", "primary-soft"), ("text", "surface-2"),
              ("primary", "info-bg"), ("muted", "nav-bg"), ("text", "nav-bg")]
UI_PAIRS = [("border-strong", "surface"), ("border-strong", "bg"), ("focus", "bg"), ("focus", "surface"),
            ("primary", "nav-bg"), ("score-high", "surface"), ("score-mid", "surface"), ("score-low", "surface"),
            ("danger", "error-bg")]


class Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class UITests(unittest.TestCase):
    def test_contrast_light_and_dark(self):
        for theme in ("light", "dark"):
            t = tokens(theme)
            for fg, bg in TEXT_PAIRS:
                self.assertGreaterEqual(ratio(t[fg], t[bg]), 4.5, f"{theme}: {fg} on {bg}")
            for fg, bg in UI_PAIRS:
                self.assertGreaterEqual(ratio(t[fg], t[bg]), 3.0, f"{theme}: {fg} vs {bg}")

    def test_themes_visibly_different_including_header_and_nav(self):
        light, dark = tokens("light"), tokens("dark")
        for k in ("bg", "surface", "header-bg", "nav-bg", "text", "primary"):
            self.assertNotEqual(light[k], dark[k], k)
        media_block = CSS.split("@media (prefers-color-scheme: dark)", 1)[1]
        for k, v in dark.items():
            self.assertIn(f"--{k}: {v}", media_block, f"system dark must match manual dark for {k}")

    def test_enterprise_restraint(self):
        css_no_comments = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
        self.assertNotRegex(css_no_comments, r"gradient|backdrop-filter|text-shadow")
        self.assertIn("@media (forced-colors: active)", CSS)
        self.assertIn("prefers-reduced-motion", CSS)
        self.assertRegex(CSS, r":focus-visible\s*\{\s*outline:\s*3px")

    def test_every_control_has_a_label_or_name(self):
        c = Collector(); c.feed(HTML)
        label_for = {a.get("for") for t, a in c.tags if t == "label"}
        for tag, a in c.tags:
            if tag in ("input", "select", "textarea") and a.get("type") != "hidden":
                wrapped = tag == "input" and a.get("type") == "checkbox"  # wrapped in its <label>
                self.assertTrue(a.get("id") in label_for or wrapped or a.get("aria-label"), f"unlabelled {tag} {a}")
            if tag == "button":
                self.assertIn("type", a, f"button without type: {a}")

    def test_landmarks_skip_link_and_live_region(self):
        for needle in ('<a class="skip" href="#main"', "<header", "<nav", '<main id="main"', 'aria-live="polite"',
                       'role="alert"', 'lang="en"', 'name="viewport"'):
            self.assertIn(needle, HTML)

    def test_no_unsafe_dom_and_new_tab_links(self):
        code = re.sub(r"//[^\n]*", "", JS)
        self.assertNotRegex(code, r"innerHTML|outerHTML|insertAdjacentHTML|eval\(|document\.write")
        self.assertIn('target="_blank" rel="noopener noreferrer"', HTML)
        self.assertIn("opens in a new tab", HTML)

    def test_target_size_and_reflow(self):
        self.assertRegex(CSS, r"\.btn \{[^}]*min-height: 36px")
        self.assertRegex(CSS, r"\.linklike \{[^}]*min-height: 24px")
        self.assertIn("@media (max-width: 720px)", CSS)
        self.assertNotRegex(CSS, r"(?<![-\w])(min-)?width:\s*\d{3,}px")  # fixed widths >= 100px break 320px reflow
        self.assertIn("overflow-wrap: anywhere", CSS)

    def test_score_not_colour_alone(self):
        self.assertIn("Strong match", JS)
        self.assertIn("Match score ${j.score} out of 100", JS)

    def test_descriptions_switch_and_theme_remembered(self):
        self.assertIn("rmjf-desc", JS); self.assertIn("rmjf-theme", (STATIC / "theme.js").read_text())
        self.assertIn(".no-desc .desc", CSS)

    def test_one_primary_action_per_view(self):
        for view in re.findall(r'<section id="view-.*?</section>\n', HTML, re.S):
            forms = re.findall(r"<form.*?</form>", view, re.S)
            prim = view.count('class="btn primary"')
            self.assertLessEqual(prim, 1, view[:60])


if __name__ == "__main__":
    unittest.main()
