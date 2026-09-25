"""Static checks for the combined All tab (frontend-only wiring)."""
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DASH = REPO_ROOT / "opencode_dashboard.py"


def _source():
    return DASH.read_text(encoding="utf-8")


class TestAllTabWiring(unittest.TestCase):
    def setUp(self):
        self.src = _source()

    def test_tab_and_view_markup(self):
        for token in ['id="tab-all"', 'id="view-all"', 'id="tab-all-cnt"',
                      'id="al-range"', 'id="al-headline"', 'id="al-pills"',
                      'id="al-summary"', 'id="al-days"', 'id="al-sources"']:
            self.assertIn(token, self.src)

    def test_all_tab_is_first_in_strip(self):
        self.assertLess(self.src.index('id="tab-all"'), self.src.index('id="tab-opencode"'))
        self.assertEqual(self.src.count('class="stab'), 6)

    def test_renderers_defined(self):
        self.assertIn("function combinedDays(){", self.src)
        self.assertIn("function renderAllTab(){", self.src)

    def test_render_hooks(self):
        self.assertEqual(self.src.count("renderAllTab();"), 6)
        for token in ['render:d=>{S=d;renderAll();renderAllTab();}}',
                      'render:d=>{R=d;renderRouter();renderAllTab();}}',
                      'render:d=>{JV=d;renderJev();renderAllTab();}}',
                      'render:d=>{AG=d;renderAntigravity();renderAllTab();}}',
                      'render:d=>{CL=d;renderClaude();renderAllTab();}}']:
            self.assertIn(token, self.src)

    def test_tab_switch_and_restore(self):
        for token in ["$('tab-all').onclick=()=>setTab('all');",
                      "saved==='all'",
                      "if(al)renderAllTab();",
                      "$('view-all').hidden=!al;"]:
            self.assertIn(token, self.src)

    def test_export_and_table_columns(self):
        for token in ["TAB==='all'", 'all-usage.json', 'all-usage-days.csv',
                      'Jev judgments', 'AG steps', 'const rows=combinedDays();',
                      'claude_tokens']:
            self.assertIn(token, self.src)


if __name__ == "__main__":
    unittest.main()
