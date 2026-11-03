from dataclasses import replace
import math
import pathlib
import sys
import unittest


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# WP2: the engine has no Pd fallback. Tests that need Pd supply the case Pd of
# the reference example explicitly: 0.9 (D 150 kN + self-weight 103.0 kN), no
# lateral axial load (Example 7.4 basis).
REFERENCE_PD = 0.9 * (150.0 + 103.0)
REFERENCE_PD_BASIS = "Example 7.4: 0.9 (150 + 103.0) kN; no lateral axial load"

from in_plane_engine import (  # noqa: E402
    BAR_AREAS,
    CalculationError,
    UNIT_FACE_SHELL_REFERENCE,
    calculate_wall,
    interaction_curve,
    moment_at_axial_load,
    section_point_at_c,
    section_properties,
)
from in_plane_engine import WallInput as _EngineWallInput  # noqa: E402


def WallInput(**values):  # noqa: N802 - fixture with the engine's signature
    """WP4: the least clear cell has no engine default; fixtures declare the unit's
    cell, min(100, t - 2 tf) with the reference mortar-bedded face shell."""
    if "least_clear_cell_dimension" not in values:
        t = values.get("thickness", 190.0)
        tf = values.get("face_shell_thickness") or UNIT_FACE_SHELL_REFERENCE["mortar_bedded"][float(t)]
        values["least_clear_cell_dimension"] = min(100.0, t - 2.0 * tf)
    return _EngineWallInput(**values)


class InPlaneEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.example = WallInput(
            horizontal_reinforcement_area=0.0,
            shear_method="Simplified",
            dead_load_compression=REFERENCE_PD,
            pd_basis=REFERENCE_PD_BASIS,
        )

    def test_example_7_1_fully_grouted_flexure(self) -> None:
        curve, _ = interaction_curve(self.example)
        self.assertAlmostEqual(moment_at_axial_load(curve, 0.0), 792.3, delta=0.8)
        self.assertAlmostEqual(moment_at_axial_load(curve, 200.0), 1186.6, delta=0.8)

        point = section_point_at_c(self.example, 2220.0)
        self.assertAlmostEqual(point.p, 1141.8, delta=0.2)
        self.assertAlmostEqual(point.m, 2325.4, delta=0.2)

    def test_example_7_1_squat_wall_reduced_moment_arm(self) -> None:
        point = section_point_at_c(replace(self.example, height=3000.0), 2220.0)
        self.assertAlmostEqual(point.p, 1141.8, delta=0.2)
        self.assertAlmostEqual(point.m, 2021.1, delta=0.2)

    def test_example_7_2_discrete_partial_grout(self) -> None:
        wall = replace(
            self.example,
            wall_type="Partially grouted reinforced masonry",
            partial_grout_method="Discrete",
        )
        result = calculate_wall(wall)
        self.assertAlmostEqual(result.section.area, 479_600.0, delta=1.0)
        self.assertAlmostEqual(result.section.weighted_fm, 9.0, delta=0.02)
        self.assertAlmostEqual(result.moment_resistance, 1102.4, delta=1.0)

    def test_unreinforced_code_eccentricity_limit_is_0_33_length(self) -> None:
        wall = WallInput(
            wall_type="Unreinforced masonry",
            length=1200.0,
            height=3000.0,
            bar_count=0,
            factored_axial=31.5,
            factored_moment=0.0,
            factored_shear=0.0,
            dead_load_compression=0.0,
            horizontal_reinforcement_area=0.0,
        )
        result = calculate_wall(wall)
        self.assertAlmostEqual(result.moment_resistance, 31.5 * 0.33 * 1.2, delta=0.02)

    def test_example_7_4_2014_shear(self) -> None:
        fully_grouted = replace(
            self.example,
            factored_shear=200.0,
            factored_moment=1000.0,
            dead_load_compression=0.9 * (150.0 + 103.0),
        )
        full = calculate_wall(fully_grouted)
        self.assertAlmostEqual(full.shear.masonry_resistance, 234.0, delta=0.2)
        self.assertAlmostEqual(full.shear.maximum_resistance, 499.5, delta=0.2)

        partial = calculate_wall(
            replace(
                fully_grouted,
                wall_type="Partially grouted reinforced masonry",
                dead_load_compression=0.9 * (150.0 + 64.4),
            )
        )
        self.assertAlmostEqual(partial.shear.masonry_resistance, 123.9, delta=0.2)
        self.assertAlmostEqual(partial.shear.required_horizontal_area, 18.6, delta=0.2)
        self.assertAlmostEqual(partial.shear.maximum_resistance, 273.6, delta=0.3)

    def test_2024_reinforced_shear_uses_new_beta_theta_and_limit(self) -> None:
        wall = replace(self.example, edition="S304:24", shear_method="Simplified")
        result = calculate_wall(wall)
        expected_beta = 230.0 / (1000.0 + 1.4 * 4000.0)
        expected_vm = 0.60 * expected_beta * math.sqrt(9.0) * 190.0 * 4000.0 / 1000.0
        self.assertAlmostEqual(result.shear.beta, expected_beta, places=8)
        self.assertEqual(result.shear.theta_degrees, 42.0)
        self.assertAlmostEqual(result.shear.masonry_resistance, expected_vm, places=6)
        self.assertAlmostEqual(
            result.shear.maximum_resistance,
            0.25 * 0.60 * 9.0 * 190.0 * 4000.0 / 1000.0,
            places=6,
        )

    def test_2024_sliding_omits_2014_phi_m_multiplier(self) -> None:
        wall14 = replace(self.example, factored_shear=0.0)
        wall24 = replace(wall14, edition="S304:24")
        result14 = calculate_wall(wall14)
        result24 = calculate_wall(wall24)
        self.assertAlmostEqual(
            result24.sliding_resistance / result14.sliding_resistance,
            1.0 / 0.60,
            places=8,
        )

    def test_2024_unreinforced_vm_omits_2014_strength_multiplier(self) -> None:
        base = WallInput(
            wall_type="Unreinforced masonry",
            bar_count=0,
            factored_shear=100.0,
            factored_moment=400.0,
            dead_load_compression=0.0,
            horizontal_reinforcement_area=0.0,
        )
        result14 = calculate_wall(base)
        result24 = calculate_wall(replace(base, edition="S304:24"))
        expected24 = (
            0.60
            * 0.16
            * (2.0 - 1.0)
            * base.thickness
            * (0.8 * base.length)
            * result24.section.gamma_g
            / 1000.0
        )
        self.assertAlmostEqual(result24.shear.masonry_resistance, expected24, places=6)
        self.assertGreater(result14.shear.masonry_resistance, result24.shear.masonry_resistance)

    def test_supported_grid_produces_finite_ordered_curves(self) -> None:
        for edition in ("S304-14", "S304:24"):
            for wall_type in (
                "Unreinforced masonry",
                "Fully grouted reinforced masonry",
                "Partially grouted reinforced masonry",
            ):
                wall = replace(
                    self.example,
                    edition=edition,
                    wall_type=wall_type,
                    height=3000.0,
                    bar_count=0 if wall_type == "Unreinforced masonry" else 5,
                )
                result = calculate_wall(wall)
                self.assertTrue(result.curve)
                self.assertTrue(all(math.isfinite(p.p) and math.isfinite(p.m) for p in result.curve))
                minimum_p = -result.tension_cap if wall_type != "Unreinforced masonry" else 0.0
                self.assertTrue(all(p.p >= minimum_p - 1.0e-8 and p.m >= -1.0e-8 for p in result.curve))
                self.assertIsNotNone(moment_at_axial_load(result.curve, min(wall.factored_axial, result.axial_cap)))

    def test_partial_grout_overlap_is_rejected(self) -> None:
        with self.assertRaises(CalculationError):
            section_properties(
                replace(
                    self.example,
                    wall_type="Partially grouted reinforced masonry",
                    grouted_cell_width=1300.0,
                )
            )

    def test_near_squat_wall_never_exceeds_normal_flexure(self) -> None:
        normal = calculate_wall(replace(self.example, height=5000.0, factored_axial=0.0))
        squat = calculate_wall(replace(self.example, height=4500.0, factored_axial=0.0))
        self.assertLessEqual(squat.moment_resistance, normal.moment_resistance + 1.0e-8)

    def test_undecided_shear_steel_requirement_leaves_its_details_incomplete(self) -> None:
        # S304-14, 4000 x 3000, Pf 200, Vf 60, Mf 150 kN m, 15M bond beams (Av 200) at 2400 mm.
        # Without the case Pd the no-steel Vr is unknown: the 10.15.1.4 spacing is
        # INCOMPLETE, not FAIL. With Pd 179.49 kN, Vm 221.74 kN >= Vf 60 kN: not required.
        wall = WallInput(edition="S304-14", length=4000.0, height=3000.0, factored_axial=200.0,
                         factored_shear=60.0, factored_moment=150.0, horizontal_reinforcement_type="Bond beam bars",
                         horizontal_bar_size="15M", horizontal_reinforcement_area=200.0,
                         horizontal_reinforcement_spacing=2400.0, bond_beam_anchorage="standard_hook")
        spacing = lambda result: next(c for c in result.checks
                                      if c.check_id == "ip.horizontal_shear_reinforcement_spacing")
        undecided = calculate_wall(wall)
        self.assertIsNone(undecided.shear.horizontal_steel_required)
        self.assertEqual((spacing(undecided).level, spacing(undecided).unresolved), ("blocked", "incomplete"))
        self.assertIn("undecided", spacing(undecided).detail)
        decided = calculate_wall(replace(wall, dead_load_compression=179.49, pd_basis="test: declared case Pd"))
        self.assertFalse(decided.shear.horizontal_steel_required)
        self.assertAlmostEqual(decided.shear.resistance_without_horizontal_steel, 221.74, places=2)
        self.assertEqual((spacing(decided).level, spacing(decided).kind), ("pass", "info"))

    def test_vertical_bar_spacing_over_2400_is_blocked(self) -> None:
        result = calculate_wall(
            replace(
                self.example,
                length=10_000.0,
                height=10_000.0,
                bar_count=2,
                factored_axial=0.0,
                factored_moment=0.0,
                factored_shear=0.0,
            )
        )
        spacing = next(c for c in result.checks if c.title == "Maximum vertical-bar spacing")
        self.assertEqual(spacing.level, "blocked")

    def test_partial_grout_lower_method_selects_smeared_example_result(self) -> None:
        result = calculate_wall(
            replace(
                self.example,
                wall_type="Partially grouted reinforced masonry",
                partial_grout_method="Use lower of discrete and smeared",
            )
        )
        self.assertAlmostEqual(result.moment_resistance, 1073.4, delta=1.0)
        self.assertAlmostEqual(result.alternate_moment_resistance, 1102.4, delta=1.0)

    def test_reinforced_axial_tension_curve_and_demand(self) -> None:
        result = calculate_wall(
            replace(
                self.example,
                factored_axial=-100.0,
                factored_moment=200.0,
            )
        )
        self.assertAlmostEqual(result.tension_cap, 340.0, places=6)
        self.assertIsNotNone(result.moment_resistance)
        self.assertLess(result.curve[0].p, 0.0)

    def test_unreinforced_full_and_solid_routes(self) -> None:
        full = calculate_wall(
            WallInput(
                wall_type="Unreinforced masonry",
                unreinforced_condition="Fully grouted hollow",
                bar_count=0,
            )
        )
        solid = calculate_wall(
            WallInput(
                wall_type="Unreinforced masonry",
                unreinforced_condition="Solid units",
                bar_count=0,
            )
        )
        self.assertEqual(full.section.area, full.section.gross_area)
        self.assertEqual(solid.section.area, solid.section.gross_area)
        self.assertAlmostEqual(full.section.ft, 0.65)
        self.assertAlmostEqual(solid.section.ft, 0.40)

    def test_default_self_weight_reproduces_reference_dead_load(self) -> None:
        result = calculate_wall(self.example)
        self.assertAlmostEqual(result.self_weight, 103.0, delta=0.1)
        self.assertAlmostEqual(result.pd, 0.9 * (150.0 + 103.0), delta=0.1)
        # WP2: without a supplied case Pd the engine forms none (no fallback).
        unsupplied = calculate_wall(replace(self.example, dead_load_compression=None))
        self.assertIsNone(unsupplied.pd)
        self.assertIsNone(unsupplied.sliding.resistance)
        self.assertEqual(next(c.unresolved for c in unsupplied.checks if c.check_id == "ip.sliding_shear"), "incomplete")
        partial = calculate_wall(
            replace(
                self.example,
                wall_type="Partially grouted reinforced masonry",
            )
        )
        self.assertAlmostEqual(partial.self_weight, 64.4, delta=0.1)

    def test_horizontal_shear_steel_requires_anchorage(self) -> None:
        unanchored = calculate_wall(
            replace(
                self.example,
                edition="S304:24",
                horizontal_reinforcement_type="Bond beam bars",
                horizontal_reinforcement_area=200.0,
                horizontal_reinforcement_spacing=200.0,
                horizontal_anchorage_confirmed=False,
                # F-074: two 10M bars per level (Av 200 > Ab 100) at a declared
                # 40 mm clear spacing inside the bond beam.
                bars_per_bond_beam=2,
                bond_beam_bar_clear_spacing=40.0,
            )
        )
        # F-080: bond-beam bars are credited only with a declared end anchorage
        # (a standard hook anchors 15M and smaller bars, 12.4.11.2 / 13.4.11.2).
        self.assertIsNone(unanchored.wall.bond_beam_anchorage)
        anchored = calculate_wall(
            replace(
                unanchored.wall,
                bond_beam_anchorage="standard_hook",
                horizontal_continuity_confirmed=True,
            )
        )
        self.assertEqual(unanchored.shear.steel_resistance, 0.0)
        self.assertGreater(anchored.shear.steel_resistance, 0.0)

    def test_bond_beam_development_is_checked(self) -> None:
        # F-066: the item is a requirement only where the steel is needed for
        # in-plane shear; Vf = 300 kN exceeds Vr without horizontal steel
        # (267.3 kN), so the short development is blocked.
        wall = replace(
            self.example,
            factored_shear=300.0,
            horizontal_reinforcement_type="Bond beam bars",
            horizontal_reinforcement_area=200.0,
            horizontal_bar_size="15M",
            bond_beam_anchorage="standard_hook",
            horizontal_continuity_confirmed=True,
            available_horizontal_development=100.0,
        )
        result = calculate_wall(wall)
        check = next(c for c in result.checks if c.title == "Bond-beam-bar development")
        self.assertEqual(check.level, "blocked")
        self.assertEqual(result.shear.steel_resistance, 0.0)
        # At Vf = 200 kN <= Vm = 233.96 kN the steel is not required: information only.
        not_required = calculate_wall(replace(wall, factored_shear=200.0))
        check = next(c for c in not_required.checks if c.title == "Bond-beam-bar development")
        self.assertEqual((check.level, check.kind), ("pass", "info"))
        self.assertIn("Not required for in-plane shear", check.detail)

    def test_joint_reinforcement_uses_600_mm_and_wire_diameter_limits(self) -> None:
        # F-066: the spacing limit applies where the steel is required for
        # in-plane shear (Vf = 300 kN > Vr without it, 267.3 kN); the 12.1.4
        # wire-diameter limit is unconditional.
        result = calculate_wall(
            replace(
                self.example,
                factored_shear=300.0,
                horizontal_reinforcement_type="Joint reinforcement",
                horizontal_reinforcement_area=21.0,
                horizontal_reinforcement_spacing=800.0,
                horizontal_anchorage_confirmed=True,
                horizontal_continuity_confirmed=True,
                joint_wire_diameter=5.5,
                mortar_joint_thickness=10.0,
            )
        )
        levels = {c.title: c.level for c in result.checks}
        self.assertEqual(levels["Horizontal shear-reinforcement spacing"], "blocked")
        self.assertEqual(levels["Joint-reinforcement wire diameter"], "blocked")

    def test_2024_general_shear_is_exposed_and_can_govern(self) -> None:
        result = calculate_wall(
            replace(self.example, edition="S304:24", shear_method="Use lower of simplified and general")
        )
        self.assertIsNotNone(result.shear.simplified_resistance)
        self.assertIsNotNone(result.shear.general_resistance)
        self.assertAlmostEqual(
            result.shear.resistance,
            min(result.shear.simplified_resistance, result.shear.general_resistance),
            places=8,
        )
        self.assertAlmostEqual(result.shear.epsilon_x, 0.0014583333333333334, places=12)
        self.assertAlmostEqual(result.shear.ze, 5600.0, places=6)
        self.assertAlmostEqual(result.shear.beta, 0.02471776589423648, places=12)

    def test_between_course_sliding_reports_bond_route_and_dowel_area(self) -> None:
        # Hollow URM default wall: Pd = 0.9 (150 + 54.80) = 184.32 kN; no uncracked-area credit by default (F-045).
        result = calculate_wall(
            replace(
                self.example,
                wall_type="Unreinforced masonry",
                bar_count=0,
                sliding_plane="Between masonry courses",
                factored_shear=400.0,
                dead_load_compression=0.9 * (150.0 + 54.80),
            )
        )
        self.assertIsNotNone(result.sliding.bond_route)
        self.assertGreater(result.sliding.required_dowel_area, 0.0)
        self.assertEqual(result.wall.uncracked_area_fraction, 0.0)
        self.assertAlmostEqual(result.sliding.resistance, 110.59, delta=0.005)

    def test_development_lap_and_clearance_checks_block_bad_detail(self) -> None:
        result = calculate_wall(
            replace(
                self.example,
                available_vertical_development=100.0,
                splice_present=True,
                provided_lap_length=100.0,
                least_clear_cell_dimension=20.0,
            )
        )
        levels = {c.title: c.level for c in result.checks}
        self.assertEqual(levels["Vertical bar fit in masonry cell"], "blocked")
        self.assertEqual(levels["Vertical-bar development at critical section"], "blocked")
        self.assertEqual(levels["Class B tension lap splice"], "blocked")

    def test_tied_compression_reinforcement_increases_axial_cap_when_ties_pass(self) -> None:
        untied = calculate_wall(self.example)
        tied = calculate_wall(
            replace(
                self.example,
                compression_reinforcement_tied=True,
                tie_arrangement_confirmed=True,
                tie_spacing=150.0,
            )
        )
        self.assertGreater(tied.axial_cap, untied.axial_cap)
        tie_check = next(c for c in tied.checks if c.title == "Compression-reinforcement ties")
        self.assertEqual(tie_check.level, "pass")

    def test_stack_pattern_checks_vertical_joint_separately(self) -> None:
        result = calculate_wall(
            replace(
                self.example,
                bond_pattern="Stack pattern",
                horizontal_reinforcement_type="Bond beam bars",
                horizontal_reinforcement_area=200.0,
                bond_beam_anchorage="standard_hook",  # F-080: declared end anchorage
                bars_per_bond_beam=2, bond_beam_bar_clear_spacing=40.0,  # F-074: two 10M per level
                horizontal_continuity_confirmed=True,
                stack_joint_area=1000.0,
                stack_vertical_shear=150.0,
            )
        )
        expected = 0.60 * 0.70 * 0.85 * 1000.0 * 400.0 / 1000.0
        self.assertAlmostEqual(result.shear.stack_vertical_resistance, expected, places=6)
        self.assertGreater(result.shear.resistance, expected)
        check = next(c for c in result.checks if c.title == "Stack-pattern vertical joint shear")
        self.assertEqual(check.level, "blocked")

    def test_oop_interface_blocks_until_linked_and_checks_combined_utilization(self) -> None:
        missing = calculate_wall(self.example)
        self.assertEqual(
            next(c for c in missing.checks if c.title == "Concurrent minor-axis/OOP check supplied").level,
            "blocked",
        )
        linked = calculate_wall(
            replace(
                self.example,
                oop_check_provided=True,
                oop_stability_passed=True,
                oop_utilization=0.10,
            )
        )
        self.assertAlmostEqual(linked.stability.combined_utilization, linked.pm_utilization + 0.10)

    def test_complete_rectangular_wall_keeps_only_unverified_a371_review(self) -> None:
        wall = replace(
            self.example,
            horizontal_reinforcement_type="Bond beam bars",
            horizontal_bar_size="15M",
            horizontal_reinforcement_area=200.0,
            bond_beam_anchorage="standard_hook",  # F-080: declared end anchorage
            horizontal_continuity_confirmed=True,
            base_connection_confirmed=True,
            top_connection_confirmed=True,
            oop_check_provided=True,
            oop_stability_passed=True,
            oop_utilization=0.05,
            # WP3: declared not part of the SFRS, in a case without E, and the
            # wall role declared (no seismic or infill gate applies).
            sfrs_wall=False,
            wall_role="loadbearing_shear_wall",
        )
        for edition in ("S304-14", "S304:24"):
            result = calculate_wall(replace(wall, edition=edition))
            pending = [c for c in result.checks if c.level != "pass"]
            self.assertEqual([c.check_id for c in pending], ["ip.a371_clearance_and_placement"])
            self.assertEqual(pending[0].kind, "review")
            self.assertIn("register I-21", pending[0].detail)


if __name__ == "__main__":
    unittest.main()
