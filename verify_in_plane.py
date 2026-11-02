"""Comprehensive wall-level verification matrix for the live app release."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math

from in_plane_engine import PD_DEAD_LOAD_FACTOR, WallInput, calculate_wall, wall_self_weight


@dataclass(frozen=True)
class Case:
    name: str
    wall: WallInput
    expect_blocked: bool = False


def complete_wall(**changes: object) -> WallInput:
    base = WallInput(
        horizontal_reinforcement_type="Bond beam bars",
        horizontal_bar_size="15M",
        horizontal_reinforcement_area=200.0,
        horizontal_reinforcement_spacing=200.0,
        horizontal_anchorage_confirmed=True,
        horizontal_continuity_confirmed=True,
        unit_geometry_confirmed=True,
        # Declared unit geometry (190 mm unit; comparison-workbook values): bedded and S304:24 minimum.
        face_shell_thickness=36.2,
        minimum_face_shell_thickness=30.0,
        least_clear_cell_dimension=100.0,  # declared clear cell of the 190 mm unit
        base_connection_confirmed=True,
        top_connection_confirmed=True,
        oop_check_provided=True,
        oop_stability_passed=True,
        oop_utilization=0.05,
    )
    return replace(base, **changes)


def with_case_pd(wall: WallInput) -> WallInput:
    """The case Pd of the matrix: 0.9 (D 150 kN + self-weight above the section)
    with no lateral axial load, unless the case supplies its own (WP2: the
    engine has no Pd fallback)."""
    if wall.dead_load_compression is not None:
        return wall
    return replace(wall, dead_load_compression=PD_DEAD_LOAD_FACTOR * (150.0 + wall_self_weight(wall)),
                   pd_basis="matrix case composition: 0.9 (150 kN + self-weight); no lateral axial load")


def blocked_count(result: object) -> int:
    return sum(check.level == "blocked" for check in result.checks)


def main() -> None:
    # Direct wall actions represent the governing section from the named
    # structural idealization; building-level distribution remains upstream.
    cases = (
        Case("cantilever-top-point-load", complete_wall(factored_shear=200.0, factored_moment=1000.0)),
        Case("fixed-fixed-pier-end", complete_wall(factored_shear=200.0, factored_moment=500.0)),
        Case("squat-cantilever", complete_wall(height=3000.0, factored_shear=200.0, factored_moment=600.0)),
        Case("tall-cantilever", complete_wall(height=7500.0, factored_shear=100.0, factored_moment=750.0)),
        Case("low-compression", complete_wall(factored_axial=0.0, factored_moment=500.0)),
        Case("high-compression", complete_wall(factored_axial=1500.0, factored_moment=500.0)),
        # The uplift case's lateral axial load exceeds 0.9 D_cnt by 100 kN: Pd_case = -100 kN.
        Case("reinforced-uplift", complete_wall(factored_axial=-100.0, factored_moment=200.0, factored_shear=100.0, shear_method="General",
                                                dead_load_compression=-100.0)),
        Case("partial-grout-lower", complete_wall(wall_type="Partially grouted reinforced masonry")),
        Case("unreinforced-hollow", complete_wall(wall_type="Unreinforced masonry", unreinforced_condition="Hollow ungrouted", bar_count=0, factored_shear=25.0, factored_moment=100.0, horizontal_reinforcement_type="None", horizontal_reinforcement_area=0.0)),
        Case("unreinforced-fully-grouted", complete_wall(wall_type="Unreinforced masonry", unreinforced_condition="Fully grouted hollow", bar_count=0, factored_shear=25.0, factored_moment=100.0, horizontal_reinforcement_type="None", horizontal_reinforcement_area=0.0)),
        Case("unreinforced-solid", complete_wall(wall_type="Unreinforced masonry", unreinforced_condition="Solid units", bar_count=0, factored_shear=25.0, factored_moment=100.0, horizontal_reinforcement_type="None", horizontal_reinforcement_area=0.0)),
        Case("smooth-base-sliding", complete_wall(sliding_friction=0.7, factored_shear=100.0)),
        Case("between-course-sliding", complete_wall(sliding_plane="Between masonry courses", uncracked_area_fraction=0.5, factored_shear=100.0)),
        Case("stack-pattern", complete_wall(bond_pattern="Stack pattern", factored_shear=20.0, stack_joint_area=1000.0, stack_vertical_shear=100.0)),
        Case("joint-reinforcement", complete_wall(horizontal_reinforcement_type="Joint reinforcement", horizontal_reinforcement_area=21.0, horizontal_reinforcement_spacing=200.0, factored_shear=150.0)),
        Case("bad-development", complete_wall(available_vertical_development=100.0), True),
        Case("bad-vertical-spacing", complete_wall(length=10_000.0, height=10_000.0, bar_count=2), True),
        Case("unanchored-horizontal-steel", complete_wall(horizontal_anchorage_confirmed=False), True),
        Case("missing-oop-interface", complete_wall(oop_check_provided=False), True),
        Case("missing-base-connection", complete_wall(base_connection_confirmed=False), True),
    )

    rows: list[tuple[str, str, float, float, float, int]] = []
    for case in cases:
        for edition in ("S304-14", "S304:24"):
            result = calculate_wall(with_case_pd(replace(case.wall, edition=edition)))
            blocked = blocked_count(result)
            if case.expect_blocked:
                assert blocked > 0, f"{case.name} {edition} should be blocked"
            else:
                assert blocked == 0, f"{case.name} {edition} unexpectedly blocked"
            assert result.moment_resistance is not None
            assert all(
                math.isfinite(value)
                for value in (
                    result.moment_resistance,
                    result.shear.resistance,
                    result.sliding.resistance,
                )
            )
            rows.append(
                (
                    case.name,
                    edition,
                    result.moment_resistance,
                    result.shear.resistance,
                    result.sliding.resistance,
                    blocked,
                )
            )

    # Independent structural-analysis demand relationships used in the matrix.
    assert 200.0 * 5000.0 / 1000.0 == 1000.0  # cantilever top point load
    assert 200.0 * 5000.0 / 2000.0 == 500.0  # fixed-fixed pier end moment

    # Reference and cross-edition numerical anchors.
    example = WallInput(
        least_clear_cell_dimension=100.0,
        factored_axial=200.0,
        factored_moment=1000.0,
        factored_shear=200.0,
        dead_load_compression=227.7,
        horizontal_reinforcement_type="None",
        horizontal_reinforcement_area=0.0,
        shear_method="Simplified",
    )
    result14 = calculate_wall(example)
    result24 = calculate_wall(replace(example, edition="S304:24"))
    assert abs(result14.moment_resistance - 1186.6) < 1.0
    assert abs(result14.shear.resistance - 233.9641889778846) < 1.0e-6
    assert abs(result24.shear.simplified_resistance - 47.67272727272727) < 1.0e-6

    # Pd supplied: 0.9 (150 + 103.0) kN for the reference example (Example 7.4 basis).
    normal = calculate_wall(replace(example, factored_axial=0.0, height=5000.0))
    near_squat = calculate_wall(replace(example, factored_axial=0.0, height=4500.0))
    assert near_squat.moment_resistance <= normal.moment_resistance + 1.0e-9

    print(f"VERIFICATION_MATRIX=PASS cases={len(rows)}")
    print("case,edition,Mr_kNm,Vr_kN,Vslide_kN,blocked")
    for row in rows:
        print(f"{row[0]},{row[1]},{row[2]:.3f},{row[3]:.3f},{row[4]:.3f},{row[5]}")


if __name__ == "__main__":
    main()
