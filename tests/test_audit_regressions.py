"""Regressions from the September 2026 clause and boundary-condition audit."""

from dataclasses import replace
import math
import unittest

from in_plane_engine import CalculationError, WallInput, calculate_wall


class AuditRegressions(unittest.TestCase):
    def test_squat_uplift_has_no_interpolated_false_positive_moment(self):
        result = calculate_wall(WallInput(length=5000.0, height=3000.0, thickness=140.0, nominal_unit_strength=30.0, factored_axial=-272.0, factored_moment=0.5))
        self.assertEqual(result.moment_resistance, 0.0)
        self.assertEqual(next(c.level for c in result.checks if c.title == "Axial load plus in-plane moment"), "blocked")

    def test_contiguous_grouted_cells_use_fully_grouted_shear_rules(self):
        for edition in ("S304-14", "S304:24"):
            full = WallInput(edition=edition, bar_count=25, shear_method="General")
            packed = replace(full, wall_type="Partially grouted reinforced masonry", grouted_cell_width=200.0)
            a, b = calculate_wall(full), calculate_wall(packed)
            self.assertEqual(b.section.gamma_g, 1.0)
            self.assertAlmostEqual(a.moment_resistance, b.moment_resistance)
            self.assertAlmostEqual(a.shear.resistance, b.shear.resistance)

    def test_axial_overload_with_oop_result_returns_checks(self):
        for axial in (-1000.0, 10000.0):
            result = calculate_wall(WallInput(factored_axial=axial, oop_check_provided=True))
            self.assertIsNone(result.moment_resistance)
            self.assertIsNone(result.stability.combined_utilization)
            self.assertTrue(any(c.level == "blocked" for c in result.checks))

    def test_zero_demand_at_pure_tension_endpoint_is_defined(self):
        result = calculate_wall(WallInput(factored_axial=-340.0, factored_moment=0.0, factored_shear=0.0, oop_check_provided=True))
        self.assertEqual(result.pm_utilization, 0.0)
        self.assertEqual(result.sliding_utilization, 0.0)
        self.assertEqual(result.stability.combined_utilization, 0.0)

    def test_invalid_ties_cannot_increase_any_compression_resistance(self):
        base = WallInput(factored_axial=1000.0)
        untied = calculate_wall(base)
        for changes in ({"tie_spacing": 500.0, "tie_arrangement_confirmed": True}, {"tie_spacing": 150.0, "tie_arrangement_confirmed": False}):
            invalid = calculate_wall(replace(base, compression_reinforcement_tied=True, **changes))
            self.assertEqual(invalid.axial_cap, untied.axial_cap)
            self.assertEqual(invalid.moment_resistance, untied.moment_resistance)

    def test_nominal_diameter_and_uncredited_transverse_steel_in_development(self):
        plain = calculate_wall(WallInput())
        transverse = calculate_wall(WallInput(horizontal_reinforcement_type="Bond beam bars", horizontal_reinforcement_area=200.0, horizontal_anchorage_confirmed=True))
        expected = 1.15 * 0.8 * 400 * 200 / (2.5 * 16.0 * math.sqrt(20.0))
        self.assertAlmostEqual(plain.detailing.vertical_development_required, expected)
        self.assertEqual(transverse.detailing.vertical_development_required, plain.detailing.vertical_development_required)

    def test_bond_bar_strength_limit_is_distinct_from_wire(self):
        with self.assertRaises(CalculationError):
            calculate_wall(WallInput(horizontal_reinforcement_type="Bond beam bars", horizontal_fy=500.0))
        calculate_wall(WallInput(horizontal_reinforcement_type="Joint reinforcement", horizontal_fy=485.0))

    def test_short_or_discontinuous_horizontal_steel_is_not_credited(self):
        base = WallInput(edition="S304:24", horizontal_reinforcement_type="Bond beam bars", horizontal_reinforcement_area=200.0, horizontal_anchorage_confirmed=True, horizontal_continuity_confirmed=True)
        self.assertGreater(calculate_wall(base).shear.steel_resistance, 0.0)
        for changes in ({"available_horizontal_development": 100.0}, {"horizontal_continuity_confirmed": False}):
            result = calculate_wall(replace(base, **changes))
            self.assertEqual(result.shear.steel_resistance, 0.0)
            self.assertTrue(any(c.level == "blocked" for c in result.checks))

    def test_simplified_shear_can_use_qualified_vertical_spacing(self):
        wall = WallInput(edition="S304:24", bar_count=21, shear_method="Simplified")
        result = calculate_wall(wall)
        spacing = (5000 - 2 * 100) / 20
        self.assertGreaterEqual(200, 0.002 * 190 * spacing)
        self.assertAlmostEqual(result.shear.beta, 230 / (1000 + 1.4 * spacing))

    def test_general_shear_reduces_underdeveloped_longitudinal_area(self):
        base = WallInput(edition="S304:24", shear_method="General")
        full = calculate_wall(base)
        short = calculate_wall(replace(base, available_vertical_development=300.0))
        fraction = 300 / full.detailing.vertical_development_required
        self.assertAlmostEqual(short.shear.epsilon_x, min(0.003, full.shear.epsilon_x / fraction))
        self.assertLess(short.shear.resistance, full.shear.resistance)
        self.assertEqual(short.sliding.developed_vertical_area, 0.0)

    def test_uplift_is_deducted_from_sliding_clamping(self):
        for edition, factor in (("S304-14", 0.6), ("S304:24", 1.0)):
            result = calculate_wall(WallInput(edition=edition, factored_axial=-100.0))
            self.assertAlmostEqual(result.sliding.resistance, factor * (0.85 * 1000 * 400 / 1000 - 100))
            no_dowels = calculate_wall(replace(result.wall, vertical_bars_developed_at_base=False, sliding_plane="Between masonry courses", uncracked_area_fraction=1.0))
            self.assertEqual(no_dowels.sliding.resistance, 0.0)

    def test_squat_cap_requires_full_length_load_distribution(self):
        base = WallInput(height=2500.0, horizontal_reinforcement_type="Bond beam bars", horizontal_reinforcement_area=200.0, horizontal_anchorage_confirmed=True, horizontal_continuity_confirmed=True)
        regular = calculate_wall(base)
        confirmed = calculate_wall(replace(base, squat_shear_distribution_confirmed=True))
        self.assertAlmostEqual(confirmed.shear.maximum_resistance / regular.shear.maximum_resistance, 1.5)

    def test_stack_spacing_and_missing_joint_actions_are_blocked(self):
        for kind, spacing in (("Bond beam bars", 1000.0), ("Joint reinforcement", 500.0)):
            result = calculate_wall(WallInput(bond_pattern="Stack pattern", horizontal_reinforcement_type=kind, horizontal_reinforcement_area=200.0, horizontal_reinforcement_spacing=spacing, horizontal_anchorage_confirmed=True, horizontal_continuity_confirmed=True))
            checks = {c.title: c.level for c in result.checks}
            self.assertEqual(checks["Stack-pattern head-joint reinforcement"], "blocked")
            self.assertEqual(checks["Stack-pattern vertical joint shear"], "blocked")

    def test_free_standing_tensile_limit_and_top_connection(self):
        wall = WallInput(wall_type="Unreinforced masonry", unreinforced_condition="Fully grouted hollow", bar_count=0, factored_axial=0.0, factored_moment=0.0, free_standing=True)
        result = calculate_wall(wall)
        self.assertEqual(result.section.ft, 0.1)
        self.assertAlmostEqual(result.moment_resistance, 0.6 * 0.1 * 190 * 5000**2 / 6 / 1e6)
        self.assertNotIn("Wall-to-floor/roof connection", [c.title for c in result.checks])

    def test_precracked_unreinforced_wall_has_no_pure_flexural_tension_credit(self):
        wall = WallInput(wall_type="Unreinforced masonry", bar_count=0, factored_axial=0.0, factored_moment=0.0, factored_shear=0.0, unreinforced_precracked=True)
        result = calculate_wall(wall)
        self.assertEqual(result.moment_resistance, 0.0)
        self.assertEqual(result.pm_utilization, 0.0)

    def test_class_a_requires_area_and_staggering_conditions(self):
        wall = WallInput(splice_present=True, splice_class="A")
        result = calculate_wall(wall)
        self.assertEqual(next(c.level for c in result.checks if c.title == "Class A splice eligibility"), "blocked")
        confirmed = calculate_wall(replace(wall, class_a_splice_confirmed=True))
        self.assertEqual(next(c.level for c in confirmed.checks if c.title == "Class A splice eligibility"), "pass")

    def test_self_weight_is_for_height_above_checked_joint(self):
        base = calculate_wall(WallInput())
        mid = calculate_wall(WallInput(height_above_section=2500.0))
        top = calculate_wall(WallInput(height_above_section=0.0))
        self.assertAlmostEqual(mid.self_weight, base.self_weight / 2)
        self.assertEqual(top.self_weight, 0.0)
        self.assertEqual(top.pd, 0.9 * 150)

    def test_invalid_counts_and_nonfinite_detailing_are_rejected(self):
        for changes in ({"bar_count": 2.5}, {"bar_count": True}, {"developed_vertical_bar_count": 1.5}, {"tie_spacing": math.nan}, {"tie_diameter": math.inf}, {"height_above_section": 6000.0}):
            with self.subTest(changes=changes), self.assertRaises(CalculationError):
                calculate_wall(replace(WallInput(), **changes))

    def test_no_required_area_is_reported_beyond_masonry_shear_cap(self):
        result = calculate_wall(WallInput(factored_shear=10000.0))
        self.assertIsNone(result.shear.required_horizontal_area)


if __name__ == "__main__":
    unittest.main()
