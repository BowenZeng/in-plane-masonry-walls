"""Behavior-level Streamlit regressions for the load-case workflow.

Expected values are hand values (derivation in each test) or equalities with
the load layer; none is a recorded engine output. The class is skipped below
the pinned Streamlit 1.61.1 (plan 7.3, F-148).
"""
import json
from pathlib import Path
import unittest

try:
    import streamlit
    from streamlit.testing.v1 import AppTest
except ImportError:
    streamlit = AppTest = None

APP = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")
STREAMLIT_FLOOR = (1, 61, 1)


def installed_streamlit():
    if streamlit is None:
        return (0, 0, 0)
    parts = []
    for piece in streamlit.__version__.split(".")[:3]:
        digits = ""
        for char in piece:
            if not char.isdigit():
                break
            digits += char
        parts.append(int(digits or 0))
    return tuple(parts + [0] * (3 - len(parts)))


def control(app, kind, label):
    return next(item for item in getattr(app, kind) if item.label == label)


def table(app, *columns):
    return next(item.value for item in app.dataframe if set(columns) <= set(item.value.columns))


def tables(app, *columns):
    return [item.value for item in app.dataframe if set(columns) <= set(item.value.columns)]


def notices(app):
    return [str(m.value) for kind in ("info", "warning", "error", "success") for m in getattr(app, kind)]


def case_row(case_id, p=200.0, m=50.0, v=20.0, lateral=0.0):
    # WP2: each direct case declares its signed lateral axial load (None = undeclared).
    return {"Case ID": case_id, "Name": case_id, "Enabled": True,
            "P (kN)": p, "M (kN-m)": m, "V (kN)": v,
            "Joint V (kN)": None, "Counteracting dead load D_cnt (kN)": None,
            "Signed lateral axial in this case (kN)": lateral,
            "Coupling-beam axial in this case (kN)": None}


# Hand values (default sidebar wall: lw 5000, t 190, 5-15M at e = 100, fy 400,
# f'gr 20 MPa, unit solid fraction 0.56, masonry 2100 / grout 2350 kg/m3).
PHI_M, PHI_S, FY, AB_15M = 0.60, 0.85, 400.0, 200.0


def self_weight(height_mm, *, glf=1.0, length=5000.0, thickness=190.0):
    """Wall weight above the section in kN: lw h t (sf rho_m + (1 - sf) glf rho_g) g."""
    return length * height_mm * thickness * 1.0e-9 * (0.56 * 2100.0 + 0.44 * glf * 2350.0) * 9.81 / 1000.0


def slide_14(pd, developed_bars):
    """S304-14 10.10.5.1: Vr = phi_m mu (Pd + phi_s As,dev fy), mu = 1.0, in kN."""
    return PHI_M * 1.0 * max(0.0, pd + PHI_S * developed_bars * AB_15M * FY / 1000.0)


def slide_24(pd, developed_bars):
    """S304:24 11.10.5.1 as printed (register I-4): Vr = mu (Pd + phi_s As,dev fy), in kN.

    kdev = 1: ld = 1.15 k3 fy Ab / ((dcs + Ktr) sqrt(f'gr)) with (dcs + Ktr) at
    the 2.5 db = 40 mm cap = 1.15 x 0.8 x 400 x 200 / (40 x sqrt(20)) = 411.4 mm
    < 1200 mm available (S304:24 13.4.2.3; S304-14 12.4.2.3 gives the same).
    """
    return 1.0 * max(0.0, pd + PHI_S * developed_bars * AB_15M * FY / 1000.0)


LEGACY_SET = "Legacy 14-row reference set (not NBC 2025)"
ORDINARY_CONDITIONS = (("Importance category", "Normal"),
                       ("Occupancy of the area carrying L", "Ordinary (companion L as tabulated)"),
                       ("Roof live load", "Roof live load included in L"),
                       ("Parking deck", "No parking deck"),
                       ("Dead-load parts", "None: D only"),
                       ("Fixed parts of W and E", "None: W and E fully reversible"))


@unittest.skipIf(AppTest is None or installed_streamlit() < STREAMLIT_FLOOR,
                 "Install requirements.txt (Streamlit 1.61.1) to run UI checks")
class StreamlitRegressionTests(unittest.TestCase):
    def setUp(self):
        self.app = AppTest.from_file(APP).run(timeout=40)
        self.assertFalse(self.app.exception)

    def run_app(self):
        self.app.run(timeout=40)
        self.assertFalse(self.app.exception)

    def set_cases(self, rows):
        self.app.session_state["direct_rows"] = rows
        self.app.session_state["direct_version"] += 1
        self.run_app()

    def set_input(self, kind, label, value):
        control(self.app, kind, label).set_value(value)
        self.run_app()

    def results(self):
        return table(self.app, "IP resistance", "Additional checks")

    def result(self, case_id, edition):
        return self.results().set_index(["Case ID", "Edition"]).loc[(case_id, edition)]

    def declare_seismic(self, system, sfrs_type=None, role="Loadbearing shear wall"):
        control(self.app, "selectbox", "Seismic force-resisting system (SFRS)").select(system)
        if sfrs_type is not None:
            control(self.app, "selectbox", "SFRS wall type").select(sfrs_type)
        control(self.app, "selectbox", "Shear-wall role").select(role)
        control(self.app, "button", "Apply seismic declaration").click()
        self.run_app()

    def generated_mode(self, load_set=None):
        control(self.app, "radio", "Load entry mode").set_value("Generate from unfactored actions")
        self.run_app()
        if load_set is not None:
            self.set_input("selectbox", "ULS load-combination set", load_set)

    def save_oop_results(self):
        for edition in ("S304-14", "S304:24"):
            control(self.app, "checkbox", f"{edition} linked OOP result supplied").set_value(True)
            control(self.app, "number_input", f"{edition} OOP utilization").set_value(0.1)
            control(self.app, "checkbox", f"{edition} OOP stability passes").set_value(True)
        control(self.app, "button", "Save and review case inputs").click()
        self.run_app()

    def banner(self):
        return [w.value for w in self.app.warning
                if "Seismic provisions not evaluated: S304-14 Clause 16 / S304:24 Clause 17" in w.value]

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

    def test_generated_legacy_set_checks_23_cases_and_copy_keeps_totals_and_seismic_gate(self):
        # Declared not part of the SFRS: the gate stays only for the E rows (WP3).
        self.declare_seismic("Not part of the SFRS")
        self.generated_mode(LEGACY_SET)
        before = self.results().copy()
        self.assertEqual(len(before), 46)  # 14 rows, 9 with W or E: 5 + 2 x 9 = 23 cases x 2 editions
        self.assertTrue(any("2 enabled case(s) carry an earthquake factor" in text for text in self.banner()))
        control(self.app, "button", "Copy generated cases to direct mode").click()
        self.run_app()
        self.assertEqual(control(self.app, "radio", "Load entry mode").value, "Enter factored cases")
        after = self.results()
        for column in ("Case ID", "Edition", "P (kN)", "Signed M (kN-m)", "Signed V (kN)",
                       "Pd (kN)", "Mr(Pf) (kN-m)", "Vr diagonal (kN)", "Vr sliding (kN)", "Additional checks"):
            self.assertEqual(before[column].tolist(), after[column].tolist())
        self.assertTrue(self.app.session_state["copied_origins"])
        # A copied earthquake row keeps its flag, so the gate and the banner stay.
        self.assertTrue(any("2 enabled case(s) carry an earthquake factor" in text for text in self.banner()))
        control(self.app, "selectbox", "Case to inspect / review").select("R14[E+]")
        self.run_app()
        checks = table(self.app, "Check", "Category", "Clause").set_index("Check")
        self.assertEqual(checks.loc["Seismic provisions not evaluated", "Status"], "UNSUPPORTED")

    def test_generated_factor_and_contribution_edits_are_applied(self):
        self.generated_mode(LEGACY_SET)
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

    def test_nbc_preset_needs_declared_conditions_then_generates_33_cases(self):
        """WP1 step 3: the NBC 2025 set is the default and has no default conditions.

        Ordinary fixture (Normal, ordinary occupancy, roof L included in L, no deck,
        no parts): 21 formulas of NBC 2025 Table 4.1.3.2.-A, 12 with W or E, so
        9 + 2 x 12 = 33 cases. 1.4D row: P = 1.4 (150 + SW) and Pd = 0.9 (150 + SW).
        """
        import load_combinations as lc
        self.generated_mode()
        self.assertEqual(control(self.app, "selectbox", "ULS load-combination set").value,
                         "NBC 2025 Table 4.1.3.2.-A (declare the load conditions)")
        frame = self.results()
        self.assertTrue((frame["IP resistance"] == "INVALID").all())
        self.assertTrue(any("Declare the NBC 2025 load conditions" in e.value for e in self.app.error))
        self.assertEqual(self.app.session_state["factor_rows"], [])
        self.assertTrue(control(self.app, "button", "Copy generated cases to direct mode").disabled)
        for label, value in ORDINARY_CONDITIONS:
            control(self.app, "selectbox", label).select(value)
        control(self.app, "button", "Apply NBC 2025 load conditions").click()
        self.run_app()
        frame = self.results()
        conditions = lc.nbc.LoadConditions("Normal", True, "ordinary", "included_in_L", "none")
        rows = lc.NBC2025_COMBINATIONS(conditions)
        expected = {c.case_id for c in lc.generate_load_cases({"D": lc.Action(150.0)}, rows, conditions=conditions)}
        self.assertEqual(len(rows), 21)
        self.assertEqual(len(expected), 9 + 2 * 12)
        self.assertEqual(len(frame), 2 * 33)
        self.assertEqual(set(frame["Case ID"]), expected)
        self.assertFalse((frame["IP resistance"] == "INVALID").any())
        mirrored = self.app.session_state["factor_rows"]
        self.assertEqual([r["Combination ID"] for r in mirrored], [c.combination_id for c in rows])
        self.assertEqual([[r[k] for k in lc.LOAD_TYPES] for r in mirrored],
                         [[c.factors.get(k, 0.0) for k in lc.LOAD_TYPES] for c in rows])
        dead = 150.0 + self_weight(5000.0)
        n1 = frame.set_index(["Case ID", "Edition"]).loc[("N1", "S304-14")]
        self.assertAlmostEqual(n1["P (kN)"], 1.4 * dead, places=6)
        self.assertAlmostEqual(n1["Pd (kN)"], 0.9 * dead, places=6)
        self.assertFalse(control(self.app, "button", "Copy generated cases to direct mode").disabled)

    def test_unused_nonzero_contribution_is_an_error_naming_the_type(self):
        """F-006: the legacy rows have no H factor, so an entered H makes every case invalid."""
        self.generated_mode(LEGACY_SET)
        version = self.app.session_state["generated_version"]
        self.app.session_state[f"contribution_editor_{version}"] = {
            "edited_rows": {5: {"P (kN)": 10.0}}, "added_rows": [], "deleted_rows": []}
        control(self.app, "button", "Apply unfactored actions and combinations").click()
        self.run_app()
        frame = self.results()
        self.assertTrue((frame["IP resistance"] == "INVALID").all())
        self.assertTrue(all("The H contribution is non-zero" in issue for issue in frame["Issues"]))

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
        self.generated_mode(LEGACY_SET)
        control(self.app, "radio", "Load entry mode").set_value("Enter factored cases")
        self.run_app()
        self.assertEqual(set(self.results()["Case ID"]), {"A", "B"})
        # The chosen ULS set survives direct mode, where its selectbox is not rendered.
        self.generated_mode()
        self.assertEqual(control(self.app, "selectbox", "ULS load-combination set").value, LEGACY_SET)

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

    # 7.4 keyed inputs (F-028, F-029; AT-LC-57/58 equivalents)

    def test_developed_count_defaults_to_all_bars_and_survives_bar_count_edits(self):
        """AT-LC-58: developed = 2 survives bars 5 -> 6 and 6 -> 4.

        Direct case P 300, M 600, V 250 kN, N_lat 0: Pd = 0.9 (150 + SW) =
        227.68 kN; 2 developed 15M: S304-14 Vr,slide = 0.6 (227.68 + 0.85 x 2 x
        200 x 400 / 1000) = 218.21 kN (utilization 250 / 218.21 = 1.146, FAIL);
        S304:24 Vr,slide = 363.68 kN.
        """
        label = "Developed vertical bars at sliding plane"
        self.set_cases([case_row("C", p=300.0, m=600.0, v=250.0)])
        self.assertIsNone(control(self.app, "number_input", label).value)  # blank = all bars
        self.set_input("number_input", label, 2)
        self.set_input("number_input", "Number of bars", 6)
        self.assertEqual(control(self.app, "number_input", label).value, 2)
        pd = 0.9 * (150.0 + self_weight(5000.0))
        s14, s24 = self.result("C", "S304-14"), self.result("C", "S304:24")
        self.assertAlmostEqual(s14["Pd (kN)"], pd, places=6)
        self.assertAlmostEqual(s14["Vr sliding (kN)"], slide_14(pd, 2), places=6)
        self.assertAlmostEqual(s14["Vr sliding (kN)"], 218.21, places=2)
        self.assertAlmostEqual(s14["Sliding utilization"], 250.0 / slide_14(pd, 2), places=6)
        self.assertAlmostEqual(s14["Sliding utilization"], 1.146, places=3)
        self.assertEqual(s14["IP resistance"], "FAIL")
        self.assertAlmostEqual(s24["Vr sliding (kN)"], slide_24(pd, 2), places=6)
        self.assertAlmostEqual(s24["Vr sliding (kN)"], 363.68, places=2)
        self.set_input("number_input", "Number of bars", 4)
        self.assertEqual(control(self.app, "number_input", label).value, 2)
        self.assertFalse([text for text in notices(self.app) if "Developed vertical bars" in text])

    def test_developed_count_is_reduced_once_and_never_raised(self):
        label = "Developed vertical bars at sliding plane"
        self.set_input("number_input", label, 5)
        self.set_input("number_input", "Number of bars", 4)
        self.assertEqual(control(self.app, "number_input", label).value, 4)
        self.assertEqual(len([i.value for i in self.app.info if "Developed vertical bars" in i.value]), 1)
        self.set_input("number_input", "Number of bars", 6)
        self.assertEqual(control(self.app, "number_input", label).value, 4)
        self.assertFalse([i.value for i in self.app.info if "Developed vertical bars" in i.value])

    def test_blank_developed_count_credits_all_ten_bars(self):
        """AT-LC-58: 10 bars, count blank = all bars: Vr,slide = 0.6 (227.68 + 680) = 544.61 kN and 907.68 kN."""
        self.set_cases([case_row("C", p=300.0, m=600.0, v=250.0)])
        self.set_input("number_input", "Number of bars", 10)
        self.assertIsNone(control(self.app, "number_input", "Developed vertical bars at sliding plane").value)
        pd = 0.9 * (150.0 + self_weight(5000.0))
        self.assertAlmostEqual(self.result("C", "S304-14")["Vr sliding (kN)"], slide_14(pd, 10), places=6)
        self.assertAlmostEqual(self.result("C", "S304-14")["Vr sliding (kN)"], 544.61, places=2)
        self.assertAlmostEqual(self.result("C", "S304:24")["Vr sliding (kN)"], slide_24(pd, 10), places=6)
        self.assertAlmostEqual(self.result("C", "S304:24")["Vr sliding (kN)"], 907.68, places=2)

    def test_height_above_section_survives_hw_edits_and_is_clamped_once(self):
        """AT-LC-57: height 0 survives hw 5000 -> 6000.

        Top section: self-weight 0, Pd = 0.9 x 150 = 135.0 kN (N_lat 0); all 5
        bars developed: Vr,slide = 0.6 (135.0 + 340.0) = 285.0 kN (S304-14) and
        475.0 kN (S304:24); legacy R01 1.4D: P = 1.4 x 150 = 210.0 kN. Then 5500
        mm with hw 6000 -> 5000 is clamped to 5000 mm with one notice naming the height.
        """
        label = "Wall height above checked section (mm)"
        self.set_cases([case_row("C", p=200.0, m=1000.0, v=200.0)])
        self.set_input("number_input", label, 0.0)
        self.set_input("number_input", "Height hw (mm)", 6000.0)
        self.assertEqual(control(self.app, "number_input", label).value, 0.0)
        s14, s24 = self.result("C", "S304-14"), self.result("C", "S304:24")
        self.assertAlmostEqual(s14["Pd (kN)"], 0.9 * 150.0, places=9)
        self.assertAlmostEqual(s14["Vr sliding (kN)"], slide_14(135.0, 5), places=9)
        self.assertAlmostEqual(s14["Vr sliding (kN)"], 285.0, places=9)
        self.assertAlmostEqual(s24["Vr sliding (kN)"], slide_24(135.0, 5), places=9)
        self.assertAlmostEqual(s24["Vr sliding (kN)"], 475.0, places=9)
        self.generated_mode(LEGACY_SET)
        self.assertAlmostEqual(self.result("R01", "S304-14")["P (kN)"], 1.4 * 150.0, places=9)
        control(self.app, "radio", "Load entry mode").set_value("Enter factored cases")
        self.run_app()
        self.set_input("number_input", label, 5500.0)
        self.set_input("number_input", "Height hw (mm)", 5000.0)
        self.assertEqual(control(self.app, "number_input", label).value, 5000.0)
        self.assertEqual(len([text for text in notices(self.app) if "height" in text.lower()]), 1)

    def test_untouched_base_section_follows_hw(self):
        """The base section stays the base section: Pd = 0.9 (150 + SW(6000 mm)) = 246.22 kN."""
        self.set_cases([case_row("C")])
        self.set_input("number_input", "Height hw (mm)", 6000.0)
        self.assertEqual(control(self.app, "number_input", "Wall height above checked section (mm)").value, 6000.0)
        self.assertAlmostEqual(self.result("C", "S304-14")["Pd (kN)"], 0.9 * (150.0 + self_weight(6000.0)), places=6)
        self.assertFalse([text for text in notices(self.app) if "height" in text.lower()])

    def test_face_shell_and_clear_cell_inputs_are_keyed(self):
        bedded = "Mortar-bedded face-shell thickness tf (mm) - reference value, confirm"
        minimum = "S304:24 minimum face-shell thickness (mm), Clause 3.1"
        cell = "Least clear reinforced-cell dimension (mm) - reference value, confirm"
        self.set_cases([case_row("C")])
        self.set_input("selectbox", "Wall type", "Partially grouted reinforced masonry")
        # An untouched reference value follows the unit thickness (36.2 -> 38.6 mm) ...
        self.set_input("selectbox", "Actual thickness t (mm)", 240.0)
        self.assertEqual(control(self.app, "number_input", bedded).value, 38.6)
        self.set_input("selectbox", "Actual thickness t (mm)", 190.0)
        self.assertEqual(control(self.app, "number_input", bedded).value, 36.2)
        # ... an entered value is kept.
        self.set_input("number_input", bedded, 30.0)
        self.set_input("number_input", cell, 90.0)
        self.set_input("selectbox", "Actual thickness t (mm)", 240.0)
        self.assertEqual(control(self.app, "number_input", bedded).value, 30.0)
        self.assertEqual(control(self.app, "number_input", cell).value, 90.0)
        self.set_input("selectbox", "Actual thickness t (mm)", 190.0)
        self.set_input("number_input", minimum, 28.0)
        self.set_input("number_input", bedded, 25.0)
        self.assertEqual(control(self.app, "number_input", minimum).value, 25.0)
        self.assertEqual(len([i.value for i in self.app.info if "minimum face-shell" in i.value]), 1)
        trace = table(self.app, "variable", "value", "equation").set_index("variable")
        self.assertEqual(float(trace.loc["tf (Ae)", "value"]), 25.0)
        self.assertEqual(float(trace.loc["least clear cell", "value"]), 90.0)
        # t - 2 tf = 190 - 2 x 60 = 70 mm < 90 mm: the clear cell is clamped once.
        self.set_input("number_input", bedded, 60.0)
        self.assertEqual(control(self.app, "number_input", cell).value, 70.0)
        self.assertEqual(len([i.value for i in self.app.info if "Least clear cell" in i.value]), 1)

    # F-126 and the WP3 seismic declaration

    def test_success_only_without_a_blocked_code_limit(self):
        """F-126: every IP resistance passes, but the undeclared seismic and role items block the success message."""
        self.set_cases([case_row("SAFE")])
        self.assertTrue((self.results()["IP resistance"] == "PASS").all())
        self.assertFalse(self.app.success)
        warning = next(w.value for w in self.app.warning if "Blocked code limits" in w.value)
        self.assertIn("All entered IP resistance cases pass, but", warning)
        self.assertIn("Seismic provisions not evaluated (S304-14 16: INCOMPLETE", warning)
        self.assertIn("Seismic provisions not evaluated (S304:24 17: INCOMPLETE", warning)
        self.assertIn("Infill shear wall role", warning)
        self.assertTrue(self.banner())
        self.declare_seismic("Not part of the SFRS")
        self.assertEqual([s.value for s in self.app.success], ["All entered IP resistance cases pass."])
        self.assertFalse([w.value for w in self.app.warning if "Blocked code limits" in w.value])
        self.assertFalse(self.banner())

    def test_sfrs_wall_never_reads_checks_clear(self):
        """WP3: the same reviewed case reads CHECKS CLEAR as a non-SFRS wall and OUTSTANDING as an SFRS wall."""
        self.set_cases([case_row("SAFE")])
        self.set_input("checkbox", "Wall-to-foundation force transfer confirmed", True)
        self.set_input("checkbox", "Wall-to-floor/roof force transfer confirmed", True)
        self.declare_seismic("Not part of the SFRS")
        self.save_oop_results()
        frame = self.results()
        self.assertTrue((frame["Supplement review"] == "Reviewed").all())
        self.assertTrue((frame["Additional checks"] == "CHECKS CLEAR (limited scope)").all())
        self.declare_seismic("Part of the SFRS (seismic force-resisting system)", "Conventional construction")
        self.save_oop_results()  # review again, so only the seismic gate differs
        frame = self.results()
        self.assertTrue((frame["Supplement review"] == "Reviewed").all())
        self.assertTrue((frame["IP resistance"] == "PASS").all())
        self.assertTrue((frame["Additional checks"] == "OUTSTANDING").all())
        self.assertFalse(self.app.success)
        banner = self.banner()
        self.assertEqual(len(banner), 1)
        self.assertIn("Clause 16", banner[0])
        self.assertIn("Clause 17", banner[0])
        self.assertIn("declared part of the SFRS (conventional construction)", banner[0])
        for checks in tables(self.app, "Check", "Category", "Clause"):
            statuses = checks.set_index("Check")["Status"]
            self.assertEqual(statuses["Seismic provisions not evaluated"], "UNSUPPORTED")
            self.assertEqual(set(statuses[~statuses.isin(["PASS", "INFO"])].index),
                             {"Seismic provisions not evaluated"})

    # New engine inputs, F-059, F-070 and the 7.3 floor

    def test_bond_beam_anchorage_declaration_controls_the_vs_credit(self):
        """F-080: no Vs without a declared anchorage; a standard hook credits 15M bond beams.

        S304-14 10.10.2.2: Vs = 0.6 phi_s Av fy dv / s = 0.6 x 0.85 x 200 x 400 x
        4000 / 400 / 1000 = 408.0 kN (dv = 0.8 lw = 4000 mm). Development: k1 1.3
        (fresh grout not declared), ld = 1.15 x 1.3 x 0.8 x 400 x 200 / (40 x
        sqrt(20)) = 534.9 mm <= 600 mm available.
        """
        self.set_cases([case_row("C")])
        self.set_input("selectbox", "Horizontal reinforcement type", "Bond beam bars")
        self.set_input("selectbox", "Bond-beam bar size", "15M")
        self.set_input("number_input", "Area Av at each level (mm2)", 200.0)
        self.set_input("number_input", "Vertical spacing s (mm)", 400.0)
        self.set_input("checkbox", "Horizontal reinforcement continuous along wall and at top", True)
        trace = table(self.app, "variable", "value", "equation").set_index("variable")
        self.assertEqual(float(trace.loc["Vs", "value"]), 0.0)
        self.set_input("selectbox", "Bond-beam bar end anchorage", "Standard stirrup hook (10M and 15M only)")
        trace = table(self.app, "variable", "value", "equation").set_index("variable")
        self.assertAlmostEqual(float(trace.loc["Vs", "value"]), 0.6 * PHI_S * 200.0 * FY * 4000.0 / 400.0 / 1000.0, places=3)
        # A 20M bar is beyond the hook limit (12.4.11.2(b)): no credit.
        self.set_input("selectbox", "Bond-beam bar size", "20M")
        self.set_input("number_input", "Area Av at each level (mm2)", 300.0)
        trace = table(self.app, "variable", "value", "equation").set_index("variable")
        self.assertEqual(float(trace.loc["Vs", "value"]), 0.0)

    def test_two_bars_per_bond_beam_need_their_clear_spacing(self):
        """F-074: Av = 2 x 200 mm2 per level is credited only with the bars per beam and their clear spacing.

        Clear spacing 50 mm: c/c 66 mm, dcs = 2/3 x 66 = 44 mm > 2.5 db = 40 mm cap,
        ld = 534.9 mm <= 600 mm; S304-14 Vs = 0.6 x 0.85 x 400 x 400 x 4000 / 400 / 1000 = 816.0 kN.
        """
        self.set_cases([case_row("C")])
        self.set_input("selectbox", "Horizontal reinforcement type", "Bond beam bars")
        self.set_input("selectbox", "Bond-beam bar size", "15M")
        self.set_input("number_input", "Area Av at each level (mm2)", 400.0)
        self.set_input("number_input", "Vertical spacing s (mm)", 400.0)
        self.set_input("checkbox", "Horizontal reinforcement continuous along wall and at top", True)
        self.set_input("selectbox", "Bond-beam bar end anchorage", "Mechanical anchorage developing fy")
        vs = lambda: float(table(self.app, "variable", "value", "equation").set_index("variable").loc["Vs", "value"])
        self.assertEqual(vs(), 0.0)
        self.set_input("number_input", "Bars per bond beam", 2)
        self.assertEqual(vs(), 0.0)
        self.set_input("number_input", "Clear spacing of the bars inside a bond beam (mm)", 50.0)
        self.assertAlmostEqual(vs(), 0.6 * PHI_S * 400.0 * FY * 4000.0 / 400.0 / 1000.0, places=3)

    def test_type_n_joint_reinforcement_needs_its_declared_fgr(self):
        """F-095: Type N mortar: no joint-wire credit until f'gr is declared.

        3.65 mm wire, f'gr 10 MPa: ld = max(1.15 x 0.8 x 400 x 10.46 / (9.125 x sqrt(10)), 300) = 300 mm
        <= 600 mm; S304-14 Vs = 0.6 x 0.85 x 50 x 400 x 4000 / 400 / 1000 = 102.0 kN.
        """
        label = "Joint-reinforcement f'gr in Type N mortar (MPa)"
        self.set_cases([case_row("C")])
        self.set_input("selectbox", "Horizontal reinforcement type", "Joint reinforcement")
        self.assertTrue(control(self.app, "number_input", label).disabled)
        self.set_input("selectbox", "Mortar", "Type N")
        self.assertFalse(control(self.app, "number_input", label).disabled)
        self.set_input("number_input", "Area Av at each level (mm2)", 50.0)
        self.set_input("number_input", "Vertical spacing s (mm)", 400.0)
        self.set_input("checkbox", "Joint reinforcement (or unreinforced-wall steel) anchored at both wall ends", True)
        self.set_input("checkbox", "Horizontal reinforcement continuous along wall and at top", True)
        vs = lambda: float(table(self.app, "variable", "value", "equation").set_index("variable").loc["Vs", "value"])
        self.assertEqual(vs(), 0.0)
        self.set_input("number_input", label, 10.0)
        self.assertAlmostEqual(vs(), 0.6 * PHI_S * 50.0 * FY * 4000.0 / 400.0 / 1000.0, places=3)

    def test_distributed_flexural_reinforcement_declaration_sets_s304_14_dv(self):
        """F-101 (register I-8): two bars at e = 1500 mm, d = 3500 mm < 0.8 lw = 4000 mm."""
        label = "Flexural reinforcement distributed along the length (S304-14 dv)"
        self.set_cases([case_row("C")])
        self.set_input("number_input", "Number of bars", 2)
        self.set_input("number_input", "End bar to wall end (mm)", 1500.0)
        dv = lambda: float(table(self.app, "variable", "value", "equation").set_index("variable").loc["dv", "value"])
        self.assertEqual(dv(), 3500.0)  # not declared: a two-bar wall gets no 0.8 lw credit
        self.set_input("selectbox", label, "Yes: distributed along the length")
        self.assertEqual(dv(), 0.8 * 5000.0)
        self.set_input("selectbox", label, "No: not distributed along the length")
        self.assertEqual(dv(), 3500.0)

    def test_unsupported_height_feeds_the_column_test(self):
        """S304:24 3.1 / S304-14 2.2: 500 x 3000 x 190 partially grouted is column-sized (3000 > 5 x 200 = 1000,
        500 < 3 x 200 = 600) and not permitted; with 900 mm between lateral supports it is not column-sized."""
        self.set_cases([case_row("C", m=10.0, v=5.0)])
        self.set_input("selectbox", "Wall type", "Partially grouted reinforced masonry")
        self.set_input("number_input", "Length lw (mm)", 500.0)
        self.set_input("number_input", "Height hw (mm)", 3000.0)
        self.set_input("number_input", "Number of bars", 2)
        checks = table(self.app, "Check", "Category", "Clause").set_index("Check")
        self.assertEqual(checks.loc["Column dimension limits", "Status"], "BLOCKED")
        self.set_input("number_input", "Unsupported height between lateral supports (mm)", 900.0)
        checks = table(self.app, "Check", "Category", "Clause").set_index("Check")
        self.assertEqual(checks.loc["Column dimension limits", "Status"], "PASS")
        self.assertIn("unsupported height h = 900 mm", checks.loc["Column dimension limits", "Result"])

    def test_original_case_is_not_pre_reviewed_with_bond_credit(self):
        """AT-LC-59 / F-045: no seeded CASE-1 supplement; between courses CASE-1 is evaluated once N_lat is declared.

        Auc = 0, so the bond route is mu Pd and the reinforced route governs: Vr,slide = 0.6 (227.68 + 340) =
        340.61 kN (S304-14) and 567.68 kN (S304:24). The default row leaves N_lat blank (WP2), which alone makes
        it INCOMPLETE.
        """
        self.assertNotIn("CASE-1", dict(self.app.session_state["direct_supplements"]))
        self.assertTrue((self.results()["Supplement review"] == "Not supplied").all())
        self.set_input("selectbox", "Sliding plane", "Between masonry courses")
        checks = table(self.app, "Check", "Category", "Clause").set_index("Check")
        self.assertIn("case Pd required", checks.loc["Sliding shear", "Result"])
        self.set_cases([case_row("CASE-1", p=200.0, m=1000.0, v=200.0)])
        frame = self.results()
        self.assertFalse((frame["IP resistance"] == "INCOMPLETE").any())
        self.assertTrue((frame["Supplement review"] == "Not supplied").all())
        pd = 0.9 * (150.0 + self_weight(5000.0))
        self.assertAlmostEqual(self.result("CASE-1", "S304-14")["Vr sliding (kN)"], slide_14(pd, 5), places=6)
        self.assertAlmostEqual(self.result("CASE-1", "S304:24")["Vr sliding (kN)"], slide_24(pd, 5), places=6)

    def test_bond_beam_courses_add_their_grout_to_the_self_weight(self):
        """F-042: partially grouted default wall, glf = 5 x 200 / 5000 = 0.2.

        SW = lw hw t (sf rho_m + (1 - sf) glf rho_g) g = 64.435 kN; four 200 mm
        courses add (1 - sf)(1 - glf) t lw (4 x 200) rho_g g = 6.167 kN.
        """
        self.set_cases([case_row("C")])
        self.set_input("selectbox", "Wall type", "Partially grouted reinforced masonry")
        self.set_input("selectbox", "Horizontal reinforcement type", "Bond beam bars")
        self.set_input("number_input", "Area Av at each level (mm2)", 100.0)
        checks = table(self.app, "Check", "Category", "Clause").set_index("Check")
        self.assertEqual(checks.loc["Self-weight basis", "Status"], "INCOMPLETE")
        base = self_weight(5000.0, glf=0.2)
        grout = 0.44 * 0.8 * 190.0 * 5000.0 * 800.0 * 1.0e-9 * 2350.0 * 9.81 / 1000.0
        frame = table(self.app, "Calculated self-weight (kN)", "D_cnt (kN)").set_index(["Case ID", "Edition"])
        self.assertAlmostEqual(frame.loc[("C", "S304-14"), "Calculated self-weight (kN)"], base, places=6)
        self.set_input("number_input", "Bond-beam courses above the checked section", 4)
        self.set_input("number_input", "Bond-beam course height (mm)", 200.0)
        frame = table(self.app, "Calculated self-weight (kN)", "D_cnt (kN)").set_index(["Case ID", "Edition"])
        self.assertAlmostEqual(frame.loc[("C", "S304-14"), "Calculated self-weight (kN)"], base + grout, places=6)
        checks = table(self.app, "Check", "Category", "Clause").set_index("Check")
        self.assertEqual(checks.loc["Self-weight basis", "Status"], "PASS")

    def test_proposed_8_10_2_3_correction_is_an_unreinforced_s304_24_option(self):
        """F-059: printed 8.10.2.3 by default; the proposed sqrt(f'm) form only when checked (register I-5)."""
        label = "S304:24 unreinforced shear: apply the proposed 8.10.2.3 correction (vm with sqrt(f'm))"
        self.assertTrue(control(self.app, "checkbox", label).disabled)
        self.set_cases([case_row("U")])
        self.set_input("selectbox", "Wall type", "Unreinforced masonry")
        self.assertFalse(control(self.app, "checkbox", label).disabled)
        s304_24 = tables(self.app, "Check", "Category", "Clause")[1].set_index("Check")
        self.assertIn("printed 8.10.2.3", s304_24.loc["Diagonal shear", "Result"])
        self.set_input("checkbox", label, True)
        s304_14, s304_24 = (t.set_index("Check") for t in tables(self.app, "Check", "Category", "Clause"))
        self.assertIn("proposed June 2025 correction", s304_24.loc["Diagonal shear", "Result"])
        self.assertNotIn("8.10.2.3", s304_14.loc["Diagonal shear", "Result"])

    def test_tie_arrangement_checkbox_names_the_review_item(self):
        """F-070: the checkbox confirms the 12.2.6 / 13.2.6 arrangement review item."""
        label = "Compression-steel tie arrangement confirmed: every bar laterally supported, hooks and placement"
        self.assertTrue(control(self.app, "checkbox", label).disabled)
        self.set_cases([case_row("C")])
        self.set_input("checkbox", "Credit tied compression reinforcement", True)
        checks = table(self.app, "Check", "Category", "Clause").set_index("Check")
        self.assertEqual(checks.loc["Compression-reinforcement tie arrangement", "Status"], "BLOCKED")
        self.set_input("checkbox", label, True)
        checks = table(self.app, "Check", "Category", "Clause").set_index("Check")
        self.assertEqual(checks.loc["Compression-reinforcement tie arrangement", "Status"], "PASS")

    def test_streamlit_floor_guard_stops_below_the_pin(self):
        import streamlit_app
        self.assertEqual(streamlit_app.STREAMLIT_FLOOR, STREAMLIT_FLOOR)
        self.assertEqual(streamlit_app.streamlit_version("1.61.1"), (1, 61, 1))
        self.assertEqual(streamlit_app.streamlit_version("1.62.0rc1"), (1, 62, 0))
        self.assertLess(streamlit_app.streamlit_version("1.45.1"), STREAMLIT_FLOOR)
        original = streamlit.__version__
        try:
            streamlit.__version__ = "1.45.1"
            old = AppTest.from_file(APP).run(timeout=40)
        finally:
            streamlit.__version__ = original
        self.assertFalse(old.exception)
        self.assertTrue(any("1.61.1" in e.value and "1.45.1" in e.value for e in old.error))
        self.assertFalse(old.dataframe)
        self.assertFalse(old.number_input)


if __name__ == "__main__":
    unittest.main()
