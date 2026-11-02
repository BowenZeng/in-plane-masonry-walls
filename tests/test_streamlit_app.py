"""Behavior-level Streamlit regressions for the load-case workflow."""
import json
from pathlib import Path
import unittest

try:
    from streamlit.testing.v1 import AppTest
except ImportError:
    AppTest = None


def control(app, kind, label):
    return next(item for item in getattr(app, kind) if item.label == label)


def table(app, *columns):
    return next(item.value for item in app.dataframe if set(columns) <= set(item.value.columns))


def case_row(case_id, p=200.0, m=50.0, v=20.0, lateral=0.0):
    # WP2: each direct case declares its signed lateral axial load (None = undeclared).
    return {"Case ID": case_id, "Name": case_id, "Enabled": True,
            "P (kN)": p, "M (kN-m)": m, "V (kN)": v,
            "Joint V (kN)": None, "Counteracting dead load D_cnt (kN)": None,
            "Signed lateral axial in this case (kN)": lateral,
            "Coupling-beam axial in this case (kN)": None}


@unittest.skipIf(AppTest is None, "Install requirements.txt to run UI checks")
class StreamlitRegressionTests(unittest.TestCase):
    def setUp(self):
        self.app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py")).run(timeout=40)
        self.assertFalse(self.app.exception)

    def run_app(self):
        self.app.run(timeout=40)
        self.assertFalse(self.app.exception)

    def set_cases(self, rows):
        self.app.session_state["direct_rows"] = rows
        self.app.session_state["direct_version"] += 1
        self.run_app()

    def results(self):
        return table(self.app, "IP resistance", "Additional checks")

    def test_default_case_has_existing_demands_and_all_batch_exports(self):
        frame = self.results()
        self.assertEqual(len(frame), 2)
        self.assertEqual(set(frame["Case ID"]), {"CASE-1"})
        self.assertTrue((frame["P (kN)"] == 200).all())
        self.assertTrue((frame["Signed M (kN-m)"] == 1000).all())
        self.assertTrue((frame["Signed V (kN)"] == 200).all())
        self.assertEqual(set(frame["Edition"]), {"S304-14", "S304:24"})
        self.assertEqual(len(self.app.get("vega_lite_chart")), 1)
        labels = {b.label for b in self.app.get("download_button")}
        self.assertEqual(labels, {"All-case results CSV", "Governing cases CSV", "Checks CSV",
                                 "Calculation traces CSV", "P-M points CSV"})
        self.assertEqual(self.app.get("link_button")[0].proto.url,
                         "https://github.com/BowenZeng/in-plane-masonry-walls")

    def test_selection_never_changes_batch_or_governing_and_unselected_failure_counts(self):
        self.set_cases([case_row("SAFE"), case_row("FAIL", m=5000)])
        before = self.results().copy()
        governing = table(self.app, "Governing case IDs", "Utilization").copy()
        self.assertTrue((before[before["Case ID"] == "FAIL"]["IP resistance"] == "FAIL").all())
        self.assertFalse(any(s.value == "All entered IP resistance cases pass." for s in self.app.success))
        control(self.app, "selectbox", "Case to inspect / review").select("FAIL")
        self.run_app()
        self.assertTrue(before.equals(self.results()))
        self.assertTrue(governing.equals(table(self.app, "Governing case IDs", "Utilization")))

    def test_generated_mode_checks_23_cases_and_copy_preserves_totals(self):
        control(self.app, "radio", "Load entry mode").set_value("Generate from unfactored actions")
        self.run_app()
        before = self.results().copy()
        self.assertEqual(len(before), 46)
        control(self.app, "button", "Copy generated cases to direct mode").click()
        self.run_app()
        self.assertEqual(control(self.app, "radio", "Load entry mode").value, "Enter factored cases")
        after = self.results()
        for column in ("Case ID", "Edition", "P (kN)", "Signed M (kN-m)", "Signed V (kN)",
                       "Pd (kN)", "Mr(Pf) (kN-m)", "Vr diagonal (kN)", "Vr sliding (kN)"):
            self.assertEqual(before[column].tolist(), after[column].tolist())
        self.assertTrue(self.app.session_state["copied_origins"])

    def test_generated_factor_and_contribution_edits_are_applied(self):
        control(self.app, "radio", "Load entry mode").set_value("Generate from unfactored actions")
        self.run_app()
        version = self.app.session_state["generated_version"]
        self.app.session_state[f"contribution_editor_{version}"] = {
            "edited_rows": {4: {"P (kN)": 100.0, "M (kN-m)": -50.0, "V (kN)": -10.0}},
            "added_rows": [], "deleted_rows": []}
        control(self.app, "button", "Apply unfactored actions and combinations").click()
        self.run_app()
        frame = self.results().set_index(["Case ID", "Edition"])
        plus, minus = frame.loc[("R14[E+]", "S304:24")], frame.loc[("R14[E-]", "S304:24")]
        self.assertAlmostEqual(plus["P (kN)"] - minus["P (kN)"], 200)
        self.assertAlmostEqual(plus["Signed M (kN-m)"], -50)
        self.assertAlmostEqual(minus["Signed M (kN-m)"], 50)

    def test_actual_direct_editor_submission_retains_new_row_and_stable_id(self):
        version = self.app.session_state["direct_version"]
        self.app.session_state[f"direct_editor_{version}"] = {
            "edited_rows": {0: {"M (kN-m)": -40.0}},
            "added_rows": [case_row("ADDED", p=-100, m=-50, v=-10)], "deleted_rows": []}
        control(self.app, "button", "Apply factored cases").click()
        self.run_app()
        frame = self.results()
        self.assertEqual(len(frame), 4)
        self.assertEqual(set(frame["Case ID"]), {"CASE-1", "ADDED"})
        self.assertTrue((frame[frame["Case ID"] == "CASE-1"]["Signed M (kN-m)"] == -40).all())

    def test_uplift_and_large_unselected_demands_are_in_chart_domain(self):
        self.set_cases([case_row("FIRST"), case_row("UPLIFT", p=-1000, m=-7000)])
        chart = json.loads(self.app.get("vega_lite_chart")[0].proto.spec)
        enc = chart["layer"][0]["encoding"]
        self.assertLess(enc["y"]["scale"]["domain"][0], -1000)
        self.assertGreater(enc["x"]["scale"]["domain"][1], 7000)

    def test_partial_grout_curve_methods_are_explicit_and_all_types_render(self):
        for wall_type in ("Unreinforced masonry", "Partially grouted reinforced masonry", "Fully grouted reinforced masonry"):
            control(self.app, "selectbox", "Wall type").select(wall_type)
            self.run_app()
            self.assertEqual(len(self.results()), 2)
            spec = json.loads(self.app.get("vega_lite_chart")[0].proto.spec)
            self.assertEqual(spec["layer"][0]["encoding"]["strokeDash"]["scale"]["domain"], ["Discrete", "Smeared"])

    def test_oop_values_remain_case_and_edition_specific_then_become_stale(self):
        for edition, value in (("S304-14", .1), ("S304:24", .3)):
            control(self.app, "checkbox", f"{edition} linked OOP result supplied").set_value(True)
            control(self.app, "number_input", f"{edition} OOP utilization").set_value(value)
            control(self.app, "checkbox", f"{edition} OOP stability passes").set_value(True)
        control(self.app, "button", "Save and review case inputs").click()
        self.run_app()
        supplements = self.app.session_state["direct_supplements"]
        self.assertEqual(supplements["CASE-1"].oop["S304-14"].utilization, .1)
        self.assertEqual(supplements["CASE-1"].oop["S304:24"].utilization, .3)
        self.assertTrue((self.results()["Supplement review"] == "Reviewed").all())
        control(self.app, "number_input", "Length lw (mm)").set_value(5100)
        self.run_app()
        self.assertTrue((self.results()["Supplement review"] == "STALE").all())
        self.assertTrue(any("stale" in warning.value for warning in self.app.warning))
        checks = table(self.app, "Check", "Category", "Clause").set_index("Check")
        self.assertEqual(checks.loc["Concurrent minor-axis/OOP check supplied", "Status"], "BLOCKED")

    def test_reordering_and_mode_switching_retain_case_inputs(self):
        self.set_cases([case_row("A"), case_row("B")])
        control(self.app, "checkbox", "Use a sourced case Pd instead of the case composition").set_value(True)
        control(self.app, "number_input", "Signed case Pd (kN)").set_value(99)
        control(self.app, "text_input", "Source of the case Pd").set_value("External analysis")
        control(self.app, "button", "Save and review case inputs").click()
        self.run_app()
        self.set_cases([case_row("B"), case_row("A")])
        frame = self.results()
        self.assertTrue((frame[frame["Case ID"] == "A"]["Pd (kN)"] == 99).all())
        self.assertTrue((frame[frame["Case ID"] == "B"]["Pd (kN)"] != 99).all())
        control(self.app, "radio", "Load entry mode").set_value("Generate from unfactored actions")
        self.run_app()
        control(self.app, "radio", "Load entry mode").set_value("Enter factored cases")
        self.run_app()
        self.assertEqual(set(self.results()["Case ID"]), {"A", "B"})

    def test_invalid_case_does_not_hide_valid_results_or_exports(self):
        bad = case_row("BAD")
        bad["M (kN-m)"] = None
        self.set_cases([case_row("GOOD"), bad])
        frame = self.results()
        self.assertTrue((frame[frame["Case ID"] == "BAD"]["IP resistance"] == "INVALID").all())
        self.assertTrue((frame[frame["Case ID"] == "GOOD"]["IP resistance"] == "PASS").all())
        self.assertEqual(len(self.app.get("download_button")), 5)

    def test_empty_batch_has_no_pass_and_disabled_exports(self):
        self.set_cases([])
        self.assertFalse(self.app.success)
        self.assertTrue(any("No enabled cases" in i.value for i in self.app.info))
        self.assertTrue(all(button.disabled for button in self.app.get("download_button")))
        self.assertIn("Case ID", self.app.dataframe[0].value.columns)
        version = self.app.session_state["direct_version"]
        self.app.session_state[f"direct_editor_{version}"] = {
            "edited_rows": {}, "added_rows": [case_row("RECOVERED")], "deleted_rows": []}
        control(self.app, "button", "Apply factored cases").click()
        self.run_app()
        self.assertEqual(set(self.results()["Case ID"]), {"RECOVERED"})

    def test_switching_horizontal_steel_to_none_clears_engine_credit(self):
        control(self.app, "selectbox", "Horizontal reinforcement type").select("Bond beam bars")
        self.run_app()
        control(self.app, "number_input", "Area Av at each level (mm2)").set_value(200)
        self.run_app()
        control(self.app, "selectbox", "Horizontal reinforcement type").select("None")
        self.run_app()
        trace = table(self.app, "variable", "value", "equation").set_index("variable")
        self.assertEqual(float(trace.loc["Vs", "value"]), 0)

    def test_axial_overload_with_oop_input_does_not_crash(self):
        self.set_cases([case_row("OVER", p=10000)])
        control(self.app, "checkbox", "S304-14 linked OOP result supplied").set_value(True)
        control(self.app, "button", "Save and review case inputs").click()
        self.run_app()
        self.assertTrue((self.results()["IP resistance"] == "FAIL").all())
        self.assertTrue(self.results()["P-M utilization"].isna().all())


if __name__ == "__main__":
    unittest.main()
