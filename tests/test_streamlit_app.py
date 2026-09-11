"""User-visible regression checks; run with the declared app dependencies."""

import json
from pathlib import Path
import unittest

try:
    from streamlit.testing.v1 import AppTest
except ImportError:
    AppTest = None


def control(app, kind, label):
    return next(item for item in getattr(app, kind) if item.label == label)


@unittest.skipIf(AppTest is None, "Install requirements.txt to run UI checks")
class StreamlitRegressionTests(unittest.TestCase):
    def setUp(self):
        self.app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py")).run(timeout=30)

    def run_app(self):
        self.app.run(timeout=30)
        self.assertFalse(self.app.exception)
        self.assertFalse(self.app.error)

    def test_clean_layout_keeps_source_chart_checks_trace_and_exports(self):
        app = self.app
        self.assertFalse(app.exception)
        self.assertFalse(app.warning)
        self.assertFalse(app.metric)
        self.assertEqual(app.get("link_button")[0].proto.url, "https://github.com/BowenZeng/in-plane-masonry-walls")
        self.assertNotIn("Shear branch details", [h.value for h in app.subheader])
        self.assertNotIn("What changes between editions", [e.label for e in app.expander])
        self.assertEqual(len(app.get("vega_lite_chart")), 1)
        self.assertEqual(len(app.get("download_button")), 3)
        self.assertIn("Technical calculation trace", [e.label for e in app.expander])

    def test_uplift_is_visible_inside_chart_domain(self):
        control(self.app, "number_input", "Axial load Pf (kN; compression +)").set_value(-1000.0)
        self.run_app()
        chart = json.loads(self.app.get("vega_lite_chart")[0].proto.spec)
        domain = chart["layer"][0]["encoding"]["y"]["scale"]["domain"]
        self.assertLess(domain[0], -1000.0)
        self.assertGreater(domain[1], 0.0)

    def test_switching_horizontal_steel_to_none_clears_engine_credit(self):
        control(self.app, "selectbox", "Horizontal reinforcement type").select("Bond beam bars")
        self.run_app()
        control(self.app, "number_input", "Area Av at each level (mm2)").set_value(200.0)
        self.run_app()
        control(self.app, "selectbox", "Horizontal reinforcement type").select("None")
        self.run_app()
        trace = self.app.dataframe[4].value.set_index("Variable")
        self.assertEqual(float(trace.loc["Vs", "Value"]), 0.0)

    def test_all_supported_wall_type_transitions_render(self):
        for wall_type in ("Unreinforced masonry", "Partially grouted reinforced masonry", "Fully grouted reinforced masonry"):
            control(self.app, "selectbox", "Wall type").select(wall_type)
            self.run_app()
            self.assertEqual(len(self.app.dataframe), 6)

    def test_oop_values_remain_specific_to_their_edition(self):
        control(self.app, "checkbox", "Linked OOP wall result supplied").set_value(True)
        self.run_app()
        control(self.app, "number_input", "S304-14 OOP utilization").set_value(0.10)
        control(self.app, "number_input", "S304:24 OOP utilization").set_value(0.30)
        self.run_app()
        frame = self.app.dataframe[0].value.set_index("Quantity")
        for edition, expected in (("S304-14", 0.10), ("S304:24", 0.30)):
            combined = frame.loc["Combined in-plane plus OOP utilization", edition]
            pm = frame.loc["P-M utilization", edition]
            self.assertAlmostEqual(combined - pm, expected)

    def test_axial_overload_with_oop_input_does_not_crash(self):
        control(self.app, "checkbox", "Linked OOP wall result supplied").set_value(True)
        control(self.app, "number_input", "Axial load Pf (kN; compression +)").set_value(10000.0)
        self.run_app()
        for index in (1, 2):
            checks = self.app.dataframe[index].value.set_index("Check")
            self.assertEqual(checks.loc["Maximum compression resistance", "Status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
