"""Independent arithmetic and end-to-end invariants for the batch layer."""
from dataclasses import replace
import csv
import io
import math
import unittest

import in_plane_engine as engine
import load_combinations as lc
import batch_reporting as reports


def wall_input(**values):
    """WP4: fixtures declare the 190 mm unit's least clear cell (no engine default)."""
    values.setdefault("least_clear_cell_dimension", 100.0)
    return engine.WallInput(**values)


class CombinationTests(unittest.TestCase):
    def test_reference_earthquake_factor_includes_axial_and_both_directions(self):
        cases = lc.generate_load_cases(
            {"D": lc.Action(100, 10, 2), "L": lc.Action(20, 8, 4),
             "S": lc.Action(40, 12, 8), "E": lc.Action(200, -50, -30)},
            [lc.REFERENCE_COMBINATIONS[-1]], self_weight=10)
        self.assertEqual([c.case_id for c in cases], ["R14[E+]", "R14[E-]"])
        self.assertEqual(cases[0].actions, lc.Action(330, -33, -24))
        self.assertEqual(cases[1].actions, lc.Action(-70, 67, 36))
        self.assertEqual(cases[0].dead_load_for_pd, 110)

    def test_wind_reversal_preserves_cancellation_before_absolute_value(self):
        cases = lc.generate_load_cases({"D": lc.Action(100, 60, 20), "W": lc.Action(200, -60, -20)},
                                       [lc.Combination("DW", "D +/- W", {"D": 1, "W": 1})])
        self.assertEqual(cases[0].actions, lc.Action(300, 0, 0))
        self.assertEqual(cases[1].actions, lc.Action(-100, 120, 40))

    def test_custom_wind_earthquake_gets_four_complete_vector_pairings(self):
        cases = lc.generate_load_cases({"D": lc.Action(40, -5, 8), "W": lc.Action(10, 20, -4),
                                        "E": lc.Action(-12, 8, 6), "Other": lc.Action(3, 2, 1)},
                                       [lc.Combination("CUSTOM", "Project formula", {"D": 1, "W": 2, "E": 0.5, "Other": 3})])
        expected = [(63, 45, 6), (75, 37, 0), (23, -35, 22), (35, -43, 16)]
        self.assertEqual([(c.actions.p, c.actions.m, c.actions.v) for c in cases], expected)
        self.assertEqual(len({c.case_id for c in cases}), 4)

    def test_reference_set_expands_to_23_cases_and_ids_survive_reordering(self):
        # WP1: the legacy set keeps 23 cases for the base load types; the NBC 2025
        # set of the ordinary fixture (one L, one S, no parts) has 33.
        actions = {key: lc.Action(1, 2, 3) for key in lc.BASE_TYPES}
        forward = lc.generate_load_cases(actions)
        backward = lc.generate_load_cases(actions, tuple(reversed(lc.REFERENCE_COMBINATIONS)))
        self.assertEqual(len(forward), 23)
        self.assertEqual({c.case_id: c.actions for c in forward}, {c.case_id: c.actions for c in backward})
        conditions = lc.nbc.LoadConditions("Normal", True, "ordinary", "included_in_L", "none")
        nbc_cases = lc.generate_load_cases(actions, lc.NBC2025_COMBINATIONS(conditions), conditions=conditions)
        self.assertEqual(len(nbc_cases), 33)
        self.assertFalse(any(c.error for c in nbc_cases))

    def test_self_weight_is_included_once_with_same_dead_basis_for_pd(self):
        wall = wall_input()
        weight = engine.wall_self_weight(wall)
        definitions = [lc.Combination("D14", "1.4D", {"D": 1.4}), lc.Combination("D09", ".9D", {"D": .9})]
        added = lc.generate_load_cases({"D": lc.Action(150, 0, 0)}, definitions, self_weight=weight)
        included = lc.generate_load_cases({"D": lc.Action(150 + weight, 0, 0)}, definitions,
                                         self_weight=weight, self_weight_treatment=lc.SELF_WEIGHT_INCLUDED)
        for a, b in zip(added, included):
            self.assertEqual(a.actions, b.actions)
        results = lc.check_load_cases(wall, added).results
        for r in results:
            self.assertAlmostEqual(r.result.pd, .9 * (150 + weight))
            self.assertAlmostEqual(r.result.wall.factored_axial, r.case.actions.p)
            self.assertAlmostEqual(r.result.self_weight, weight)

    def test_stack_joint_actions_use_factors_and_signs_and_missing_is_not_zero(self):
        definition = [lc.Combination("DW", "D+W", {"D": 1.25, "W": 1.4})]
        cases = lc.generate_load_cases({"D": lc.Action(10, 0, 0, 8), "W": lc.Action(0, 0, 1, -5)}, definition)
        self.assertEqual([c.actions.stack_v for c in cases], [3, 17])
        missing = lc.generate_load_cases({"D": lc.Action(10, 0, 0), "W": lc.Action(0, 0, 1, -5)}, definition)
        self.assertIsNone(missing[0].actions.stack_v)

    def test_invalid_formula_is_visible_and_does_not_suppress_valid_formula(self):
        cases = lc.generate_load_cases({"D": lc.Action(100)},
                                       [lc.Combination("BAD", "bad", {"D": math.nan}), lc.REFERENCE_COMBINATIONS[0]])
        batch = lc.check_load_cases(wall_input(), cases)
        self.assertEqual([r.resistance_status for r in batch.results], ["INVALID", "INVALID", "PASS", "PASS"])
        self.assertFalse(batch.all_ip_pass)

    def test_duplicate_formula_ids_and_unknown_load_factors_are_rejected(self):
        definitions = [lc.Combination("X", "first", {"D": 1}), lc.Combination("X", "second", {"W": 1})]
        self.assertTrue(all(c.error for c in lc.generate_load_cases({}, definitions)))
        self.assertTrue(lc.generate_load_cases({}, [lc.Combination("Z", "unknown", {"Rain": 1})])[0].error)

    def test_invalid_contribution_only_blocks_formulas_that_use_it(self):
        rows = [{"Load": key, "P (kN)": 0, "M (kN-m)": 0, "V (kN)": 0} for key in lc.LOAD_TYPES]
        rows[3]["M (kN-m)"] = "invalid"
        cases = lc.generate_load_cases(lc.contributions_from_rows(rows),
                                       [lc.REFERENCE_COMBINATIONS[0], lc.REFERENCE_COMBINATIONS[3]])
        self.assertFalse(cases[0].error)
        self.assertTrue(cases[1].error)


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.wall = wall_input()
        # WP2: direct cases declare their signed lateral axial load (here none);
        # Pd = 0.9 (D 150 kN + self-weight) from the wall's dead-load basis.
        self.case = lc.LoadCase("C1", "Low demand", lc.Action(200, 50, 20), lateral_axial=0.0)

    def test_default_single_case_matches_existing_engine_in_both_editions(self):
        case = replace(self.case, actions=lc.Action(200, 1000, 200))
        # WP-defaults (F-045): no uncracked-area credit unless entered; the engine default is 0.0 too.
        supplement = lc.review_supplement(self.wall, case, lc.CaseSupplement(uncracked_area_fraction=0.0))
        batch = lc.check_load_cases(self.wall, [case], {case.case_id: supplement})
        weight = engine.wall_self_weight(self.wall)
        for member in batch.results:
            self.assertAlmostEqual(member.result.pd, .9 * (150 + weight))
            expected = engine.calculate_wall(replace(self.wall, edition=member.edition,
                                                     dead_load_compression=member.result.pd,
                                                     pd_basis=member.result.pd_basis))
            self.assertEqual(member.result, expected)

    def test_generated_direct_and_standalone_agree_and_signed_actions_survive(self):
        generated = lc.generate_load_cases({"D": lc.Action(150, 20, 5), "W": lc.Action(-10, -300, -50)},
                                           [lc.REFERENCE_COMBINATIONS[9]], self_weight=engine.wall_self_weight(self.wall))
        direct = lc.factored_cases_from_rows(lc.direct_rows_from_cases(generated), section="Base", load_basis="Same basis")
        gen_results = lc.check_load_cases(self.wall, generated).results
        direct_results = lc.check_load_cases(self.wall, direct).results
        for a, b in zip(gen_results, direct_results):
            self.assertEqual(a.result, b.result)
            # WP2: Pd_case = 0.9 D_cnt + min(0, N_lat); W axial -10 kN x 1.4 in R10.
            self.assertAlmostEqual(a.case.pd_case, .9 * a.case.dead_load_for_pd + min(0, a.case.lateral_axial))
            standalone = engine.calculate_wall(replace(self.wall, edition=a.edition,
                factored_axial=a.case.actions.p, factored_moment=abs(a.case.actions.m), factored_shear=abs(a.case.actions.v),
                dead_load_compression=a.case.pd_case, pd_basis=a.case.pd_basis,
                uncracked_area_fraction=0))
            self.assertEqual(a.result, standalone)
        self.assertLess(generated[0].actions.m, 0)

    def test_direct_pf_is_not_increased_by_self_weight_or_refactored(self):
        result = lc.check_load_cases(self.wall, [self.case]).results[0].result
        self.assertEqual(result.wall.factored_axial, 200)
        self.assertAlmostEqual(result.pd, .9 * (150 + result.self_weight))

    def test_each_case_recomputes_demand_dependent_resistances(self):
        # WP2: the uplift case declares its tensile lateral axial load, which reduces its Pd.
        cases = [self.case, replace(self.case, case_id="OTHER", actions=lc.Action(-100, 500, 150), lateral_axial=-300.0)]
        batch = lc.check_load_cases(self.wall, cases)
        a, b = batch.results[1].result, batch.results[3].result
        self.assertNotEqual(a.moment_resistance, b.moment_resistance)
        self.assertNotEqual(a.shear.resistance, b.shear.resistance)
        self.assertNotEqual(a.sliding.resistance, b.sliding.resistance)

    def test_different_cases_govern_flexure_and_shear_and_any_failure_counts(self):
        cases = [replace(self.case, case_id="M", actions=lc.Action(200, 2000, 1)),
                 replace(self.case, case_id="V", actions=lc.Action(200, 1, 300))]
        batch = lc.check_load_cases(self.wall, cases)
        for edition in lc.EDITIONS:
            by_check = {g.check: g for g in batch.governing if g.edition == edition}
            self.assertEqual(by_check["P-M"].case_ids, ("M",))
            self.assertEqual(by_check["Diagonal shear"].case_ids, ("V",))
        self.assertFalse(batch.all_ip_pass)

    def test_invalid_duplicate_and_nonfinite_cases_do_not_abort_valid_cases(self):
        cases = [self.case, replace(self.case, case_id="DUP"), replace(self.case, case_id="DUP"),
                 replace(self.case, case_id="NAN", actions=lc.Action(math.nan))]
        batch = lc.check_load_cases(self.wall, cases)
        self.assertEqual(batch.results[0].resistance_status, "PASS")
        self.assertTrue(all(r.resistance_status == "INVALID" for r in batch.results[2:]))
        self.assertFalse(batch.all_ip_pass)

    def test_malformed_editor_row_is_retained_and_blank_is_not_zero(self):
        rows = [{"Case ID": "bad", "P (kN)": "abc", "M (kN-m)": 0, "V (kN)": 0},
                {"Case ID": "blank", "P (kN)": 0, "M (kN-m)": None, "V (kN)": 0},
                {"Case ID": "good", "P (kN)": 200, "M (kN-m)": 0, "V (kN)": 0}]
        cases = lc.factored_cases_from_rows(rows, section="Base", load_basis="External analysis")
        self.assertEqual([bool(c.error) for c in cases], [True, True, False])
        self.assertEqual(len(lc.check_load_cases(self.wall, cases).results), 6)

    def test_axial_overload_has_no_fake_zero_pm_utilization(self):
        case = replace(self.case, actions=lc.Action(10000, 0, 0))
        batch = lc.check_load_cases(self.wall, [case, replace(self.case, case_id="OK")])
        member = batch.results[0]
        pm = next(c for c in member.resistance_checks if c.check == "P-M")
        self.assertEqual(member.resistance_status, "FAIL")
        self.assertIsNone(pm.utilization)
        self.assertEqual(pm.status, "FAIL")
        self.assertIn(case.case_id, next(g for g in batch.governing if g.check == "P-M").unranked_case_ids)

    def test_zero_resistance_positive_demand_is_infinite_failure(self):
        wall = replace(self.wall, vertical_bars_developed_at_base=False)
        # WP2: zero clamping comes from a case Pd below zero (uplift 300 kN > 0.9 D_cnt).
        case = replace(self.case, actions=lc.Action(-10, 0, 1), lateral_axial=-300.0)
        batch = lc.check_load_cases(wall, [case])
        sliding = next(c for c in batch.results[0].resistance_checks if c.check == "Sliding")
        self.assertTrue(math.isinf(sliding.utilization))
        self.assertEqual(sliding.status, "FAIL")
        self.assertEqual(next(g for g in batch.governing if g.check == "Sliding").case_ids, ("C1",))

    def test_missing_stack_demand_is_incomplete_and_never_all_pass(self):
        wall = replace(self.wall, bond_pattern="Stack pattern", stack_joint_area=200)
        batch = lc.check_load_cases(wall, [self.case])
        self.assertEqual(batch.results[0].resistance_status, "INCOMPLETE")
        self.assertFalse(batch.all_ip_pass)

    def test_missing_oop_keeps_ip_results_but_additional_checks_outstanding(self):
        batch = lc.check_load_cases(self.wall, [self.case])
        self.assertTrue(batch.all_ip_pass)
        self.assertEqual(batch.results[0].additional_status, "OUTSTANDING")
        self.assertIsNone(batch.results[0].result.stability.combined_utilization)

    def test_oop_is_specific_to_case_and_edition_and_stale_on_changed_actions(self):
        supplement = lc.review_supplement(self.wall, self.case, lc.CaseSupplement(oop={
            "S304-14": lc.LinkedOOP(.1, True), "S304:24": lc.LinkedOOP(.3, False)}))
        cases = [self.case, replace(self.case, case_id="C2")]
        batch = lc.check_load_cases(self.wall, cases, {"C1": supplement})
        self.assertAlmostEqual(batch.results[0].result.stability.combined_utilization - batch.results[0].result.pm_utilization, .1)
        self.assertAlmostEqual(batch.results[1].result.stability.combined_utilization - batch.results[1].result.pm_utilization, .3)
        self.assertFalse(batch.results[1].result.stability.oop_stability_passed)
        self.assertIsNone(batch.results[2].result.stability.combined_utilization)
        changed = replace(self.case, actions=lc.Action(201, 50, 20))
        stale = lc.check_load_cases(self.wall, [changed], {"C1": supplement}).results[0]
        self.assertEqual(stale.supplement_status, "STALE")
        self.assertEqual(stale.resistance_status, "PASS")
        self.assertIsNone(stale.result.stability.combined_utilization)

    def test_pd_override_and_uncracked_credit_are_case_specific_and_require_review(self):
        wall = replace(self.wall, sliding_plane="Between masonry courses")
        supplement = lc.review_supplement(wall, self.case, lc.CaseSupplement(pd_override=10, pd_override_source="Analysis X",
                                                                             uncracked_area_fraction=.75))
        cases = [self.case, replace(self.case, case_id="OTHER")]
        members = lc.check_load_cases(wall, cases, {"C1": supplement}).results
        self.assertEqual(members[0].result.pd, 10)
        self.assertGreater(members[0].result.sliding.uncracked_area, 0)
        self.assertEqual(members[2].result.sliding.uncracked_area, 0)
        changed_wall = replace(wall, length=5100)
        stale = lc.check_load_cases(changed_wall, cases, {"C1": supplement}).results[0]
        self.assertEqual(stale.resistance_status, "INCOMPLETE")
        renewed = lc.review_supplement(changed_wall, self.case, supplement)
        self.assertIsNotNone(lc.check_load_cases(changed_wall, [self.case], {"C1": renewed}).results[0].result)

    def test_reordering_preserves_supplements_governors_and_fingerprints(self):
        second = replace(self.case, case_id="SECOND", actions=lc.Action(200, 400, 40))
        sup = lc.review_supplement(self.wall, self.case, lc.CaseSupplement(pd_override=1, pd_override_source="Analysis X"))
        a = lc.check_load_cases(self.wall, [self.case, second], {"C1": sup})
        b = lc.check_load_cases(self.wall, [second, self.case], {"C1": sup})
        self.assertEqual(a.governing, b.governing)
        self.assertEqual(a.results[0].fingerprint, b.results[2].fingerprint)
        self.assertEqual(b.results[2].result.pd, 1)

    def test_source_changes_invalidate_even_when_summed_actions_match(self):
        formula = [lc.Combination("X", "D+L", {"D": 1, "L": 1})]
        a = lc.generate_load_cases({"D": lc.Action(100, 20, 0), "L": lc.Action(50, 10, 0)}, formula)[0]
        b = lc.generate_load_cases({"D": lc.Action(100, 10, 0), "L": lc.Action(50, 20, 0)}, formula)[0]
        self.assertEqual(a.actions, b.actions)
        self.assertNotEqual(lc.case_fingerprint(self.wall, a), lc.case_fingerprint(self.wall, b))

    def test_negative_dead_basis_requires_explicit_compressive_pd_override(self):
        case = replace(self.case, dead_load_for_pd=-1)
        self.assertEqual(lc.check_load_cases(self.wall, [case]).results[0].resistance_status, "INVALID")
        sup = lc.review_supplement(self.wall, case, lc.CaseSupplement(pd_override=0, pd_override_source="Analysis X"))
        self.assertEqual(lc.check_load_cases(self.wall, [case], {case.case_id: sup}).results[0].result.pd, 0)

    def test_disabled_or_empty_batches_cannot_claim_all_cases_pass(self):
        self.assertFalse(lc.check_load_cases(self.wall, []).all_ip_pass)
        self.assertFalse(lc.check_load_cases(self.wall, [replace(self.case, enabled=False)]).all_ip_pass)
        self.assertEqual(len(lc.check_load_cases(self.wall, [self.case, replace(self.case, enabled=False)]).results), 2)

    def test_partial_grout_names_and_resistances_are_correct_for_each_pf(self):
        # F-071: the engine default is now "Discrete" (register I-9); this test
        # selects the "lower of" envelope explicitly.
        wall = replace(self.wall, wall_type="Partially grouted reinforced masonry",
                       partial_grout_method="Use lower of discrete and smeared")
        cases = [replace(self.case, case_id=f"P{p}", actions=lc.Action(p, 10, 1)) for p in (-100, 0, 200, 1000)]
        for member in lc.check_load_cases(wall, cases).results:
            result = member.result
            discrete = engine.calculate_wall(replace(result.wall, partial_grout_method="Discrete"))
            smeared = engine.calculate_wall(replace(result.wall, partial_grout_method="Smeared"))
            self.assertAlmostEqual(result.moment_resistance, min(discrete.moment_resistance, smeared.moment_resistance))
            self.assertEqual({result.curve_method, result.alternate_curve_method}, {"Discrete", "Smeared"})
            expected = discrete if result.curve_method == "Discrete" else smeared
            self.assertEqual(result.curve, expected.curve)

    def test_exports_include_all_cases_signed_actions_errors_and_engine_inputs(self):
        cases = [replace(self.case, actions=lc.Action(200, -50, -20)),
                 replace(self.case, case_id="BAD", error="Missing M")]
        batch = lc.check_load_cases(self.wall, cases)
        files = reports.exports(batch)
        self.assertEqual(set(files), {"cases", "governing", "checks", "trace", "pm_points"})
        rows = list(csv.DictReader(io.StringIO(files["cases"].decode())))
        self.assertEqual(len(rows), 4)
        self.assertEqual(float(rows[0]["Signed M (kN-m)"]), -50)
        self.assertEqual(rows[2]["IP resistance"], "INVALID")
        self.assertEqual(rows[2]["P-M utilization"], "")
        self.assertIn("WallInput", files["trace"].decode())
        self.assertIn("Input fingerprint", files["pm_points"].decode())

    def test_oop_reference_and_review_basis_are_exported(self):
        supplement = lc.review_supplement(self.wall, self.case, lc.CaseSupplement(oop={
            "S304:24": lc.LinkedOOP(.12, True, "Analysis ABC, case 17")}))
        batch = lc.check_load_cases(self.wall, [self.case], {self.case.case_id: supplement})
        payload = reports.exports(batch)["cases"].decode()
        self.assertIn("Analysis ABC, case 17", payload)
        self.assertIn(supplement.reviewed_for, payload)

    def test_invalid_actions_are_not_exported_as_zero_demands(self):
        cases = lc.factored_cases_from_rows([{"Case ID": "BAD", "P (kN)": "bad"}],
                                            section="Base", load_basis="Analysis")
        batch = lc.check_load_cases(self.wall, cases)
        rows = list(csv.DictReader(io.StringIO(reports.exports(batch)["cases"].decode())))
        self.assertEqual(rows[0]["P (kN)"], "")
        self.assertEqual(rows[0]["IP resistance"], "INVALID")


if __name__ == "__main__":
    unittest.main()
