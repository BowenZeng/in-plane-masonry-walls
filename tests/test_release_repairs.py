"""Release repair oracles, independent volume/arithmetic and boundary checks."""
from dataclasses import replace
import math
import unittest

import in_plane_engine as ip
import load_combinations as lc
import nbc_load_sets as nbc


def wall(**values):
    defaults = dict(least_clear_cell_dimension=100.0, face_shell_thickness=36.2,
                    minimum_face_shell_thickness=30.0, dead_load_compression=200.0)
    return ip.WallInput(**(defaults | values))


def partial(**values):
    defaults = dict(wall_type="Partially grouted reinforced masonry",
                    horizontal_reinforcement_type="Bond beam bars", horizontal_reinforcement_area=100.0,
                    bars_per_bond_beam=1, bond_beam_course_height=200.0,
                    bond_beam_levels_above_section=(0.0, 400.0, 800.0, 1200.0))
    return wall(**(defaults | values))


def conditions(**values):
    return nbc.LoadConditions(**(dict(importance_category="Normal", uls_specified=True,
                                    occupancy="ordinary", roof_live_load="included_in_L", parking_deck="none")
                                | values))


class NBCRepairTests(unittest.TestCase):
    def test_r02_inventory_and_existing_order(self):
        original = nbc.uls_set(conditions())
        for parts, count, cases in (((), 21, 33), (("D_partition",), 23, 37),
                                    (("D_soil",), 23, 37), (("D_partition", "D_soil"), 27, 45)):
            with self.subTest(parts=parts):
                rows = nbc.uls_set(conditions(parts=frozenset(parts), soil_saturated=False))
                self.assertEqual(len(rows), count)
                self.assertEqual(sum(len(nbc.expand_directions(r.factors)) for r in rows), cases)
                self.assertEqual([r.row_id for r in rows[:len(original)]], [r.row_id for r in original])
                self.assertTrue(all(r.factors["D"] == 1.0 for r in rows if r.case == 5))

    def test_r02_resisting_soil_is_retained_and_every_subset_is_present(self):
        base = conditions(parts=frozenset({"D_partition", "D_soil", "E_fixed"}), soil_saturated=True)
        rows = {r.row_id: r for r in nbc.uls_set(base)}
        for prefix in ("N50", "N5LS"):
            for suffix, omitted in (("_DP0", {"D_partition"}), ("_DS0", {"D_soil"}),
                                    ("_DP0_DS0", {"D_partition", "D_soil"})):
                row = rows[prefix + suffix]
                self.assertEqual({k for k in ("D_partition", "D_soil") if row.factors[k] == 0}, omitted)
                self.assertEqual(row.factors["E_fixed"], 1.0)
                self.assertEqual([f["E_fixed"] for _, f in nbc.expand_directions(row.factors)], [1.0, 1.0])
        resisting = nbc.uls_set(replace(base, soil_resisting=True))
        self.assertFalse(any("DS0" in r.row_id for r in resisting))
        self.assertTrue(all(r.factors["D_soil"] == 1.0 for r in resisting if r.case == 5))

    def test_r02_roof_family_and_full_vector_omission(self):
        cond = conditions(roof_live_load="separate", parts=frozenset({"L_roof", "D_partition"}))
        definitions = lc.NBC2025_COMBINATIONS(cond)
        self.assertIn("N5L_DP0", [d.combination_id for d in definitions])
        cases = lc.generate_load_cases({"D": lc.Action(100, 10, 20),
                                        "D_partition": lc.Action(500, 90, 80), "E": lc.Action(0, 1000, 5)},
                                       definitions, conditions=cond, self_weight_treatment=lc.SELF_WEIGHT_INCLUDED)
        by_id = {c.case_id: c for c in cases}
        self.assertEqual(by_id["N50_DP0[E+]"].actions, lc.Action(100, 1010, 25))
        self.assertEqual(by_id["N50_DP0[E-]"].actions, lc.Action(100, -990, 15))

    def test_r13_saturated_and_nonsaturated_n1(self):
        for saturated, expected in ((True, 215.0), (False, 210.0)):
            cond = conditions(parts=frozenset({"D_soil"}), soil_saturated=saturated)
            row = next(r for r in nbc.uls_set(cond) if r.row_id == "N1")
            self.assertEqual(100 * row.factors["D"] + 50 * row.factors["D_soil"], expected)
            self.assertIn("1.5D_soil" if saturated else "1.4D_soil", row.name)

    def test_r13_unknown_only_blocks_uls_and_types_are_validated(self):
        cond = conditions(parts=frozenset({"D_soil"}))
        self.assertEqual(cond.problems(), [])
        with self.assertRaisesRegex(ValueError, "saturated"):
            nbc.uls_set(cond)
        self.assertTrue(nbc.sls_set(cond))
        self.assertTrue(nbc.uls_set(conditions()))
        for value in (0, 1, "false", "undeclared"):
            self.assertTrue(replace(cond, soil_saturated=value).problems())

    def test_r14_fixed_earthquake_coupling_matches_hand_arithmetic(self):
        actions = {"D": lc.Action(100), "W": lc.Action(100), "E": lc.Action(), "E_fixed": lc.Action(-50)}
        row = lc.Combination("WE", "WE", {"D": 1, "W": 1, "E": 1, "E_fixed": 1})
        cases = lc.generate_load_cases(actions, (row,), coupling_beam_axial=True,
                                       self_weight_treatment=lc.SELF_WEIGHT_INCLUDED)
        first = cases[0]
        self.assertEqual((first.actions.p, first.lateral_axial, first.coupling_axial, first.pd_case),
                         (150.0, 50.0, -50.0, 40.0))
        self.assertTrue(first.warnings)
        result = ip.calculate_wall(wall(factored_axial=150, factored_shear=240, factored_moment=100,
                                        dead_load_compression=first.pd_case,
                                        available_vertical_development=1200))
        self.assertAlmostEqual(result.sliding.resistance, 228.0)
        self.assertGreater(240.0, result.sliding.resistance)
        self.assertTrue(all(c.coupling_axial == -50 for c in cases))

    def test_r14_signed_components_and_disabled_coupling(self):
        for e, fixed in ((10, 0), (0, -20), (10, -20), (-10, 20)):
            for sense in (-1, 1):
                for enabled in (False, True):
                    lateral, coupling = lc.lateral_axial_components(
                        {"E": e, "E_fixed": fixed, "W": 7, "W_fixed": 3},
                        {"E": sense, "E_fixed": 1, "W": sense, "W_fixed": 1}, enabled)
                    self.assertEqual(lateral, sense * (e + 7) + fixed + 3)
                    self.assertEqual(coupling, sense * e + fixed if enabled else 0)

    def test_pre_repair_review_fingerprint_is_stale_with_unchanged_inputs(self):
        item = ip.WallInput(least_clear_cell_dimension=100.0)
        case = lc.LoadCase("C1", "fixture", lc.Action(200, 50, 20))
        # Captured from the pre-repair fingerprint algorithm for this exact wall/case.
        old = "710b15dc895f2b45cd304c702664f521d37cc5bb5fb1cf402c034b05fc504618"
        supplement = lc.CaseSupplement(pd_override=40, pd_override_source="historic case review", reviewed_for=old)
        self.assertNotEqual(lc.case_fingerprint(item, case), old)
        members = lc.check_load_cases(item, (case,), {"C1": supplement}).results
        self.assertTrue(all(m.supplement_status == "STALE" for m in members))
        current = lc.review_supplement(item, case, supplement)
        self.assertTrue(all(m.supplement_status == "Reviewed"
                            for m in lc.check_load_cases(item, (case,), {"C1": current}).results))


class GeometryRepairTests(unittest.TestCase):
    def test_r04_small_end_cover_disallows_table_and_reduces_credit(self):
        item = wall(end_bar_distance=10, available_vertical_development=600)
        section = ip.section_properties(item)
        development = ip._vertical_ld(item, section)
        expected = 1.15 * .8 * 400 * 200 / (10 * math.sqrt(20))
        self.assertIsNone(development.table)
        self.assertAlmostEqual(development.length, expected)
        result = ip.calculate_wall(item)
        self.assertAlmostEqual(result.development_factor, 600 / expected)
        check = next(c for c in result.checks if c.check_id == "ip.vertical_bar_development_at_critical_section")
        self.assertEqual(check.level, "blocked")

    def test_r04_exact_cover_boundary(self):
        for end, allowed in ((24 - 1e-6, False), (24, True), (24 + 1e-6, True)):
            item = wall(end_bar_distance=end)
            self.assertEqual(ip._vertical_ld(item, ip.section_properties(item)).table is not None, allowed)

    def test_r04_spacing_thresholds_are_separate_from_physical_cover(self):
        for spacing, table in ((38.4 - 1e-6, False), (38.4, True), (48 - 1e-6, True), (48, True)):
            item = wall(bar_count=2, end_bar_distance=100, length=200 + spacing)
            development = ip._vertical_ld(item, ip.section_properties(item))
            self.assertEqual(development.table is not None, table)
            self.assertAlmostEqual(development.dcs, min(50, 2 * spacing / 3))
            if table:
                coefficient = 0.45 if spacing >= 48 else 0.6
                self.assertAlmostEqual(development.table, coefficient * .8 * 400 * 16 / math.sqrt(20))

    def test_r04_epoxy_uses_end_cover_and_preserves_adequate_cover_in_both_editions(self):
        for edition in ("S304-14", "S304:24"):
            for end, multiplier in ((56 - 1e-6, 1.5), (56, 1.2), (100, 1.2)):
                # A 116 mm clear cell admits the >= 3db cover boundary at e=56.
                item = wall(edition=edition, least_clear_cell_dimension=116, end_bar_distance=end,
                            epoxy_coated_vertical_bars=True)
                coated = ip._vertical_ld(item, ip.section_properties(item))
                bare = replace(item, epoxy_coated_vertical_bars=False)
                uncoated = ip._vertical_ld(bare, ip.section_properties(bare))
                self.assertAlmostEqual(coated.formula / uncoated.formula, multiplier)

    def test_r06_duplicate_or_overlapping_courses_are_invalid(self):
        for levels, course in (((1000, 1000, 1000), 200), ((1000, 1100), 200), ((1000, 1000), None)):
            item = partial(bond_beam_levels_above_section=levels, bond_beam_course_height=course)
            with self.subTest(levels=levels), self.assertRaisesRegex(ip.CalculationError, "duplicate|overlap"):
                ip.wall_self_weight(item)

    def test_r06_touching_courses_reordering_and_boundary_clipping(self):
        item = partial(bond_beam_levels_above_section=(-100, 100, 4900))
        other = replace(item, bond_beam_levels_above_section=(4900, 100, -100))
        self.assertEqual(ip.wall_self_weight(item), ip.wall_self_weight(other))
        # 100 + 200 + 100 mm after section/top clipping.
        uniform = 5 * 5 * .19 * (.56 * 2100 + .44 * .2 * 2350) * 9.81 / 1000
        extra = .44 * .8 * .19 * 5 * .4 * 2350 * 9.81 / 1000
        self.assertAlmostEqual(ip.wall_self_weight(item), uniform + extra)

    def test_r06_single_course_sliding_oracle(self):
        item = partial(edition="S304:24", horizontal_reinforcement_area=200, horizontal_bar_size="15M",
                       bond_beam_levels_above_section=(1000,), vertical_bars_developed_at_base=False,
                       developed_vertical_bar_count=0, factored_shear=196)
        # W contributes shear only; .9(D + calculated SW) is the case Pd.
        pd = .9 * (150 + ip.wall_self_weight(item))
        result = ip.calculate_wall(replace(item, dead_load_compression=pd))
        self.assertAlmostEqual(result.sliding.resistance, 194.379, delta=.001)
        self.assertLess(result.sliding.resistance, 196)


class IntervalWeightTests(unittest.TestCase):
    def test_r09_lower_and_upper_course_layouts_have_different_station_forces(self):
        for levels, expected in (((0, 400, 800, 1200), 132.2175115),
                                 ((3000, 3400, 3800, 4200), 138.38478382)):
            item = partial(bond_beam_levels_above_section=levels)
            total = ip.wall_self_weight(item)
            interval = ip.wall_weight_between(item, 0, 2500)
            self.assertAlmostEqual(100 + total - interval.weight, expected, places=7)
            self.assertAlmostEqual(interval.weight, interval.uniform_weight + interval.bond_beam_weight)

    def test_r09_straddling_course_and_interval_additivity(self):
        item = partial(bond_beam_levels_above_section=(2400,))
        first, second = ip.wall_weight_between(item, 0, 2500), ip.wall_weight_between(item, 2500, 5000)
        extra = .44 * .8 * .19 * 5 * .1 * 2350 * 9.81 / 1000
        self.assertAlmostEqual(first.bond_beam_weight, extra)
        self.assertAlmostEqual(second.bond_beam_weight, extra)
        self.assertAlmostEqual(first.weight + second.weight, ip.wall_self_weight(item))

    def test_r09_nonzero_section_and_top_clipping(self):
        item = partial(height_above_section=3000, bond_beam_levels_above_section=(-100, 2900))
        result = ip.wall_weight_between(item, 2000, 5000)
        self.assertAlmostEqual(result.weight, ip.wall_self_weight(item))
        extra = .44 * .8 * .19 * 5 * .2 * 2350 * 9.81 / 1000
        self.assertAlmostEqual(result.bond_beam_weight, extra)

    def test_r09_count_preserves_total_but_cannot_locate_interior_grout(self):
        item = partial(bond_beam_levels_above_section=4)
        self.assertAlmostEqual(ip.wall_weight_between(item, 0, 5000).weight, ip.wall_self_weight(item))
        with self.assertRaisesRegex(ip.MissingWeightDeclaration, "count"):
            ip.wall_weight_between(item, 0, 2500)

    def test_r09_missing_height_or_inventory_is_not_zero(self):
        for values in ({"bond_beam_course_height": None}, {"bond_beam_levels_above_section": None}):
            with self.subTest(values=values), self.assertRaises(ip.MissingWeightDeclaration):
                ip.wall_weight_between(partial(**values), 0, 2500)

    def test_r09_zero_count_above_does_not_describe_below(self):
        for levels in (0, (), (1000,)):
            item = partial(height_above_section=3000, bond_beam_levels_above_section=levels)
            with self.subTest(levels=levels), self.assertRaises(ip.UnsupportedWeightInterval):
                ip.wall_weight_between(item, 1000, 2000)
        for levels in (0, ()):
            result = ip.wall_weight_between(partial(bond_beam_levels_above_section=levels,
                                                    bond_beam_course_height=None), 0, 2500)
            self.assertEqual(result.bond_beam_weight, 0)

    def test_r09_inapplicable_supplemental_grout_supports_below_section(self):
        for item in (wall(height_above_section=3000),
                     partial(height_above_section=3000, horizontal_reinforcement_area=0),
                     partial(height_above_section=3000, unit_solid_fraction=1.0),
                     wall(height_above_section=3000, wall_type="Unreinforced masonry",
                          unreinforced_condition="Solid units", bar_count=0)):
            interval = ip.wall_weight_between(item, 1000, 2000)
            self.assertGreater(interval.weight, 0)
            self.assertEqual(interval.bond_beam_weight, 0)

    def test_r09_zero_length_interval_needs_no_inventory(self):
        self.assertEqual(ip.wall_weight_between(partial(bond_beam_levels_above_section=None), 0, 0).weight, 0)


if __name__ == "__main__":
    unittest.main()
