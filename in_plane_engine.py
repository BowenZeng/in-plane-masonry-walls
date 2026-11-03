"""CSA S304 in-plane masonry wall design engine.

The engine is independent of Streamlit.  It covers rectangular, solid wall
segments without openings for the three public routes in the UI:

* unreinforced hollow concrete masonry;
* fully grouted reinforced concrete masonry; and
* partially grouted reinforced concrete masonry.

Lengths are millimetres, stresses are MPa, forces are kN, and moments are
kN-m unless a field says otherwise.  Compression is positive.  The tool is a
validated draft, not a substitute for project-specific engineering review.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Literal


Edition = Literal["S304-14", "S304:24"]
WallType = Literal[
    "Unreinforced masonry",
    "Fully grouted reinforced masonry",
    "Partially grouted reinforced masonry",
]
CheckLevel = Literal["pass", "warning", "blocked"]
# Why a blocked numeric check is unresolved: "" = the requirement is not met
# (a failure); "incomplete" = a declaration the check needs is missing (no
# review evidence can resolve it); "unsupported" = the engine does not
# implement the requirement for the declared conditions (a coverage gap).
Unresolved = Literal["", "unsupported", "incomplete"]

PHI_M = 0.60
PHI_S = 0.85
E_S = 200_000.0
EPS_MU = 0.003
ECCENTRICITY_LIMIT = 0.33

# Pd is based on 0.9 times the dead load (S304-14 2.3 and 10.10.2.1; S304:24
# Clause 3). NBC 2025 4.1.3.2.(5) takes 1.0D in load case 5 for member
# resistances; this engine keeps 0.9 in every combination (register I-1,
# F-062). Flipping the constant changes only load-case-5 values.
PD_DEAD_LOAD_FACTOR = 0.9
# A bar exactly at mid-length belongs to the tension half in the S304:24
# eps_x area ("within 0.5 lw"), with a tolerance of 1e-6 lw (register I-6).
TENSION_HALF_BOUNDARY_BAR = "include"
# Reference face-shell thicknesses (mm) by nominal unit thickness. Source: the
# comparison workbook via masonry_oop.masonry_tables; CSA A165.1 was not
# supplied, so the values are unverified (register I-14). A result that uses
# one of them carries an INCOMPLETE ip.unit_face_shell_declared item.
UNIT_FACE_SHELL_REFERENCE = {
    "mortar_bedded": {140.0: 26.0, 190.0: 36.2, 240.0: 38.6, 290.0: 41.4},
    "minimum": {140.0: 25.0, 190.0: 30.0, 240.0: 35.0, 290.0: 35.0},
}
WALL_ROLES = ("loadbearing_shear_wall", "non_loadbearing_shear_wall", "infill_shear_wall")
# End anchorage of bond-beam shear bars (S304-14 12.4.11.2; S304:24 13.4.11.2):
# a standard stirrup hook anchors 15M and smaller bars only; mechanical
# anchorage develops fy for any size (F-080).
BOND_BEAM_ANCHORAGES = ("standard_hook", "mechanical")
HOOK_ANCHORED_BAR_SIZES = ("10M", "15M")
# Table route of S304-14 12.4.2.4 / S304:24 13.4.2.4 (Table 10): coefficient for
# walls whose bars being developed have a clear spacing of at least 2 db; 0.6
# for other cases. Preconditions: clear cover >= db, clear spacing >= 1.4 db.
TABLE_LD_COEFFICIENT = {"S304-14": 0.45, "S304:24": 0.4}
TABLE_LD_OTHER_CASES = 0.6
# f'gr for wire in Type S mortar (S304-14 12.4.1.3; S304:24 13.4.1.3); Type N
# mortar needs a declared joint_reinforcement_fgr.
JOINT_WIRE_MORTAR_FGR = 10.0
# Clause 16 / 17 provisions the engine does not evaluate (register I-22).
SEISMIC_PROVISIONS = {
    "S304-14": ("running bond 16.1.2; weldable reinforcement 16.4.1; minimum seismic reinforcement 16.4.5.1; "
                "seismic maximum spacings 16.4.5.3-16.4.5.4; conventional construction 16.5.3 (axial limit) and "
                "16.5.4 (capacity-design shear); moderately ductile and ductile walls 16.6-16.9 (hinge ties 16.6.5); "
                "increased strain 16.10; boundary elements 16.11"),
    "S304:24": ("running bond 17.1.2; weldable reinforcement 17.4.1; minimum seismic reinforcement 17.4.5.1; "
                "seismic maximum spacings 17.4.5.3-17.4.5.4; conventional construction 17.5.3 (c/d limit) and "
                "17.5.4 (capacity-design shear); moderately ductile and ductile walls 17.6-17.9 (hinge ties 17.6.5); "
                "increased strain 17.10; boundary elements 17.11; elastic performance 17.3.5"),
}


def _register(question: str, reading: str, cls: str, blocking: str, findings: str) -> dict[str, str]:
    return {"question": question, "reading": reading, "class": cls, "blocking": blocking, "findings": findings}


# Interpretation register entries owned by the in-plane engine. Classes: C
# confirmed defect, P provisional interpretation, S conservative policy, M
# model choice, G capability gap; "policy" and "declaration" mark tool rules.
# The ids are shared with the masonry_check provenance and the check details.
INTERPRETATIONS = {
    "I-1": _register("Dead-load factor in Pd for NBC load case 5: 0.9 (S304 Pd definition) or 1.0 (NBC 4.1.3.2.(5))?",
                     "0.9 in every combination; constant PD_DEAD_LOAD_FACTOR", "P",
                     "none; printed in every Pd trace", "F-062, F-002, F-003"),
    "I-2": _register("Pd without a declared case composition",
                     "not computed; the Pd-dependent checks are INCOMPLETE", "policy",
                     "diagonal shear (Pd routes) and sliding INCOMPLETE until D_cnt and the signed lateral axial load, or a sourced signed Pd, are declared",
                     "F-005, F-003, R05"),
    "I-3": _register("Sliding clamp basis",
                     "C = max(0, Pd_case + phi_s As,dev fy kdev), never Pf; bed-joint friction max(0, Pd_case); S304-14 7.10.5.1 P1 read as Pd",
                     "C + P", "none", "F-003, F-192"),
    "I-4": _register("S304:24 shear-friction resistances without phi_m as printed (April 2024)",
                     "printed form (11.10.5.1 p.91; 8.10.5 p.58)", "P",
                     "none; the detail names the printed basis", "F-056"),
    "I-5": _register("S304:24 8.10.2.3 vm without sqrt(f'm) as printed",
                     "printed form unless apply_proposed_8_10_2_3_correction", "P",
                     "URM only (outside Release 1 scope)", "F-059"),
    "I-6": _register("Bar exactly at mid-length in the eps_x area",
                     "include, tolerance 1e-6 lw (TENSION_HALF_BOUNDARY_BAR)", "P",
                     "none; one convention for every geometry", "F-057, R07"),
    "I-7": _register("eps_x doubling under net tension",
                     "only when Mf <= |Pf| e_t (compression face cracked)", "P", "none", "F-065"),
    "I-8": _register("S304-14 dv for walls without distributed flexural reinforcement",
                     "declared flexural_reinforcement_distributed; false -> min(0.8 lw, d)", "P",
                     "the declaration is required for S304-14 walls with fewer than 3 bars", "F-101"),
    "I-9": _register("Partially grouted flexure model",
                     "discrete segments (Table 4 Note 3 second option); the smeared value is reported beside it",
                     "P", "none", "F-071"),
    "I-10": _register("Squat-wall deep-section model (10.2.8 / 11.2.8)",
                      "plane-section bar forces with the resultant at d (force-weighted tension centroid in S304:24)",
                      "P", "P-M UNSUPPORTED when the reduced-arm moment is negative or a >= d",
                      "F-055, F-058, F-068"),
    "I-12": _register("dcs reading",
                      "S304:24 outside masonry surface (t/2 for a centred bar, the wall end for vertical bars, 2s/3); "
                      "S304-14 'closest masonry surface' = min(least clear cell / 2, t/2, end_bar_distance); "
                      "clear cover to that same physical surface, with the separate 2s/3 limit on dcs; "
                      "the 12.4.2.4 / 13.4.2.4 table route offered; 12.4.2.3 with sqrt(f'gr)",
                      "P", "none", "F-038, F-053, F-141"),
    "I-13": _register("Bars developed over less than ld",
                      "proportional kdev in Mr, Tr and the S304:24 dowel force; S304-14 dowels all-or-nothing",
                      "P", "none", "F-047"),
    "I-14": _register("Face-shell thickness per edition (CSA A165.1 not supplied)",
                      "declared values; reference table UNIT_FACE_SHELL_REFERENCE unverified", "declaration",
                      "ip.unit_face_shell_declared INCOMPLETE whenever a reference value was used",
                      "F-030, F-061, F-052"),
    "I-15": _register("Undeclared grout type under 'lower of'",
                      "candidate ga 1.4 bounded by the simplified route; forced general route INCOMPLETE", "policy",
                      "as stated; the General method raises", "F-034"),
    "I-16": _register("k1 for bond-beam bars", "declared horizontal_top_bar", "declaration",
                      "required when bond beams are entered", "F-037"),
    "I-17": _register("Horizontal steel beyond the spacing limit; spacing items where no shear steel is needed",
                      "not credited; items not applicable when Vf <= Vr(Av = 0)", "C + P", "none", "F-046, F-066"),
    "I-18": _register("Minimum vertical steel for s <= 4t",
                      "total As >= 0.00125 t lw (C); end-bar tributary rule (P)", "C + P",
                      "none; both named in the detail", "F-075"),
    "I-19": _register("Column definition h > 5(t+10) and lw < 3(t+10)",
                      "partially grouted or hollow elements blocked", "C",
                      "ip.column_dimension_limits FAIL", "F-089, R08"),
    "I-20": _register("Minimum-steel continuity with terminated bars", "not modelled", "G",
                      "ip.minimum_vertical_reinforcement_continuity UNSUPPORTED when bars are not continuous",
                      "F-033, R04"),
    "I-21": _register("A371 clearance limits (not supplied)", "none", "declaration",
                      "review item only; no numeric check", "F-099"),
    "I-22": _register("Seismic provisions (Clause 16 / 17) and NBC 4.1.8 applicability",
                      "not implemented; 4.1.8.1 simplified-procedure exemption with verified eligibility", "G",
                      "seismic gate UNSUPPORTED (INCOMPLETE while undeclared) for every wall",
                      "F-007, F-048, F-049, F-014"),
    "I-30": _register("Infill shear walls", "not evaluated", "G",
                      "ip.infill_shear_wall_role UNSUPPORTED", "F-012"),
    "I-31": _register("Unreinforced shear walls", "legacy route only, outside Release 1 scope", "G",
                      "masonry_check has no URM route; UI/batch URM runs carry the seismic gate and the face-shell declaration item",
                      "F-031, F-052, F-090, F-098"),
}

BAR_PROPERTIES = {
    "10M": (100.0, 11.3),
    "15M": (200.0, 16.0),
    "20M": (300.0, 19.5),
    "25M": (500.0, 25.2),
}
BAR_AREAS = {size: values[0] for size, values in BAR_PROPERTIES.items()}

# Concrete masonry values from the edition-specific CSA S304 Table 4 routes.
# Each pair is (ungrouted, fully grouted), in MPa.
_FM_TABLE: dict[Edition, dict[str, dict[float, tuple[float, float]]]] = {
    "S304-14": {
        "Type S": {
            10.0: (6.5, 5.0),
            15.0: (10.0, 7.5),
            20.0: (13.0, 10.0),
            30.0: (17.5, 13.5),
        },
        "Type N": {
            10.0: (6.0, 4.5),
            15.0: (8.0, 6.0),
            20.0: (10.0, 7.5),
            30.0: (12.0, 9.0),
        },
    },
    "S304:24": {
        "Type S": {
            10.0: (7.5, 6.0),
            15.0: (12.0, 9.0),
            20.0: (15.5, 12.0),
            30.0: (20.5, 16.0),
        },
        "Type N": {
            10.0: (7.0, 5.5),
            15.0: (9.5, 7.0),
            20.0: (12.0, 9.0),
            30.0: (14.0, 10.5),
        },
    },
}

_FT_TABLE = {
    "Hollow ungrouted": {"Type S": 0.40, "Type N": 0.30},
    "Fully grouted hollow": {"Type S": 0.65, "Type N": 0.50},
    "Solid units": {"Type S": 0.40, "Type N": 0.30},
}


@dataclass(frozen=True)
class WallInput:
    edition: Edition = "S304-14"
    wall_type: WallType = "Fully grouted reinforced masonry"
    length: float = 5000.0
    height: float = 5000.0
    thickness: float = 190.0
    nominal_unit_strength: float = 15.0
    mortar_type: str = "Type S"
    bond_pattern: str = "Running bond"
    free_standing: bool = False
    unreinforced_condition: str = "Hollow ungrouted"
    unreinforced_precracked: bool = False
    # Partially grouted flexure: the discrete model of Table 4 Note 3 (grouted
    # and ungrouted parts treated separately) by default; "Smeared" is a tool
    # idealization and "Use lower of discrete and smeared" a tool-side
    # conservative envelope (register I-9, F-071).
    partial_grout_method: str = "Discrete"
    unit_geometry_confirmed: bool = False
    # Mortar-bedded face-shell thickness (mm): S304-14 Ae and the clear-cell
    # bound. S304:24 Ae uses the Clause 3.1 minimum face-shell thickness.
    # None = not declared: the unverified UNIT_FACE_SHELL_REFERENCE value is
    # used and ip.unit_face_shell_declared is INCOMPLETE (register I-14).
    face_shell_thickness: float | None = None
    minimum_face_shell_thickness: float | None = None
    grouted_cell_width: float = 200.0
    bar_size: str = "15M"
    bar_count: int = 5
    end_bar_distance: float = 100.0
    fy: float = 400.0
    compression_reinforcement_tied: bool = False
    tie_arrangement_confirmed: bool = False
    tie_diameter: float = 3.65
    tie_spacing: float = 200.0
    horizontal_reinforcement_type: str = "None"
    horizontal_bar_size: str = "10M"
    horizontal_reinforcement_area: float = 0.0
    horizontal_reinforcement_spacing: float = 200.0
    horizontal_fy: float = 400.0
    # Joint reinforcement (and URM) end anchorage confirmation. Bond-beam bars
    # use the declared bond_beam_anchorage instead (F-080).
    horizontal_anchorage_confirmed: bool = False
    horizontal_continuity_confirmed: bool = False
    horizontal_top_bar: bool = False
    # Bond-beam declarations (None = not declared; no credit without them):
    # end anchorage "standard_hook" (10M/15M only) or "mechanical" (F-080);
    # bars per bond beam and, for 2 or more, their clear spacing inside the
    # beam in mm, which sets dcs = 2/3 of the c/c spacing (F-074); the
    # bond-beam courses above the checked section for the self-weight: a
    # tuple of course elevations (mm above the section, bottom of course) or
    # a course count, with the course height in mm (F-042).
    bond_beam_anchorage: str | None = None
    bars_per_bond_beam: int | None = None
    bond_beam_bar_clear_spacing: float | None = None
    bond_beam_levels_above_section: tuple[float, ...] | int | None = None
    bond_beam_course_height: float | None = None
    # In-mortar f'gr (MPa) for joint-reinforcement development with Type N
    # mortar; Type S uses 10 MPa (12.4.1.3 / 13.4.1.3). None = not declared.
    joint_reinforcement_fgr: float | None = None
    shear_method: str = "Use lower of simplified and general"
    # Printed April 2024 Clause 8.10.2.3 omits sqrt(f'm). The June 2025
    # corrections list that clause but their wording has not been read here;
    # the square-root form is a proposed correction, off by default.
    apply_proposed_8_10_2_3_correction: bool = False
    # None = not declared: the S304:24 General method raises; a general route
    # forced by axial tension uses fine grout (ga 1.7) with an INCOMPLETE item;
    # the "lower of" candidate uses ga 1.4, bounded by the simplified route (I-15).
    grout_type: str | None = None
    squat_shear_distribution_confirmed: bool = False
    stack_joint_area: float | None = None
    stack_vertical_shear: float | None = None
    factored_axial: float = 200.0
    factored_moment: float = 1000.0
    factored_shear: float = 200.0
    # Signed case Pd (kN) formed by the case layer: 0.9 D_cnt plus the signed
    # lateral axial load of the case (load_combinations.case_pd). None leaves
    # the Pd-dependent checks INCOMPLETE; there is no fallback (register I-2).
    dead_load_compression: float | None = None
    pd_basis: str = ""
    # Dead-load basis kept for the case layer's D_cnt (direct rows without
    # their own value); the engine itself never forms Pd from it.
    unfactored_dead_load_above: float = 150.0
    include_wall_self_weight_in_pd: bool = True
    height_above_section: float | None = None
    # Unsupported height between lateral supports (mm) for the column test
    # (S304-14 2.2; S304:24 3.1); None = the wall height (single storey).
    unsupported_height: float | None = None
    masonry_density: float = 2100.0
    grout_density: float = 2350.0
    unit_solid_fraction: float = 0.56
    sliding_friction: float = 1.0
    sliding_plane: str = "Base/support joint"
    # No bond credit unless the case's uncracked fraction is entered (F-045).
    uncracked_area_fraction: float = 0.0
    vertical_bars_developed_at_base: bool = True
    developed_vertical_bar_count: int | None = None
    in_situ_grout_strength: float = 20.0
    # Least clear dimension of the reinforced cell (mm): declared, no default.
    # Required for reinforced walls and bond beams; it is one cell with the
    # grouted-cell width and lies inside the hollow core t - 2 tf.
    least_clear_cell_dimension: float | None = None
    epoxy_coated_vertical_bars: bool = False
    available_vertical_development: float = 1200.0
    available_horizontal_development: float = 600.0
    splice_present: bool = False
    splice_class: str = "B"
    class_a_splice_confirmed: bool = False
    provided_lap_length: float | None = None
    vertical_bars_continuous: bool = True
    available_termination_extension: float = 5000.0
    joint_wire_diameter: float = 3.65
    mortar_joint_thickness: float = 10.0
    base_connection_confirmed: bool = False
    top_connection_confirmed: bool = False
    oop_check_provided: bool = False
    oop_utilization: float = 0.0
    oop_stability_passed: bool = False
    # S304-14 10.10.2.1: dv need not be less than 0.8 lw only for walls with
    # flexural reinforcement distributed along the length. None = not declared:
    # walls with 3 or more bars are read as distributed; two-bar walls use
    # min(0.8 lw, d) with an INCOMPLETE item (register I-8, F-101).
    flexural_reinforcement_distributed: bool | None = None
    # Seismic declarations (plan WP3), set by the case layer. earthquake_case:
    # the case has a non-zero E factor (whatever the E vector), or the case
    # layer keeps the gate for a declared non-SFRS wall. sfrs_wall: True = part
    # of the SFRS, False = declared not part of it, None = undeclared.
    # seismic_exempt_4_1_8_1: NBC 4.1.8.1 simplified procedure with eligibility
    # verified by the case layer (the engine never tests eligibility).
    # urm_seismic_permitted: the declared category / hazard and height permit
    # URM shear walls (S304-14 4.5.1 / 16.2.1; S304:24 5.5.1 / 17.2.1; NBC
    # Table 4.1.8.9). wall_role: one of WALL_ROLES, None = undeclared.
    earthquake_case: bool = False
    sfrs_wall: bool | None = None
    seismic_exempt_4_1_8_1: bool = False
    urm_seismic_permitted: bool = False
    wall_role: str | None = None

    def __post_init__(self) -> None:
        levels = self.bond_beam_levels_above_section
        if isinstance(levels, list):  # keep the frozen input hashable
            object.__setattr__(self, "bond_beam_levels_above_section", tuple(levels))


@dataclass(frozen=True)
class Segment:
    x0: float
    x1: float
    effective_thickness: float
    fm: float
    grouted: bool


@dataclass(frozen=True)
class SectionProperties:
    # face_shell_thickness / face_shell_source: the tf used for the ungrouted
    # segments in this edition (None when the section has none).
    area: float
    gross_area: float
    centroid: float
    inertia: float
    section_modulus: float
    weighted_fm: float
    grout_fraction: float
    grouted_length_fraction: float
    gamma_g: float
    bar_positions: tuple[float, ...]
    bar_spacing: float | None
    fm_ungrouted: float
    fm_grouted: float
    ft: float
    segments: tuple[Segment, ...]
    face_shell_thickness: float | None = None
    face_shell_source: str = ""
    face_shell_declared: bool = True


@dataclass(frozen=True)
class InteractionPoint:
    p: float
    m: float
    c: float | None
    branch: str
    # False where the section model is outside its domain at this neutral
    # axis (e.g. the squat-wall deep-section arm, register I-10).
    valid: bool = True
    # Squat walls (hw/lw < 1): the depth d of the relocated tension resultant
    # (10.2.8 / 11.2.8) and the unclipped reduced-arm moment in kN-m.
    reduced_depth: float | None = None
    reduced_moment: float | None = None
    note: str = ""  # why the point is invalid


@dataclass(frozen=True)
class ShearResult:
    effective_depth: float
    moment_shear_ratio: float
    beta: float | None
    theta_degrees: float | None
    masonry_resistance: float | None
    steel_resistance: float
    uncapped_resistance: float | None
    maximum_resistance: float
    resistance: float | None
    required_horizontal_area: float | None
    minimum_horizontal_area_2024: float | None
    simplified_resistance: float | None
    general_resistance: float | None
    epsilon_x: float | None
    ze: float | None
    method: str
    stack_vertical_resistance: float | None = None
    ze_basis: str = ""
    grout_assumed: bool = False
    epsilon_x_basis: str = ""
    # dv basis (S304-14 10.10.2.1, register I-8); the horizontal area credited
    # in Vs and why (spacing limit, anchorage, layout, development); Vr with
    # no horizontal steel and whether the steel is required for in-plane
    # shear (F-046, F-066, register I-17).
    effective_depth_basis: str = "0.8 lw"
    horizontal_area_credited: float = 0.0
    horizontal_credit_basis: str = ""
    resistance_without_horizontal_steel: float | None = None
    # None: undecided, the no-steel Vr is unknown (e.g. undeclared case Pd).
    horizontal_steel_required: bool | None = True
    flexural_reinforcement_distribution_assumed: bool = False


@dataclass(frozen=True)
class SlidingResult:
    resistance: float | None
    reinforced_route: float | None
    bond_route: float | None
    uncracked_area: float
    required_dowel_area: float | None
    developed_vertical_area: float
    method: str
    # Development factor kdev applied to the S304:24 dowel force (register I-13).
    development_factor: float = 1.0


@dataclass(frozen=True)
class DetailingResult:
    vertical_development_required: float
    horizontal_development_required: float | None
    lap_required: float | None
    termination_extension_required: float
    vertical_clearance_limit: float
    vertical_bar_diameter: float
    horizontal_bar_diameter: float | None
    # Equation texts of the development and lap lengths (F-038, F-081, F-095).
    vertical_development_basis: str = ""
    horizontal_development_basis: str = ""
    lap_basis: str = ""


@dataclass(frozen=True)
class StabilityResult:
    combined_utilization: float | None
    oop_check_provided: bool
    oop_stability_passed: bool


@dataclass(frozen=True)
class Check:
    level: CheckLevel
    title: str
    detail: str
    clause: str
    category: str = "additional"
    check_id: str = ""
    kind: str = "numeric"
    demand: float | None = None
    limit: float | None = None
    units: str = ""
    comparison: str | None = None
    tolerance: float = 0.0
    unresolved: Unresolved = ""


@dataclass(frozen=True)
class TraceEntry:
    variable: str
    value: str
    unit: str
    equation: str
    clause: str


@dataclass(frozen=True)
class WallResult:
    wall: WallInput
    section: SectionProperties
    curve: tuple[InteractionPoint, ...]
    alternate_curve: tuple[InteractionPoint, ...] | None
    axial_cap: float
    tension_cap: float
    moment_resistance: float | None
    alternate_moment_resistance: float | None
    pm_utilization: float | None
    shear: ShearResult
    sliding: SlidingResult
    detailing: DetailingResult
    stability: StabilityResult
    self_weight: float
    pd: float | None
    sliding_resistance: float | None
    sliding_utilization: float | None
    checks: tuple[Check, ...]
    trace: tuple[TraceEntry, ...]
    curve_method: str = "Discrete"
    alternate_curve_method: str | None = None
    pd_basis: str = ""
    # "ok"; "outside_axial_range" (Pf beyond Pr,max or Tr); or
    # "deep_section_model_outside_range" (squat reduced-arm moment negative or
    # a >= d, register I-10).
    moment_resistance_reason: str = "ok"
    # Development factor kdev = min(1, available / ld) of the vertical bars
    # (register I-13); bond-beam course grout included in self_weight (F-042).
    development_factor: float = 1.0
    bond_beam_grout_weight: float = 0.0


class CalculationError(ValueError):
    pass


class MissingWeightDeclaration(CalculationError):
    """The existing geometry inputs can describe the interval, but have not."""


class UnsupportedWeightInterval(CalculationError):
    """The above-section course inventory cannot describe a requested interval."""


@dataclass(frozen=True)
class WallWeightInterval:
    weight: float
    uniform_weight: float
    bond_beam_weight: float
    lower_elevation: float
    upper_elevation: float


def beta_one(fm: float) -> float:
    return 0.8 if fm <= 20.0 else 0.8 - 0.01 * (fm - 20.0)


def _interpolate_strength(
    strength: float,
    values: dict[float, tuple[float, float]],
) -> tuple[float, float]:
    ordered = sorted(values.items())
    if not strength >= ordered[0][0]:
        raise CalculationError("Nominal unit strength is outside the supported Table 4 range.")
    if strength >= ordered[-1][0]:
        return ordered[-1][1]  # Table 4 top row is printed "30 or more" (F-041).
    for (x0, y0), (x1, y1) in zip(ordered, ordered[1:]):
        if x0 <= strength <= x1:
            fraction = (strength - x0) / (x1 - x0)
            return tuple(a + fraction * (b - a) for a, b in zip(y0, y1))  # type: ignore[return-value]
    return ordered[-1][1]


def masonry_strengths(wall: WallInput) -> tuple[float, float]:
    return _interpolate_strength(
        wall.nominal_unit_strength,
        _FM_TABLE[wall.edition][wall.mortar_type],
    )


def _reference_face_shell(kind: str, thickness: float) -> float:
    value = UNIT_FACE_SHELL_REFERENCE[kind].get(float(thickness))
    if value is None:
        raise CalculationError(
            f"No reference {kind.replace('_', '-')} face-shell thickness for t = {thickness:g} mm; "
            "declare the unit's face-shell thicknesses.")
    return value


def _section_tf(wall: WallInput) -> tuple[float, str, bool]:
    """(tf, source, declared) used for ungrouted segments in this edition.

    S304-14 takes the mortar-bedded face shell; S304:24 takes the Clause 3.1
    minimum face-shell thickness. A missing value falls back to the unverified
    reference table and is reported as not declared (register I-14).
    """
    if wall.edition == "S304:24":
        value, kind, label = wall.minimum_face_shell_thickness, "minimum", "S304:24 3.1 minimum face-shell thickness"
    else:
        value, kind, label = wall.face_shell_thickness, "mortar_bedded", "mortar-bedded face-shell thickness"
    if value is not None:
        return value, f"declared {label}", True
    return (_reference_face_shell(kind, wall.thickness),
            f"reference {label} for t = {wall.thickness:g} mm (comparison workbook; unverified, register I-14)", False)


def resolved_face_shell_thickness(wall: WallInput) -> float:
    """Face-shell thickness of this edition's ungrouted section, in mm (F-061)."""
    return _section_tf(wall)[0]


def _bedded_tf(wall: WallInput) -> float:
    """Mortar-bedded face shell: declared, else the reference value (the clear-cell bound only)."""
    if wall.face_shell_thickness is not None:
        return wall.face_shell_thickness
    return _reference_face_shell("mortar_bedded", wall.thickness)


def _reference_bedded_tf(wall: WallInput) -> float | None:
    if wall.face_shell_thickness is not None:
        return wall.face_shell_thickness
    return UNIT_FACE_SHELL_REFERENCE["mortar_bedded"].get(float(wall.thickness))


def _validate_cell(wall: WallInput) -> None:
    """The declared clear cell is one cell inside the unit (F-053, F-152, R06)."""
    needs_cell = wall.wall_type != "Unreinforced masonry" or wall.horizontal_reinforcement_type == "Bond beam bars"
    clear = wall.least_clear_cell_dimension
    if clear is None:
        if needs_cell:
            raise CalculationError("Declare the least clear dimension of the reinforced cell "
                                   "(least_clear_cell_dimension); it has no default.")
        return
    if not math.isfinite(clear) or clear <= 0.0:
        raise CalculationError("Least clear cell dimension must be positive and finite.")
    tf = _reference_bedded_tf(wall)
    if tf is not None and clear > wall.thickness - 2.0 * tf + 1.0e-9:
        raise CalculationError(f"Least clear cell dimension {clear:.1f} mm exceeds the hollow core of the unit, "
                               f"t - 2 tf = {wall.thickness - 2.0 * tf:.1f} mm.")
    if (wall.wall_type == "Partially grouted reinforced masonry"
            and wall.grouted_cell_width < clear - 1.0e-9):
        raise CalculationError(f"Grouted-cell width {wall.grouted_cell_width:.1f} mm is less than the least clear "
                               f"cell dimension {clear:.1f} mm; the two fields describe one cell.")


def _validate(wall: WallInput) -> None:
    positive = {
        "length": wall.length,
        "height": wall.height,
        "thickness": wall.thickness,
        "nominal unit strength": wall.nominal_unit_strength,
        "horizontal reinforcement spacing": wall.horizontal_reinforcement_spacing,
        "steel yield strength": wall.fy,
        "horizontal steel yield strength": wall.horizontal_fy,
        "in-situ grout strength": wall.in_situ_grout_strength,
        "available vertical development": wall.available_vertical_development,
        "available horizontal development": wall.available_horizontal_development,
        "masonry density": wall.masonry_density,
        "grout density": wall.grout_density,
    }
    for name, value in positive.items():
        if not math.isfinite(value) or value <= 0.0:
            raise CalculationError(f"{name.capitalize()} must be positive and finite.")
    for name, value in {
        "factored moment": wall.factored_moment,
        "factored shear": wall.factored_shear,
        "unfactored dead load above wall": wall.unfactored_dead_load_above,
        "horizontal reinforcement area": wall.horizontal_reinforcement_area,
        "OOP utilization": wall.oop_utilization,
        "available termination extension": wall.available_termination_extension,
    }.items():
        if not math.isfinite(value) or value < 0.0:
            raise CalculationError(f"{name.capitalize()} must be non-negative and finite.")
    if not math.isfinite(wall.factored_axial):
        raise CalculationError("Factored axial load must be finite.")
    if wall.dead_load_compression is not None and not math.isfinite(wall.dead_load_compression):
        raise CalculationError("Case Pd must be finite.")
    if wall.edition not in _FM_TABLE:
        raise CalculationError(f"Unsupported edition: {wall.edition}.")
    if wall.mortar_type not in ("Type S", "Type N"):
        raise CalculationError("Choose Type S or Type N mortar.")
    if wall.bond_pattern not in ("Running bond", "Stack pattern"):
        raise CalculationError("Choose running bond or stack pattern.")
    if wall.unreinforced_condition not in _FT_TABLE:
        raise CalculationError("Choose a supported unreinforced masonry condition.")
    if wall.partial_grout_method not in (
        "Discrete",
        "Smeared",
        "Use lower of discrete and smeared",
    ):
        raise CalculationError("Choose a supported partial-grout method.")
    if wall.horizontal_reinforcement_type not in (
        "None",
        "Bond beam bars",
        "Joint reinforcement",
    ):
        raise CalculationError("Choose a supported horizontal reinforcement type.")
    if wall.shear_method not in (
        "Simplified",
        "General",
        "Use lower of simplified and general",
    ):
        raise CalculationError("Choose a supported S304:24 shear method.")
    if wall.grout_type not in (None, "Coarse", "Fine"):
        raise CalculationError("Choose coarse or fine grout.")
    if wall.provided_lap_length is not None and (
        not math.isfinite(wall.provided_lap_length) or wall.provided_lap_length <= 0.0
    ):
        raise CalculationError("Provided lap length must be positive and finite.")
    if wall.sliding_plane not in ("Base/support joint", "Between masonry courses"):
        raise CalculationError("Choose a supported sliding plane.")
    if wall.wall_type not in (
        "Unreinforced masonry",
        "Fully grouted reinforced masonry",
        "Partially grouted reinforced masonry",
    ):
        raise CalculationError(f"Unsupported wall type: {wall.wall_type}.")
    for name, value in (("Mortar-bedded face-shell thickness", wall.face_shell_thickness),
                        ("Minimum face-shell thickness", wall.minimum_face_shell_thickness)):
        if value is not None and (not math.isfinite(value) or value <= 0.0):
            raise CalculationError(f"{name} must be positive and finite.")
        if value is not None and value * 2.0 >= wall.thickness:
            raise CalculationError(f"{name}: two face shells must leave a positive hollow core width.")
    if (wall.face_shell_thickness is not None and wall.minimum_face_shell_thickness is not None
            and wall.minimum_face_shell_thickness > wall.face_shell_thickness + 1.0e-9):
        raise CalculationError("The minimum face-shell thickness cannot exceed the mortar-bedded face-shell thickness.")
    if not 0.0 < wall.sliding_friction <= 1.0:
        raise CalculationError("Sliding friction must be greater than zero and no greater than 1.0.")
    if wall.fy > 400.0:
        raise CalculationError("This release limits reinforcement to fy <= 400 MPa.")
    if wall.bar_size not in BAR_AREAS:
        raise CalculationError(f"Unsupported vertical bar size: {wall.bar_size}.")
    if wall.horizontal_bar_size not in BAR_AREAS:
        raise CalculationError(f"Unsupported horizontal bar size: {wall.horizontal_bar_size}.")
    if wall.horizontal_reinforcement_type == "Bond beam bars" and wall.horizontal_fy > 400.0:
        raise CalculationError("Bond-beam reinforcing bars are limited to fy <= 400 MPa; wire is a separate material route.")
    if type(wall.bar_count) is not int or wall.bar_count < 0:
        raise CalculationError("Vertical-bar count must be a non-negative integer.")
    if wall.developed_vertical_bar_count is not None and type(wall.developed_vertical_bar_count) is not int:
        raise CalculationError("Developed vertical-bar count must be an integer.")
    for name, value in {
        "tie diameter": wall.tie_diameter,
        "tie spacing": wall.tie_spacing,
        "joint-wire diameter": wall.joint_wire_diameter,
        "mortar-joint thickness": wall.mortar_joint_thickness,
    }.items():
        if not math.isfinite(value) or value <= 0.0:
            raise CalculationError(f"{name.capitalize()} must be positive and finite.")
    for name, value in {
        "stack-joint reinforcement area": wall.stack_joint_area,
        "vertical shear at stack joint": wall.stack_vertical_shear,
        "wall height above checked section": wall.height_above_section,
    }.items():
        if value is not None and (not math.isfinite(value) or value < 0.0):
            raise CalculationError(f"{name.capitalize()} must be non-negative and finite.")
    if wall.height_above_section is not None and wall.height_above_section > wall.height:
        raise CalculationError("Height above the checked section cannot exceed the total wall height.")
    if wall.wall_type != "Unreinforced masonry":
        if wall.bar_count < 2:
            raise CalculationError("A reinforced wall requires at least two vertical bars.")
        if not 0.0 < wall.end_bar_distance < wall.length / 2.0:
            raise CalculationError("End-bar distance must lie inside both ends of the wall.")
        diameter = BAR_PROPERTIES[wall.bar_size][1]
        if wall.end_bar_distance < diameter / 2.0:
            raise CalculationError("Vertical bars must fit inside the wall ends.")
        if (wall.length - 2.0 * wall.end_bar_distance) / (wall.bar_count - 1) <= diameter:
            raise CalculationError("Vertical bars overlap at the entered spacing.")
    elif wall.factored_axial < 0.0:
        raise CalculationError("Unreinforced masonry is not credited with concentric axial-tension resistance.")
    if wall.wall_type == "Partially grouted reinforced masonry":
        if not math.isfinite(wall.grouted_cell_width) or wall.grouted_cell_width <= 0.0:
            raise CalculationError("Grouted-cell width must be positive and finite.")
        spacing = (wall.length - 2.0 * wall.end_bar_distance) / (wall.bar_count - 1)
        if wall.grouted_cell_width > spacing + 1.0e-9:
            raise CalculationError("Grouted cells overlap; reduce their width or the bar count.")
    if not 0.0 < wall.unit_solid_fraction <= 1.0:
        raise CalculationError("Unit solid fraction must be greater than zero and no greater than 1.0.")
    if not 0.0 <= wall.uncracked_area_fraction <= 1.0:
        raise CalculationError("Uncracked area fraction must lie between zero and one.")
    if wall.horizontal_reinforcement_type == "None" and wall.horizontal_reinforcement_area > 0.0:
        raise CalculationError("Choose a horizontal reinforcement type when Av is non-zero.")
    if wall.developed_vertical_bar_count is not None and not 0 <= wall.developed_vertical_bar_count <= wall.bar_count:
        raise CalculationError("Developed vertical-bar count must be between zero and the total bar count.")
    if wall.splice_class not in ("A", "B"):
        raise CalculationError("Splice class must be A or B.")
    _validate_declarations(wall)
    _validate_cell(wall)


def _validate_declarations(wall: WallInput) -> None:
    """Bond-beam, joint-wire, dv and seismic declarations (F-042, F-074, F-080, F-095, F-101; WP3)."""
    if wall.bond_beam_anchorage not in (None, *BOND_BEAM_ANCHORAGES):
        raise CalculationError("Bond-beam anchorage must be 'standard_hook' or 'mechanical' (or not declared).")
    n = wall.bars_per_bond_beam
    if n is not None and (type(n) is not int or n < 1):
        raise CalculationError("Bars per bond beam must be an integer of at least 1.")
    for name, value in (("Bond-beam bar clear spacing", wall.bond_beam_bar_clear_spacing),
                        ("Bond-beam course height", wall.bond_beam_course_height),
                        ("Joint-reinforcement f'gr", wall.joint_reinforcement_fgr)):
        if value is not None and (not math.isfinite(value) or value <= 0.0):
            raise CalculationError(f"{name} must be positive and finite.")
    bond_beams = wall.horizontal_reinforcement_type == "Bond beam bars" and wall.horizontal_reinforcement_area > 0.0
    if bond_beams and n is not None:
        bar_area = BAR_AREAS[wall.horizontal_bar_size]
        if abs(wall.horizontal_reinforcement_area - n * bar_area) > 1.0e-6 * n * bar_area:
            raise CalculationError(
                f"Av at each level = {wall.horizontal_reinforcement_area:.1f} mm2 does not equal bars per bond beam x Ab "
                f"= {n} x {bar_area:.0f} = {n * bar_area:.1f} mm2.")
    validate_bond_beam_courses(wall)
    if wall.flexural_reinforcement_distributed not in (None, True, False):
        raise CalculationError("flexural_reinforcement_distributed must be true, false or not declared.")
    if wall.sfrs_wall not in (None, True, False):
        raise CalculationError("sfrs_wall must be true, false or not declared.")
    if wall.wall_role is not None and wall.wall_role not in WALL_ROLES:
        raise CalculationError(f"Wall role must be one of {', '.join(WALL_ROLES)}.")


def validate_bond_beam_courses(wall: WallInput) -> None:
    """Reject contradictory course inventories without changing their order."""
    levels, course = wall.bond_beam_levels_above_section, wall.bond_beam_course_height
    if course is not None and (isinstance(course, bool) or not isinstance(course, (int, float))
                               or not math.isfinite(course) or course <= 0.0):
        raise CalculationError("Bond-beam course height must be positive and finite.")
    if levels is not None:
        if isinstance(levels, bool) or not isinstance(levels, (int, tuple)):
            raise CalculationError("Bond-beam levels above the section: a course count or a list of elevations (mm).")
        if isinstance(levels, int):
            if levels < 0:
                raise CalculationError("Bond-beam course count must be non-negative.")
            height_above = wall.height if wall.height_above_section is None else wall.height_above_section
            if (wall.bond_beam_course_height is not None
                    and levels * wall.bond_beam_course_height > height_above + 1.0e-9):
                raise CalculationError(
                    f"{levels} bond-beam courses of {wall.bond_beam_course_height:g} mm exceed the "
                    f"{height_above:g} mm of wall above the checked section.")
        elif not all(isinstance(z, (int, float)) and not isinstance(z, bool) and math.isfinite(z) for z in levels):
            raise CalculationError("Bond-beam course elevations must be finite numbers (mm above the checked section).")
        else:
            ordered = sorted(enumerate(levels), key=lambda item: item[1])
            for (index_a, a), (index_b, b) in zip(ordered, ordered[1:]):
                if b - a <= 1.0e-9:
                    raise CalculationError(f"Bond-beam courses {index_a + 1} and {index_b + 1} have duplicate "
                                           f"elevations ({a:g}, {b:g} mm).")
                if course is not None and b < a + course - 1.0e-9:
                    raise CalculationError(f"Bond-beam courses {index_a + 1} and {index_b + 1} overlap: "
                                           f"[{a:g}, {a + course:g}] and [{b:g}, {b + course:g}] mm.")


def _bar_positions(wall: WallInput) -> tuple[float, ...]:
    if wall.wall_type == "Unreinforced masonry":
        return ()
    spacing = (wall.length - 2.0 * wall.end_bar_distance) / (wall.bar_count - 1)
    return tuple(wall.end_bar_distance + i * spacing for i in range(wall.bar_count))


def _partial_segments(
    wall: WallInput,
    fm_ungrouted: float,
    fm_grouted: float,
    positions: tuple[float, ...],
    face_shell: float,
) -> tuple[Segment, ...]:
    half = wall.grouted_cell_width / 2.0
    intervals = [
        (max(0.0, x - half), min(wall.length, x + half))
        for x in positions
    ]
    boundaries = sorted({0.0, wall.length, *(v for pair in intervals for v in pair)})
    segments: list[Segment] = []
    for x0, x1 in zip(boundaries, boundaries[1:]):
        midpoint = (x0 + x1) / 2.0
        grouted = any(a <= midpoint <= b for a, b in intervals)
        segments.append(
            Segment(
                x0,
                x1,
                wall.thickness if grouted else 2.0 * face_shell,
                fm_grouted if grouted else fm_ungrouted,
                grouted,
            )
        )
    return tuple(segments)


def section_properties(wall: WallInput, method: str | None = None) -> SectionProperties:
    _validate(wall)
    fm_ungrouted, fm_grouted = masonry_strengths(wall)
    positions = _bar_positions(wall)
    uses_face_shell = wall.wall_type == "Partially grouted reinforced masonry" or (
        wall.wall_type == "Unreinforced masonry" and wall.unreinforced_condition == "Hollow ungrouted")
    tf, tf_source, tf_declared = _section_tf(wall) if uses_face_shell else (None, "", True)
    if wall.wall_type == "Fully grouted reinforced masonry":
        discrete_segments = (Segment(0.0, wall.length, wall.thickness, fm_grouted, True),)
    elif wall.wall_type == "Partially grouted reinforced masonry":
        discrete_segments = _partial_segments(wall, fm_ungrouted, fm_grouted, positions, tf)
        if all(s.grouted for s in discrete_segments):
            tf, tf_source, tf_declared = None, "", True  # cells fill the length: no face shell in Ae
    else:
        if wall.unreinforced_condition == "Hollow ungrouted":
            discrete_segments = (
                Segment(0.0, wall.length, 2.0 * tf, fm_ungrouted, False),
            )
        else:
            discrete_segments = (
                Segment(0.0, wall.length, wall.thickness, fm_grouted, True),
            )

    discrete_area = sum(
        (s.x1 - s.x0) * s.effective_thickness for s in discrete_segments
    )
    discrete_weighted_fm = sum(
        s.fm * (s.x1 - s.x0) * s.effective_thickness for s in discrete_segments
    ) / discrete_area
    selected_method = method or (
        "Smeared" if wall.partial_grout_method == "Smeared" else "Discrete"
    )
    if wall.wall_type == "Partially grouted reinforced masonry" and selected_method == "Smeared":
        segments = (
            Segment(
                0.0,
                wall.length,
                discrete_area / wall.length,
                discrete_weighted_fm,
                False,
            ),
        )
    else:
        segments = discrete_segments

    area = sum((s.x1 - s.x0) * s.effective_thickness for s in segments)
    weighted_fm = sum(
        s.fm * (s.x1 - s.x0) * s.effective_thickness for s in segments
    ) / area
    centroid = sum(
        (s.x0 + s.x1) / 2.0 * (s.x1 - s.x0) * s.effective_thickness
        for s in segments
    ) / area
    inertia = sum(
        s.effective_thickness
        * (
            (s.x1 - s.x0) ** 3 / 12.0
            + (s.x1 - s.x0) * ((s.x0 + s.x1) / 2.0 - centroid) ** 2
        )
        for s in segments
    )
    section_modulus = inertia / max(centroid, wall.length - centroid)
    grouted_area = sum(
        (s.x1 - s.x0) * s.effective_thickness for s in discrete_segments if s.grouted
    )
    grouted_length = sum(
        s.x1 - s.x0 for s in discrete_segments if s.grouted
    )
    gross_area = wall.length * wall.thickness
    grout_fraction = grouted_area / area
    fully_grouted = grouted_length >= wall.length - 1.0e-8
    gamma_g = 1.0 if fully_grouted else min(area / gross_area, 0.5)
    bar_spacing = (
        (wall.length - 2.0 * wall.end_bar_distance) / (wall.bar_count - 1)
        if positions
        else None
    )
    return SectionProperties(
        area=area,
        gross_area=gross_area,
        centroid=centroid,
        inertia=inertia,
        section_modulus=section_modulus,
        weighted_fm=weighted_fm,
        grout_fraction=grout_fraction,
        grouted_length_fraction=grouted_length / wall.length,
        gamma_g=gamma_g,
        bar_positions=positions,
        bar_spacing=bar_spacing,
        fm_ungrouted=fm_ungrouted,
        fm_grouted=fm_grouted,
        ft=0.1 if wall.free_standing else _FT_TABLE[wall.unreinforced_condition][wall.mortar_type],
        segments=segments,
        face_shell_thickness=tf,
        face_shell_source=tf_source,
        face_shell_declared=tf_declared,
    )


def _ties_qualify(wall: WallInput) -> bool:
    return (
        wall.compression_reinforcement_tied
        and wall.tie_arrangement_confirmed
        and wall.tie_diameter >= 3.65
        and wall.tie_spacing <= min(16.0 * BAR_PROPERTIES[wall.bar_size][1], 48.0 * wall.tie_diameter, wall.thickness)
    )


def ties_qualify(wall: WallInput) -> bool:
    """Public tie-qualification result shared with the out-of-plane adapter.

    Compression reinforcement is credited only when the tie size, spacing and
    arrangement confirmation satisfy Clause 13.2 (S304-14 Clause 12.2).
    """

    return _ties_qualify(wall)


def _axial_cap(wall: WallInput, section: SectionProperties) -> float:
    compression = sum(
        0.85 * PHI_M * s.fm * (s.x1 - s.x0) * s.effective_thickness
        for s in section.segments
    )
    if _ties_qualify(wall) and section.bar_positions:
        steel_area = len(section.bar_positions) * BAR_AREAS[wall.bar_size]
        compression += steel_area * (
            PHI_S * wall.fy - 0.85 * PHI_M * section.fm_grouted
        )
    return 0.80 * compression / 1000.0


def _development_factor(wall: WallInput, section: SectionProperties) -> float:
    """kdev = min(1, available / ld) of the vertical bars at the critical section.

    A bar developed over less than ld is relied on only for the stress it can
    develop, kdev fy, in Mr, Tr and the S304:24 dowel force (register I-13,
    F-047; proportional rule of 12.5.4.4(b) / 13.5.4.4(b) and 11.10.2.6.3(c)).
    """
    if not section.bar_positions:
        return 1.0
    ld = _development_length(wall, section, horizontal=False)
    if ld is None or ld <= 0.0:
        return 1.0
    return min(1.0, wall.available_vertical_development / ld)


def _tension_cap(wall: WallInput, section: SectionProperties) -> float:
    return (
        PHI_S
        * len(section.bar_positions)
        * BAR_AREAS[wall.bar_size]
        * _development_factor(wall, section)
        * wall.fy
        / 1000.0
    )


def _bond_beam_grout(wall: WallInput, section: SectionProperties) -> tuple[float, str | None, str]:
    """(kN, status, text) of the bond-beam course grout above the checked section (F-042).

    A bond beam is a grouted course (S304-14 2.2; S304:24 3.1); each declared
    course adds grout over the length not already grouted by vertical cells:
    (1 - s_f)(1 - glf) t lw h_course rho_g g, truncated at the checked section
    and at the top of the wall. status: None (not applicable), "declared" or
    "undeclared" (no default course height: the grout is not added).
    """
    if (wall.horizontal_reinforcement_type != "Bond beam bars" or wall.horizontal_reinforcement_area <= 0.0
            or (wall.wall_type == "Unreinforced masonry" and wall.unreinforced_condition == "Solid units")
            or wall.unit_solid_fraction == 1.0 or section.grouted_length_fraction >= 1.0 - 1.0e-8):
        return 0.0, None, ""
    levels, course = wall.bond_beam_levels_above_section, wall.bond_beam_course_height
    if levels is None or course is None:
        return 0.0, "undeclared", ("bond beams on a wall with ungrouted cells: declare the bond-beam courses above the "
                                   "section (bond_beam_levels_above_section) and their course height "
                                   "(bond_beam_course_height); their grout is not included")
    height = wall.height if wall.height_above_section is None else wall.height_above_section
    if isinstance(levels, int):
        grouted_height = min(levels * course, height)
        layout = f"{levels} courses x {course:g} mm"
    else:
        grouted_height = sum(max(0.0, min(z + course, height) - max(z, 0.0)) for z in levels)
        layout = f"{len(levels)} courses of {course:g} mm at {', '.join(f'{z:g}' for z in levels)} mm above the section"
    volume = (1.0 - wall.unit_solid_fraction) * (1.0 - section.grouted_length_fraction) * wall.thickness \
        * wall.length * grouted_height * 1.0e-9
    weight = volume * wall.grout_density * 9.81 / 1000.0
    return weight, "declared", (f"bond-beam grout {weight:.3f} kN = (1 - s_f)(1 - glf) t lw h rho_g g over "
                                f"{grouted_height:.0f} mm of courses ({layout}; truncated at the section and the top)")


def _wall_self_weight(wall: WallInput, section: SectionProperties) -> float:
    height = wall.height if wall.height_above_section is None else wall.height_above_section
    gross_volume = wall.length * height * wall.thickness * 1.0e-9
    if wall.wall_type == "Unreinforced masonry" and wall.unreinforced_condition == "Solid units":
        mass = gross_volume * wall.masonry_density
    else:
        unit_mass = gross_volume * wall.unit_solid_fraction * wall.masonry_density
        grout_mass = (
            gross_volume
            * (1.0 - wall.unit_solid_fraction)
            * section.grouted_length_fraction
            * wall.grout_density
        )
        mass = unit_mass + grout_mass
    return mass * 9.81 / 1000.0 + _bond_beam_grout(wall, section)[0]


def wall_self_weight(wall: WallInput) -> float:
    """Physical wall weight above the checked section, in kN, including the
    grout of the declared bond-beam courses (F-042)."""
    return _wall_self_weight(wall, section_properties(wall, "Discrete"))


def wall_weight_between(wall: WallInput, lower_elevation: float, upper_elevation: float) -> WallWeightInterval:
    """Exact physical weight (kN) between absolute elevations in the wall.

    Course elevations remain relative to the checked IP section. The existing
    inventory says nothing about courses below that section. A count describes
    the entire above-section total, but not a proper subinterval of it.
    """
    if any(isinstance(z, bool) or not isinstance(z, (int, float)) or not math.isfinite(z)
           for z in (lower_elevation, upper_elevation)):
        raise CalculationError("Weight interval elevations must be finite numbers.")
    if lower_elevation < -1.0e-9 or upper_elevation > wall.height + 1.0e-9 or lower_elevation > upper_elevation:
        raise CalculationError("Weight interval must be ordered and lie within the total wall height.")
    low, high = max(0.0, lower_elevation), min(wall.height, upper_elevation)
    section = section_properties(wall, "Discrete")
    if high - low <= 1.0e-9:
        return WallWeightInterval(0.0, 0.0, 0.0, low, high)
    solid = wall.wall_type == "Unreinforced masonry" and wall.unreinforced_condition == "Solid units"
    density = (wall.masonry_density if solid else wall.unit_solid_fraction * wall.masonry_density
               + (1.0 - wall.unit_solid_fraction) * section.grouted_length_fraction * wall.grout_density)
    volume_per_mm = wall.length * wall.thickness * 1.0e-9
    uniform = volume_per_mm * (high - low) * density * 9.81 / 1000.0
    supplemental = (wall.horizontal_reinforcement_type == "Bond beam bars"
                    and wall.horizontal_reinforcement_area > 0.0 and not solid
                    and wall.unit_solid_fraction < 1.0 and section.grouted_length_fraction < 1.0 - 1.0e-8)
    if not supplemental:
        return WallWeightInterval(uniform, uniform, 0.0, low, high)
    height_above = wall.height if wall.height_above_section is None else wall.height_above_section
    section_elevation = wall.height - height_above
    if low < section_elevation - 1.0e-9:
        raise UnsupportedWeightInterval("The bond-beam inventory describes only courses above the checked IP "
                                        "section; weight below that section cannot be reconstructed.")
    levels, course = wall.bond_beam_levels_above_section, wall.bond_beam_course_height
    if levels == 0 or levels == ():
        grouted_height = 0.0
    elif levels is None or course is None:
        raise MissingWeightDeclaration("Declare bond_beam_levels_above_section and bond_beam_course_height "
                                       "to calculate weight between the IP section and OOP station.")
    elif isinstance(levels, int):
        if abs(low - section_elevation) > 1.0e-9 or abs(high - wall.height) > 1.0e-9:
            raise MissingWeightDeclaration("A bond-beam course count cannot locate grout within the wall; "
                                           "supply course elevations or a direct p_station.")
        grouted_height = min(levels * course, height_above)
    else:
        grouted_height = sum(max(0.0, min(section_elevation + z + course, high)
                                  - max(section_elevation + z, low)) for z in levels)
    grout = volume_per_mm * (1.0 - wall.unit_solid_fraction) * (1.0 - section.grouted_length_fraction) \
        * grouted_height * wall.grout_density * 9.81 / 1000.0
    return WallWeightInterval(uniform + grout, uniform, grout, low, high)


PD_REQUIRED = ("case Pd required: 0.9 x counteracting dead load plus the signed lateral axial load of "
               "this case (S304-14 2.3 / 10.10.2.1; S304:24 Clause 3); not supplied, so this check is "
               "not evaluated (register I-2)")


def _pd(wall: WallInput) -> tuple[float | None, bool, str]:
    """(signed case Pd in kN or None, supplied, basis). No fallback: the engine
    cannot know the case's lateral axial load (register I-2)."""
    if wall.dead_load_compression is None:
        return None, False, "not supplied"
    return wall.dead_load_compression, True, wall.pd_basis or "supplied case Pd"


@dataclass(frozen=True)
class DevelopmentLength:
    """One tension development length with its equation terms (F-038, F-074, F-095)."""

    length: float  # ld, mm, not less than 300 mm (12.4.2.1 / 13.4.2.1)
    formula: float  # 12.4.2.3 / 13.4.2.3 value
    table: float | None  # 12.4.2.4 / 13.4.2.4 value; None when its preconditions fail
    dcs: float  # before the (dcs + Ktr) <= 2.5 db cap
    basis: str


def _edition_clause(wall: WallInput, s304_14: str, s304_24: str) -> str:
    return s304_14 if wall.edition == "S304-14" else s304_24


def _ld(
    wall: WallInput,
    *,
    area: float,
    diameter: float,
    fy: float,
    fgr: float,
    dcs: float,
    dcs_text: str,
    clear_cover: float | None,
    clear_spacing: float | None,
    k1: float,
    k2: float,
    k3: float,
) -> DevelopmentLength:
    """ld = max(min(equation, table), 300 mm), Ktr = 0 (Av alone does not
    establish Atr or n across a bond-splitting plane)."""
    k12 = min(k1 * k2, 1.7)
    term = min(dcs, 2.5 * diameter)
    formula = 1.15 * k12 * k3 * fy * area / (term * math.sqrt(fgr))
    table: float | None = None
    clause = _edition_clause(wall, "12.4.2.4", "13.4.2.4 / Table 10")
    if clear_cover is None or clear_spacing is None:
        table_text = f"{clause} table route not used (cover and spacing not known)"
    elif clear_cover >= diameter - 1.0e-9 and clear_spacing >= 1.4 * diameter - 1.0e-9:
        coefficient = (TABLE_LD_COEFFICIENT[wall.edition] if clear_spacing >= 2.0 * diameter - 1.0e-9
                       else TABLE_LD_OTHER_CASES)
        table = coefficient * k12 * k3 * fy * diameter / math.sqrt(fgr)
        table_text = f"{clause} table {coefficient:g} k1k2k3 fy db / sqrt(f'gr) = {table:.1f} mm"
    else:
        table_text = (f"{clause} table route not permitted (clear cover {clear_cover:.1f} mm < db or clear spacing "
                      f"{clear_spacing:.1f} mm < 1.4 db)")
    length = max(formula if table is None else min(formula, table), 300.0)
    basis = (f"ld = max(min(1.15 k1k2k3 fy Ab / ((dcs + Ktr) sqrt(f'gr)) = {formula:.1f} mm, {table_text}), 300) "
             f"= {length:.1f} mm; dcs = {dcs:.2f} mm ({dcs_text}); (dcs + Ktr) = {term:.2f} mm (Ktr = 0, cap 2.5 db); "
             f"k1 = {k1:g}, k2 = {k2:g}, k3 = {k3:g}; f'gr = {fgr:g} MPa")
    return DevelopmentLength(length, formula, table, dcs, basis)


def _vertical_ld(wall: WallInput, section: SectionProperties) -> DevelopmentLength | None:
    if not section.bar_positions:
        return None
    area, diameter = BAR_PROPERTIES[wall.bar_size]
    spacing = section.bar_spacing or wall.length
    if wall.edition == "S304:24":
        # Clause 3 dcs: distance from the outside masonry surface (the wall face
        # for the centred bar, or the wall end) to the bar centre, and 2s/3.
        face = min(wall.thickness / 2.0, wall.end_bar_distance)
        dcs = min(face, 2.0 * spacing / 3.0)
        cover = face - diameter / 2.0
        text = ("S304:24 3.1: outside masonry surface, min(t/2 for the centred bar, end distance e), and 2s/3; "
                "clear cover to the same surface (register I-12)")
    else:
        cell = wall.least_clear_cell_dimension
        face = min(cell / 2.0, wall.thickness / 2.0, wall.end_bar_distance)
        dcs = min(face, 2.0 * spacing / 3.0)
        cover = face - diameter / 2.0
        text = ("S304-14 12.4.2.3 'closest masonry surface' read as the grouted-cell wall: min(least clear cell / 2, "
                "t/2, e, 2s/3); clear cover to the nearest of those physical surfaces "
                "(provisional, register I-12)")
    clear_spacing = spacing - diameter
    k2 = 1.0
    if wall.epoxy_coated_vertical_bars:
        k2 = 1.5 if cover < 3.0 * diameter or clear_spacing < 6.0 * diameter else 1.2
    k3 = 1.0 if wall.bar_size == "25M" else 0.8
    return _ld(wall, area=area, diameter=diameter, fy=wall.fy, fgr=wall.in_situ_grout_strength, dcs=dcs,
               dcs_text=text, clear_cover=cover, clear_spacing=clear_spacing, k1=1.0, k2=k2, k3=k3)


def _bond_beam_layout(wall: WallInput) -> tuple[int | None, float | None, str]:
    """(bars per bond beam, c/c spacing of the bars being developed, text) (F-074).

    One bar per level develops against the level pitch; two or more bars in
    one bond beam develop against their own c/c spacing inside the beam
    (dcs(b) = 2/3 of it). Course spacing is never used as bar spacing for 2+
    bars; without the declaration the spacing is None (not credited).
    """
    area, diameter = BAR_PROPERTIES[wall.horizontal_bar_size]
    count = wall.bars_per_bond_beam
    if count is None:
        if wall.horizontal_reinforcement_area > area * (1.0 + 1.0e-6):
            return None, None, (f"Av = {wall.horizontal_reinforcement_area:.1f} mm2 > Ab = {area:.0f} mm2 per level and "
                                "bars per bond beam not declared: the spacing of the bars inside the beam is unknown")
        count = 1
    if count == 1:
        return 1, wall.horizontal_reinforcement_spacing, "one bar per bond beam: s = bond-beam level pitch"
    if wall.bond_beam_bar_clear_spacing is None:
        return count, None, (f"{count} bars per bond beam and their clear spacing inside the beam not declared "
                             "(bond_beam_bar_clear_spacing)")
    pitch = wall.bond_beam_bar_clear_spacing + diameter
    return count, pitch, (f"{count} bars per bond beam at {pitch:.1f} mm c/c (clear spacing "
                          f"{wall.bond_beam_bar_clear_spacing:g} mm + db); bars taken as centred in the wall")


def _bond_beam_ld(wall: WallInput) -> DevelopmentLength | None:
    if wall.horizontal_reinforcement_type != "Bond beam bars" or wall.horizontal_reinforcement_area <= 0.0:
        return None
    _, spacing, layout = _bond_beam_layout(wall)
    if spacing is None:
        return None
    area, diameter = BAR_PROPERTIES[wall.horizontal_bar_size]
    if wall.edition == "S304:24":
        dcs = min(wall.thickness / 2.0, 2.0 * spacing / 3.0)
        cover = wall.thickness / 2.0 - diameter / 2.0
        text = f"S304:24 3.1: outside masonry surface t/2 and 2s/3; {layout} (register I-12)"
    else:
        cell = wall.least_clear_cell_dimension
        dcs = min(cell / 2.0, wall.thickness / 2.0, 2.0 * spacing / 3.0)
        cover = (cell - diameter) / 2.0
        text = (f"S304-14 grouted-cell wall reading: min(least clear cell / 2, t/2, 2s/3); {layout} "
                "(provisional, register I-12)")
    k1 = 1.3 if wall.horizontal_top_bar else 1.0
    k3 = 1.0 if wall.horizontal_bar_size == "25M" else 0.8
    return _ld(wall, area=area, diameter=diameter, fy=wall.horizontal_fy, fgr=wall.in_situ_grout_strength, dcs=dcs,
               dcs_text=text, clear_cover=cover, clear_spacing=spacing - diameter, k1=k1, k2=1.0, k3=k3)


def _joint_wire_fgr(wall: WallInput) -> float | None:
    """f'gr for wire in mortar: 10 MPa for Type S (12.4.1.3 / 13.4.1.3), else declared."""
    return JOINT_WIRE_MORTAR_FGR if wall.mortar_type == "Type S" else wall.joint_reinforcement_fgr


def _joint_wire_ld(wall: WallInput) -> DevelopmentLength | None:
    """Deformed joint wire in mortar (F-095): Ab = pi d^2 / 4, (dcs + Ktr) at the
    2.5 db cap (the wire cover in the bed joint is not an input), k3 = 0.8."""
    if wall.horizontal_reinforcement_type != "Joint reinforcement" or wall.horizontal_reinforcement_area <= 0.0:
        return None
    fgr = _joint_wire_fgr(wall)
    if fgr is None:
        return None
    diameter = wall.joint_wire_diameter
    source = ("Type S mortar, 12.4.1.3" if wall.edition == "S304-14" else "Type S mortar, 13.4.1.3") \
        if wall.mortar_type == "Type S" else "declared in-mortar value for Type N mortar"
    return _ld(wall, area=math.pi * diameter ** 2 / 4.0, diameter=diameter, fy=wall.horizontal_fy, fgr=fgr,
               dcs=2.5 * diameter, dcs_text=f"wire in the bed joint: (dcs + Ktr) taken at the 2.5 db cap; f'gr from {source}",
               clear_cover=None, clear_spacing=None, k1=1.0, k2=1.0, k3=0.8)


def _development_length(
    wall: WallInput,
    section: SectionProperties,
    *,
    horizontal: bool,
) -> float | None:
    """ld in mm of the vertical bars, or of the horizontal steel (bond-beam bars
    or joint wire); None when not applicable or not computable."""
    if not horizontal:
        result = _vertical_ld(wall, section)
    elif wall.horizontal_reinforcement_type == "Bond beam bars":
        result = _bond_beam_ld(wall)
    else:
        result = _joint_wire_ld(wall)
    return None if result is None else result.length


def _detailing_result(
    wall: WallInput,
    section: SectionProperties,
) -> DetailingResult:
    vertical = _vertical_ld(wall, section)
    vertical_ld = vertical.length if vertical is not None else 0.0
    if wall.horizontal_reinforcement_type == "Bond beam bars":
        horizontal = _bond_beam_ld(wall)
    else:
        horizontal = _joint_wire_ld(wall)
    _, vertical_diameter = BAR_PROPERTIES[wall.bar_size]
    lap_required = None
    lap_basis = ""
    if wall.splice_present:
        # Class A length (1.0 ld) only when its eligibility is confirmed;
        # otherwise the Class B length 1.3 ld.
        class_a = wall.splice_class == "A" and wall.class_a_splice_confirmed
        tension_lap = max(300.0, vertical_ld * (1.0 if class_a else 1.3))
        lap_required = tension_lap
        lap_basis = (f"tension lap max(300, {'1.0' if class_a else '1.3'} ld) = {tension_lap:.1f} mm "
                     f"({_edition_clause(wall, '12.5.4.2', '13.5.4.2')})")
        if _ties_qualify(wall):
            # Credited tied compression steel: the lap is also a compression lap
            # (12.5.5.1 / 13.5.5.1); the 0.83 tie factor of 12.5.5.4 is not applied.
            factor = 0.073 * wall.fy if wall.fy <= 400.0 else 0.133 * wall.fy - 24.0
            compression_lap = max(factor * vertical_diameter, 300.0)
            lap_required = max(tension_lap, compression_lap)
            governing = "compression lap governs" if compression_lap > tension_lap else "tension lap governs"
            lap_basis += (f"; credited tied compression steel: compression lap max("
                          f"{'0.073 fy' if wall.fy <= 400.0 else '(0.133 fy - 24)'} db = {factor * vertical_diameter:.1f}, "
                          f"300) = {compression_lap:.1f} mm ({_edition_clause(wall, '12.5.5.1', '13.5.5.1')}; "
                          f"0.83 tie factor not applied); {governing}")
    horizontal_diameter = None
    if wall.horizontal_reinforcement_type == "Joint reinforcement":
        horizontal_diameter = wall.joint_wire_diameter
    elif wall.horizontal_reinforcement_type == "Bond beam bars" and wall.horizontal_reinforcement_area > 0.0:
        horizontal_diameter = BAR_PROPERTIES[wall.horizontal_bar_size][1]
    extreme_depth = max(section.bar_positions) if section.bar_positions else 0.0
    return DetailingResult(
        vertical_development_required=vertical_ld,
        horizontal_development_required=None if horizontal is None else horizontal.length,
        lap_required=lap_required,
        termination_extension_required=max(extreme_depth, 12.0 * vertical_diameter),
        vertical_clearance_limit=(wall.least_clear_cell_dimension or 0.0) / 2.0,
        vertical_bar_diameter=vertical_diameter,
        horizontal_bar_diameter=horizontal_diameter,
        vertical_development_basis="" if vertical is None else vertical.basis,
        horizontal_development_basis="" if horizontal is None else horizontal.basis,
        lap_basis=lap_basis,
    )


def _horizontal_spacing_limit(wall: WallInput) -> float:
    """Maximum spacing of horizontal shear reinforcement (S304-14 10.15.1.4(b):
    bond beams min(2400, 0.5 lw); S304:24 11.15.1.4 b): also 0.5 hw; joint
    reinforcement 600 mm)."""
    if wall.horizontal_reinforcement_type == "Bond beam bars":
        limit = min(2400.0, 0.5 * wall.length)
        if wall.edition == "S304:24":
            limit = min(limit, 0.5 * wall.height)
        return limit
    if wall.horizontal_reinforcement_type == "Joint reinforcement":
        return 600.0
    return math.inf


def _horizontal_credit(wall: WallInput, section: SectionProperties) -> tuple[float, str]:
    """(Av credited in Vs, basis). Steel spaced beyond the 10.15.1.4 / 11.15.1.4
    limit is not qualifying shear reinforcement (register I-17, F-046); bond-beam
    bars need a declared end anchorage permitted for their size (F-080), a
    known layout (F-074) and their development length; joint wire needs its
    development length in mortar (F-095)."""
    kind = wall.horizontal_reinforcement_type
    area = wall.horizontal_reinforcement_area
    if kind == "None" or area <= 0.0:
        return 0.0, ""
    reasons: list[str] = []
    limit = _horizontal_spacing_limit(wall)
    if wall.horizontal_reinforcement_spacing > limit + 1.0e-8:
        reasons.append(f"s = {wall.horizontal_reinforcement_spacing:.0f} mm exceeds the "
                       f"{_edition_clause(wall, '10.15.1.4(b)', '11.15.1.4 b)')} limit {limit:.0f} mm (register I-17)")
    if not wall.horizontal_continuity_confirmed:
        reasons.append("continuity not confirmed")
    if kind == "Bond beam bars":
        size = wall.horizontal_bar_size
        bar_area, diameter = BAR_PROPERTIES[size]
        if wall.bond_beam_anchorage is None:
            reasons.append("end anchorage not declared (bond_beam_anchorage)")
        elif wall.bond_beam_anchorage == "standard_hook" and size not in HOOK_ANCHORED_BAR_SIZES:
            reasons.append(f"a standard stirrup hook anchors 15M and smaller bars only; {size} needs mechanical "
                           f"anchorage ({_edition_clause(wall, '12.4.11.2(b)', '13.4.11.2 b)')})")
        ld = _bond_beam_ld(wall)
        if ld is None:
            reasons.append(_bond_beam_layout(wall)[2])
        elif wall.available_horizontal_development < ld.length:
            reasons.append(f"available development {wall.available_horizontal_development:.0f} mm < ld "
                           f"{ld.length:.1f} mm ({_edition_clause(wall, '12.4.1.1', '13.4.1.1')})")
        if area < bar_area:
            reasons.append(f"Av = {area:.1f} mm2 is less than one {size} bar")
        if diameter > wall.least_clear_cell_dimension / 2.0:
            reasons.append("bar does not fit the bond-beam space")
    elif kind == "Joint reinforcement":
        if not wall.horizontal_anchorage_confirmed:
            reasons.append("end anchorage (mechanical, developing fy) not confirmed")
        if not 3.0 <= wall.joint_wire_diameter <= min(5.0, wall.mortar_joint_thickness / 2.0):
            reasons.append("wire diameter outside the joint-reinforcement limits")
        ld = _joint_wire_ld(wall)
        if ld is None:
            reasons.append(f"Type N mortar: in-mortar f'gr not declared (joint_reinforcement_fgr; "
                           f"{_edition_clause(wall, '12.4.1.3', '13.4.1.3')})")
        elif wall.available_horizontal_development < ld.length:
            reasons.append(f"available development {wall.available_horizontal_development:.0f} mm < ld "
                           f"{ld.length:.1f} mm")
    else:
        return 0.0, ""
    if reasons:
        return 0.0, "Vs not credited: " + "; ".join(reasons)
    return area, f"Av = {area:.1f} mm2 credited at s = {wall.horizontal_reinforcement_spacing:.0f} mm"


def _horizontal_area_credited(wall: WallInput, section: SectionProperties) -> float:
    return _horizontal_credit(wall, section)[0]


def section_point_at_c(
    wall: WallInput,
    neutral_axis: float,
    section: SectionProperties | None = None,
    *,
    development_factor: float | None = None,
) -> InteractionPoint:
    """Return the strain-compatible strong-axis resistance at one neutral axis.

    Tension bars are stressed to at most kdev fy (register I-13, F-047). For
    squat walls the tension resultant is moved to d (10.2.8 / 11.2.8, the
    engine's deep-section reading, register I-10); the point is flagged
    invalid when that reduced-arm moment is negative or a = beta1 c >= d.
    No eccentricity cap applies to reinforced sections (F-063).
    """

    section = section or section_properties(wall)
    if neutral_axis <= 0.0 or not math.isfinite(neutral_axis):
        raise CalculationError("Neutral-axis depth must be positive and finite.")
    kdev = _development_factor(wall, section) if development_factor is None else development_factor

    compression_force = 0.0
    moment_nmm = 0.0
    for segment in section.segments:
        block_end = min(segment.x1, beta_one(segment.fm) * neutral_axis)
        if block_end <= segment.x0:
            continue
        width = block_end - segment.x0
        force = 0.85 * PHI_M * segment.fm * segment.effective_thickness * width
        x = (segment.x0 + block_end) / 2.0
        compression_force += force
        moment_nmm += force * (section.centroid - x)

    tension: list[tuple[float, float]] = []
    compression_steel_force = 0.0
    compression_steel_moment = 0.0
    bar_area = BAR_AREAS[wall.bar_size]
    tension_stress_limit = kdev * wall.fy
    for position in section.bar_positions:
        strain = EPS_MU * (position - neutral_axis) / neutral_axis
        if strain > 0.0:
            stress = min(tension_stress_limit, E_S * strain)
            force = PHI_S * bar_area * stress
            tension.append((position, force))
        elif _ties_qualify(wall):
            stress = min(wall.fy, E_S * abs(strain))
            force = PHI_S * bar_area * stress
            if position <= beta_one(section.fm_grouted) * neutral_axis:
                force = max(
                    0.0,
                    force - 0.85 * PHI_M * section.fm_grouted * bar_area,
                )
            compression_steel_force += force
            compression_steel_moment += force * (section.centroid - position)

    squat = wall.height / wall.length < 1.0 and bool(tension)
    design_depth: float | None = None
    if squat:
        design_depth = min(0.67 * wall.length, 0.70 * wall.height)
        if wall.edition == "S304:24" and sum(t for _, t in tension) > 0.0:
            # Force-weighted resultant of the tension bars (F-068; register I-10).
            tensile_centroid = sum(x * t for x, t in tension) / sum(t for _, t in tension)
            design_depth = min(design_depth, tensile_centroid)

    tension_force = 0.0
    normal_moment = moment_nmm + compression_steel_moment
    reduced_moment = moment_nmm + compression_steel_moment
    for position, force in tension:
        tension_force += force
        normal_moment += force * (position - section.centroid)
        reduced_moment += force * (
            (design_depth if design_depth is not None else position)
            - section.centroid
        )

    moment_nmm = min(normal_moment, reduced_moment) if squat else normal_moment
    axial_force = compression_force + compression_steel_force - tension_force
    branch = "reinforced" if tension else "compression-only"
    valid = True
    note = ""
    if squat:
        assert design_depth is not None
        block = max(beta_one(s.fm) for s in section.segments) * neutral_axis
        if reduced_moment < 0.0:
            valid = False
            note = (f"reduced-arm moment {reduced_moment / 1.0e6:.2f} kN-m < 0 with the tension resultant at "
                    f"d = {design_depth:.0f} mm")
        elif block >= design_depth:
            valid = False
            note = f"compression block a = beta1 c = {block:.1f} mm >= d = {design_depth:.0f} mm"
    return InteractionPoint(
        p=axial_force / 1000.0,
        m=max(moment_nmm, 0.0) / 1_000_000.0,
        c=neutral_axis,
        branch=branch,
        valid=valid,
        reduced_depth=design_depth,
        reduced_moment=reduced_moment / 1_000_000.0 if squat else None,
        note=note,
    )


def _interpolate_point(
    before: InteractionPoint,
    after: InteractionPoint,
    target_p: float,
    branch: str | None = None,
) -> InteractionPoint:
    fraction = (target_p - before.p) / (after.p - before.p)
    c = None
    if before.c is not None and after.c is not None:
        c = before.c + fraction * (after.c - before.c)
    return InteractionPoint(
        p=target_p,
        m=before.m + fraction * (after.m - before.m),
        c=c,
        branch=branch or before.branch,
        valid=before.valid and after.valid,
    )


def _reinforced_curve(
    wall: WallInput,
    section: SectionProperties,
    samples: int,
) -> tuple[tuple[InteractionPoint, ...], float]:
    cap = _axial_cap(wall, section)
    tension_cap = _tension_cap(wall, section)
    kdev = _development_factor(wall, section)
    c_max = wall.length / min(beta_one(s.fm) for s in section.segments)
    if _ties_qualify(wall):
        c_max = max(c_max, max(section.bar_positions) * EPS_MU / (EPS_MU - wall.fy / E_S))
    depths = {c_max * i / samples for i in range(1, samples + 1)}
    # Tension bars yield at kdev fy (register I-13); compression bars at fy.
    tension_yield_strain = kdev * wall.fy / E_S
    compression_yield_strain = wall.fy / E_S
    for position in section.bar_positions:
        depths.update((position, position * EPS_MU / (EPS_MU + tension_yield_strain)))
        if _ties_qualify(wall) and compression_yield_strain < EPS_MU:
            depths.add(position * EPS_MU / (EPS_MU - compression_yield_strain))
    for segment in section.segments:
        depths.update((segment.x0 / beta_one(segment.fm), segment.x1 / beta_one(segment.fm)))
    raw = [section_point_at_c(wall, c, section, development_factor=kdev) for c in sorted(depths) if 0.0 < c <= c_max]
    curve: list[InteractionPoint] = [
        InteractionPoint(-tension_cap, 0.0, 0.0, "tension-cap")
    ]
    for point in raw:
        if point.p < -tension_cap:
            continue
        if point.p <= cap:
            curve.append(point)
            continue
        if curve and curve[-1].p < cap:
            curve.append(_interpolate_point(curve[-1], point, cap, "axial-cap"))
        break
    if not curve:
        raise CalculationError("The reinforced P-M curve could not be generated.")
    if curve[-1].p < cap:
        last = curve[-1]
        curve.append(InteractionPoint(cap, last.m, last.c, "axial-cap"))
    curve.append(InteractionPoint(cap, 0.0, None, "axial-cap"))
    return tuple(curve), cap


def _unreinforced_point(wall: WallInput, section: SectionProperties, p: float) -> InteractionPoint:
    segment = section.segments[0]
    stress = 0.85 * PHI_M * segment.fm
    p_n = p * 1000.0
    compression_limit = (
        0.60 * PHI_M * segment.fm - p_n / section.area
    ) * section.section_modulus
    tension_limit = (
        PHI_M * section.ft + p_n / section.area
    ) * section.section_modulus
    uncracked = 0.0 if wall.unreinforced_precracked else max(0.0, min(compression_limit, tension_limit))
    if p_n == 0.0:
        cracked = 0.0
        c = 0.0
    else:
        block_depth = p_n / (stress * segment.effective_thickness)
        c = block_depth / beta_one(segment.fm)
        raw = p_n * (section.centroid - block_depth / 2.0)
        cracked = min(raw, p_n * ECCENTRICITY_LIMIT * wall.length)
    if not wall.unreinforced_precracked and uncracked >= cracked:
        moment, branch = uncracked, "uncracked"
    else:
        moment, branch = cracked, "cracked"
    return InteractionPoint(p, moment / 1_000_000.0, c, branch)


def _unreinforced_curve(
    wall: WallInput,
    section: SectionProperties,
    samples: int,
) -> tuple[tuple[InteractionPoint, ...], float]:
    cap = _axial_cap(wall, section)
    curve: list[InteractionPoint] = []
    for index in range(samples + 1):
        curve.append(_unreinforced_point(wall, section, cap * index / samples))
    curve.append(InteractionPoint(cap, 0.0, None, "axial-cap"))
    return tuple(curve), cap


def interaction_curve(
    wall: WallInput,
    section: SectionProperties | None = None,
    samples: int = 1200,
) -> tuple[tuple[InteractionPoint, ...], float]:
    if type(samples) is not int or samples < 100:
        raise CalculationError("At least 100 interaction samples are required.")
    section = section or section_properties(wall)
    if wall.wall_type == "Unreinforced masonry":
        return _unreinforced_curve(wall, section, samples)
    return _reinforced_curve(wall, section, samples)


def moment_at_axial_load(
    curve: tuple[InteractionPoint, ...],
    axial_load: float,
) -> float | None:
    candidates = [p.m for p in curve if abs(p.p - axial_load) <= 1.0e-8]
    for before, after in zip(curve, curve[1:]):
        if min(before.p, after.p) <= axial_load <= max(before.p, after.p):
            if abs(after.p - before.p) <= 1.0e-12:
                candidates.extend((before.m, after.m))
            else:
                candidates.append(_interpolate_point(before, after, axial_load).m)
    return max(candidates) if candidates else None


def _effective_depth(wall: WallInput, section: SectionProperties) -> tuple[float, str, bool]:
    """(dv, basis, assumed). S304-14 10.10.2.1: dv need not be taken less than
    0.8 lw for walls with flexural reinforcement distributed along the length;
    otherwise dv = min(0.8 lw, d) (register I-8, F-101). S304:24 Clause 3 gives
    0.8 lw for all walls. assumed = a two-bar S304-14 wall whose distribution
    is not declared and whose d is below 0.8 lw."""
    floor = 0.8 * wall.length
    if wall.edition == "S304:24" or wall.wall_type == "Unreinforced masonry" or not section.bar_positions:
        return floor, "0.8 lw" + (" (S304:24 Clause 3)" if wall.edition == "S304:24" else ""), False
    depth = max(section.bar_positions)
    reduced = min(floor, depth)
    text = f"min(0.8 lw = {floor:.1f}, d = {depth:.1f}) = {reduced:.1f} mm"
    declared = wall.flexural_reinforcement_distributed
    if declared is True:
        return floor, "0.8 lw: flexural reinforcement declared distributed along the length (10.10.2.1; register I-8)", False
    if declared is False:
        return reduced, f"{text}: flexural reinforcement declared not distributed (10.10.2.1; register I-8)", False
    if len(section.bar_positions) >= 3:
        return floor, ("0.8 lw: 3 or more evenly spaced bars read as distributed along the length (10.10.2.1; "
                       "provisional, register I-8)"), False
    return reduced, (f"{text}: two-bar wall, distribution not declared, so 0.8 lw is not credited "
                     "(10.10.2.1; register I-8)"), depth < floor - 1.0e-9


def _tension_eccentricity(section: SectionProperties, length: float) -> float:
    """e_t = sum x (x - lw/2) / sum x over the bar positions, x from the
    flexural compression face (register I-7): below this eccentricity of the
    axial tension the section has no compression zone."""
    positions = section.bar_positions
    return sum(x * (x - 0.5 * length) for x in positions) / sum(positions) if positions else 0.0


def _shear_result(
    wall: WallInput,
    section: SectionProperties,
    pd_kN: float | None,
    *,
    credit_horizontal: bool = True,
) -> ShearResult:
    dv, dv_basis, dv_assumed = _effective_depth(wall, section)
    if wall.factored_shear > 0.0:
        ratio = wall.factored_moment * 1_000_000.0 / (
            wall.factored_shear * 1000.0 * dv
        )
        ratio = min(1.0, max(0.25, ratio))
    else:
        ratio = 1.0
    fm = section.weighted_fm
    gamma = section.gamma_g
    # Only compression on the joint adds to Vm: 0.25 max(0, Pd). None when the
    # case Pd is not supplied; the routes that need it then report no Vm.
    pd_term = None if pd_kN is None else 0.25 * max(0.0, pd_kN) * 1000.0
    av, credit_basis = _horizontal_credit(wall, section) if credit_horizontal else (0.0, "horizontal steel not counted")
    spacing = wall.horizontal_reinforcement_spacing
    horizontal_fy = wall.horizontal_fy
    reinforced = wall.wall_type != "Unreinforced masonry"

    beta: float | None = None
    theta: float | None = None
    minimum_av_2024: float | None = None
    simplified_resistance: float | None = None
    general_resistance: float | None = None
    epsilon_x: float | None = None
    ze: float | None = None
    ze_basis = ""
    epsilon_basis = ""
    doubling_text = ""
    grout_assumed = False
    if wall.edition == "S304:24" and reinforced:
        minimum_av_2024 = 0.35 * wall.thickness * spacing / horizontal_fy

        def capacity(beta_value: float, theta_value: float) -> tuple[float, float, float, float, float]:
            masonry_value = (
                PHI_M
                * beta_value
                * math.sqrt(fm)
                * wall.thickness
                * dv
                * gamma
            )
            lever_value = min(
                dv / math.tan(math.radians(theta_value)),
                wall.height,
            )
            steel_value = PHI_S * av * horizontal_fy * lever_value / spacing
            uncapped_value = masonry_value + steel_value
            maximum_value = 0.25 * PHI_M * fm * wall.thickness * dv * gamma
            return (
                min(uncapped_value, maximum_value),
                masonry_value,
                steel_value,
                maximum_value,
                lever_value,
            )

        pd_missing = False
        if section.grouted_length_fraction < 1.0 - 1.0e-8:
            pd_missing = pd_term is None
            beta_simplified = beta_general = 0.16 * (2.0 - ratio) + (pd_term or 0.0) / (
                math.sqrt(fm) * wall.thickness * dv
            )
            theta_simplified = theta_general = 59.0
        else:
            candidate_zs = min(dv, section.bar_spacing or dv)
            vertical_area = BAR_AREAS[wall.bar_size]
            vertical_ld = _development_length(wall, section, horizontal=False) or 1.0
            developed_fraction = min(1.0, wall.available_vertical_development / vertical_ld)
            if not wall.vertical_bars_continuous:
                developed_fraction = min(developed_fraction, wall.available_termination_extension / vertical_ld)
            # 11.10.2.5(d) / 11.10.2.6.2: the 0.002 t zs proviso is tested on the
            # gross bar area; lack of development reduces only As in eps_x
            # (11.10.2.6.3(c), F-064).
            zs = (
                candidate_zs
                if vertical_area >= 0.002 * wall.thickness * candidate_zs
                else dv
            )
            beta_simplified = (
                0.18
                if av >= minimum_av_2024
                else 230.0 / (1000.0 + 1.4 * zs)
            )
            theta_simplified = 42.0
            if av >= minimum_av_2024:
                ze, ze_basis = 300.0, "Av >= Av,min: ze = 300 mm"
            elif wall.grout_type is not None:
                ga = 1.4 if wall.grout_type == "Coarse" else 1.7
                ze, ze_basis = ga * zs, f"ze = ga zs; {wall.grout_type.lower()} grout, ga = {ga}"
            elif wall.shear_method == "General":
                raise CalculationError("The S304:24 General shear method needs grout_type (ze = ga zs, ga 1.4 coarse / "
                                       "1.7 fine, 11.10.2.6.2): declare the grout type.")
            elif wall.factored_axial < 0.0:
                grout_assumed = True
                ze, ze_basis = 1.7 * zs, ("ze = ga zs; grout type not declared, fine-grout ga = 1.7 used on the general "
                                          "route required by axial tension (register I-15)")
            else:
                ze, ze_basis = 1.4 * zs, ("ze = ga zs; grout type not declared, candidate ga = 1.4, bounded by the "
                                          "simplified route (register I-15)")
            # Bars in the flexural tension half, measured from the geometric
            # mid-length (not the section centroid); a bar at mid-length is
            # included by TENSION_HALF_BOUNDARY_BAR (register I-6, F-057).
            half, tolerance = 0.5 * wall.length, 1.0e-6 * wall.length
            tension_bars = sum(
                (position >= half - tolerance) if TENSION_HALF_BOUNDARY_BAR == "include" else (position > half + tolerance)
                for position in section.bar_positions
            )
            tension_half_area = developed_fraction * BAR_AREAS[wall.bar_size] * tension_bars
            epsilon_basis = (f"As = {tension_half_area:.1f} mm2: {tension_bars} bars in the tension half (x >= 0.5 lw; "
                             f"bar at mid-length {TENSION_HALF_BOUNDARY_BAR}d, tolerance 1e-6 lw, register I-6)")
            if developed_fraction < 1.0:
                epsilon_basis += f"; As x developed fraction {developed_fraction:.4f} (11.10.2.6.3(c))"
            design_moment = max(
                wall.factored_moment * 1_000_000.0,
                wall.factored_shear * 1000.0 * dv,
            )
            axial_n = -wall.factored_axial * 1000.0
            epsilon_x = (
                (design_moment / dv + wall.factored_shear * 1000.0 + 0.5 * axial_n)
                / (2.0 * E_S * tension_half_area)
                if tension_half_area > 0.0
                else 0.003
            )
            epsilon_x = min(0.003, max(0.0, epsilon_x))
            if wall.factored_axial < 0.0:
                # 11.10.2.6.3(e): doubled only when the axial tension cracks the
                # flexural compression face: Mf <= |Pf| e_t (register I-7, F-065).
                e_t = _tension_eccentricity(section, wall.length)
                limit_moment = -wall.factored_axial * e_t / 1000.0
                if wall.factored_moment <= limit_moment:
                    epsilon_x = min(0.003, 2.0 * epsilon_x)
                    doubling_text = "eps_x doubled: axial tension cracks the compression face (register I-7)"
                    epsilon_basis += (f"; eps_x doubled: axial tension cracks the compression face, Mf = "
                                      f"{wall.factored_moment:.2f} <= |Pf| e_t = {limit_moment:.2f} kN-m "
                                      f"(e_t = {e_t:.1f} mm; 11.10.2.6.3(e), register I-7)")
                else:
                    doubling_text = "eps_x not doubled: a compression zone remains (register I-7)"
                    epsilon_basis += (f"; eps_x not doubled: a compression zone remains, Mf = "
                                      f"{wall.factored_moment:.2f} > |Pf| e_t = {limit_moment:.2f} kN-m "
                                      f"(e_t = {e_t:.1f} mm; 11.10.2.6.3(e), register I-7)")
            beta_general = 0.40 / (1.0 + 1500.0 * epsilon_x) * 1300.0 / (1000.0 + ze)
            theta_general = 29.0 + 7000.0 * epsilon_x

        simplified = capacity(beta_simplified, theta_simplified)
        general = capacity(beta_general, theta_general)
        simplified_resistance = simplified[0] / 1000.0
        general_resistance = general[0] / 1000.0
        if wall.factored_axial < 0.0:
            selected = general
            beta, theta = beta_general, theta_general
            method = "S304:24 general route required by axial tension" + (f"; {doubling_text}" if doubling_text else "")
        elif wall.shear_method == "Simplified":
            selected = simplified
            beta, theta = beta_simplified, theta_simplified
            method = "S304:24 simplified modified-compression-field route"
        elif wall.shear_method == "General":
            selected = general
            beta, theta = beta_general, theta_general
            method = "S304:24 general modified-compression-field route"
        elif simplified[0] <= general[0]:
            selected = simplified
            beta, theta = beta_simplified, theta_simplified
            method = "Lower S304:24 route: simplified"
        else:
            selected = general
            beta, theta = beta_general, theta_general
            method = "Lower S304:24 route: general"
        resistance, masonry, steel, maximum, lever = selected
        uncapped = masonry + steel
        required = max(
            0.0,
            (wall.factored_shear * 1000.0 - masonry)
            * spacing
            / (PHI_S * horizontal_fy * lever),
        )
        if pd_missing:
            # The partially grouted beta has a Pd term: no Vm without the case Pd.
            masonry = uncapped = resistance = required = None
            beta = simplified_resistance = general_resistance = None
            method += "; " + PD_REQUIRED
    else:
        if wall.edition == "S304:24" and wall.apply_proposed_8_10_2_3_correction:
            vm = 0.16 * (2.0 - ratio) * math.sqrt(fm)
            method = (
                "S304:24 unreinforced shear route; proposed June 2025 correction to "
                "8.10.2.3 with sqrt(f'm) restored, not verified against the corrected text (register I-5)"
            )
        elif wall.edition == "S304:24":
            vm = 0.16 * (2.0 - ratio)  # Printed April 2024 Clause 8.10.2.3 omits sqrt(f'm).
            method = ("S304:24 unreinforced shear route; printed April 2024 8.10.2.3 without sqrt(f'm), "
                      "p.57 (register I-5)")
        else:
            vm = 0.16 * (2.0 - ratio) * math.sqrt(fm)
            method = "S304-14 in-plane shear route"
        masonry = None if pd_term is None else PHI_M * (vm * wall.thickness * dv + pd_term) * gamma
        steel = (
            0.60 * PHI_S * av * horizontal_fy * dv / spacing if reinforced else 0.0
        )
        uncapped = None if masonry is None else masonry + steel
        if masonry is None:
            method += "; " + PD_REQUIRED
        aspect = min(1.0, max(0.5, wall.height / wall.length))
        squat_factor = (
            2.0 - aspect
            if wall.height / wall.length < 1.0 and wall.squat_shear_distribution_confirmed
            else 1.0
        )
        maximum = (
            0.40
            * PHI_M
            * math.sqrt(fm)
            * wall.thickness
            * dv
            * gamma
            * squat_factor
        )
        resistance = None if uncapped is None else min(uncapped, maximum)
        required = (
            max(
                0.0,
                (wall.factored_shear * 1000.0 - masonry)
                * spacing
                / (0.60 * PHI_S * horizontal_fy * dv),
            )
            if reinforced and masonry is not None
            else None
        )
    stack_resistance = None
    if wall.bond_pattern == "Stack pattern":
        # This is a vertical interface action, not the horizontal diagonal Vf.
        # Ch uses the total developed area crossing the assessed head joint.
        ch = PHI_S * (wall.stack_joint_area or 0.0) * horizontal_fy if av > 0.0 else 0.0
        stack_resistance = (PHI_M * 0.7 if wall.edition == "S304-14" else 1.0) * ch / 1000.0
    if wall.factored_shear * 1000.0 > maximum + 1.0e-8:
        required = None  # More horizontal steel cannot cure the masonry shear cap.
    return ShearResult(
        effective_depth=dv,
        moment_shear_ratio=ratio,
        beta=beta,
        theta_degrees=theta,
        masonry_resistance=None if masonry is None else masonry / 1000.0,
        steel_resistance=steel / 1000.0,
        uncapped_resistance=None if uncapped is None else uncapped / 1000.0,
        maximum_resistance=maximum / 1000.0,
        resistance=None if resistance is None else resistance / 1000.0,
        required_horizontal_area=required,
        minimum_horizontal_area_2024=minimum_av_2024,
        simplified_resistance=simplified_resistance,
        general_resistance=general_resistance,
        epsilon_x=epsilon_x,
        ze=ze,
        method=method,
        stack_vertical_resistance=stack_resistance,
        ze_basis=ze_basis,
        grout_assumed=grout_assumed,
        epsilon_x_basis=epsilon_basis,
        effective_depth_basis=dv_basis,
        horizontal_area_credited=av,
        horizontal_credit_basis=credit_basis,
        flexural_reinforcement_distribution_assumed=dv_assumed,
    )


@dataclass(frozen=True)
class DowelArea:
    """Dowel steel credited across a sliding plane (register I-13)."""

    raw_area: float
    effective_area: float
    kdev: float
    text: str


_ALL_BARS = object()


def developed_dowel_area(
    wall: WallInput,
    section: SectionProperties | None = None,
    *,
    bars_developed: bool | None = None,
    developed_bar_count: int | None | object = _ALL_BARS,
    available_development: float | None = None,
) -> DowelArea:
    """Developed dowel area across a sliding plane, mm2, with the edition's
    development rule applied once (register I-13, F-047).

    Defaults are the wall's in-plane sliding inputs. S304:24 Clause 3 C
    credits As,dev x kdev below ld; S304-14 10.10.5.1 credits only bars
    detailed to develop fy (none below ld). One helper serves the in-plane
    and the out-of-plane sliding checks, so the reduction is never applied
    twice. ``developed_bar_count`` None means every bar.
    """
    section = section if section is not None else section_properties(wall, "Discrete")
    developed = wall.vertical_bars_developed_at_base if bars_developed is None else bars_developed
    count = wall.developed_vertical_bar_count if developed_bar_count is _ALL_BARS else developed_bar_count
    available = wall.available_vertical_development if available_development is None else available_development
    developed_count = 0
    if wall.wall_type != "Unreinforced masonry" and developed:
        developed_count = count if count is not None else len(section.bar_positions)
    raw_area = developed_count * BAR_AREAS[wall.bar_size]
    developed_area = raw_area
    ld = _development_length(wall, section, horizontal=False) or 0.0
    kdev = 1.0
    development_text = ""
    if ld > 0.0 and available < ld and developed_area > 0.0:
        kdev = min(1.0, available / ld)
        if wall.edition == "S304:24":
            # Clause 3 C: the factored tensile force that can be developed
            # (register I-13, F-047).
            developed_area *= kdev
            development_text = (f"; dowels developed over {available:.0f} mm < ld "
                                f"{ld:.1f} mm: As,dev x kdev = {kdev:.4f} (S304:24 Clause 3 C, register I-13)")
        else:
            # 10.10.5.1: only bars detailed to develop their yield strength.
            developed_area = 0.0
            development_text = (f"; dowels developed over {available:.0f} mm < ld "
                                f"{ld:.1f} mm are not counted (10.10.5.1: bars detailed to develop fy; register I-13)")
    return DowelArea(raw_area, developed_area, kdev, development_text)


def _sliding_result(
    wall: WallInput,
    section: SectionProperties,
    pd_kN: float | None,
) -> SlidingResult:
    dowels = developed_dowel_area(wall, section)
    developed_area, kdev, development_text = dowels.effective_area, dowels.kdev, dowels.text
    reinforced = wall.wall_type != "Unreinforced masonry"
    edition_factor = PHI_M if wall.edition == "S304-14" else 1.0
    if wall.edition == "S304:24":
        basis = (("Vr = mu C without phi_m, as printed in 11.10.5.1 (p.91)" if reinforced
                  else "Vr = mu P without phi_m, as printed in 8.10.5 (p.58)") + " (register I-4)")
    else:
        basis = ("Vr = phi_m mu C (10.10.5.1)" if reinforced
                 else "Vr = phi_m mu P1 (7.10.5.1), P1 read as Pd (register I-3)")
    # The bond route's uncracked area keeps the sign of Pf: a joint under net
    # tension has no uncracked bond area.
    uncracked_area = wall.uncracked_area_fraction * section.area if wall.factored_axial >= 0.0 else 0.0
    between_courses = wall.sliding_plane == "Between masonry courses"
    method = ("greater of separate reinforced-friction and uncracked-bond routes" if between_courses
              else "support-joint shear friction")
    if pd_kN is None:
        return SlidingResult(None, None, None, uncracked_area, None, developed_area,
                             f"{method}; {PD_REQUIRED}{development_text}", kdev)
    # The clamping force is the case Pd (0.9 D_cnt plus the signed lateral
    # axial load), never Pf: live and snow loads never clamp (register I-3).
    pd = pd_kN * 1000.0
    clamping = max(0.0, pd + PHI_S * developed_area * wall.fy)
    reinforced_route = edition_factor * wall.sliding_friction * clamping
    bond_route: float | None = None
    if between_courses:
        bond_route = (
            0.16 * PHI_M * math.sqrt(section.weighted_fm) * uncracked_area
            + edition_factor * wall.sliding_friction * max(0.0, pd)
        )
        resistance = max(reinforced_route, bond_route)
    else:
        resistance = reinforced_route
    required_area = 0.0
    if bond_route is None or bond_route < wall.factored_shear * 1000.0:
        required_area = max(
            0.0,
            (
                wall.factored_shear * 1000.0
                / (edition_factor * wall.sliding_friction)
                - pd
            )
            / (PHI_S * wall.fy),
        )
    return SlidingResult(
        resistance=resistance / 1000.0,
        reinforced_route=reinforced_route / 1000.0,
        bond_route=None if bond_route is None else bond_route / 1000.0,
        uncracked_area=uncracked_area,
        required_dowel_area=required_area,
        developed_vertical_area=developed_area,
        method=f"{method}; {basis}; C = max(0, Pd + phi_s As,dev fy){development_text}",
        development_factor=kdev,
    )


@dataclass(frozen=True)
class _Flexure:
    """What the P-M check and trace need to name their basis (F-047, F-055, F-071)."""

    reason: str = "ok"
    point: InteractionPoint | None = None
    curve_method: str = "Discrete"
    alternate_method: str | None = None
    alternate_mr: float | None = None
    kdev: float = 1.0
    vertical_ld: float = 0.0
    bond_beam_grout: tuple[float, str | None, str] = (0.0, None, "")


def _squat_text(wall: WallInput, point: InteractionPoint | None) -> str:
    clause = _edition_clause(wall, "10.2.8", "11.2.8")
    d067, d07 = 0.67 * wall.length, 0.70 * wall.height
    centroid = ", force-weighted tension centroid" if wall.edition == "S304:24" else ""
    if point is None or point.reduced_depth is None:
        return (f"Squat wall (hw/lw < 1), deep-section model ({clause}): no bar in tension at this Pf, so the "
                f"plane-section moment applies; d = min(0.67 lw = {d067:.0f}, 0.7 hw = {d07:.0f}{centroid}) - engine "
                "reading, not confirmed (register I-10).")
    return (f"Deep-section model ({clause}): tension resultant at d = {point.reduced_depth:.0f} mm = min(0.67 lw = "
            f"{d067:.0f}, 0.7 hw = {d07:.0f}{centroid}) - engine reading, not confirmed (register I-10).")


def _seismic_gate_active(wall: WallInput) -> bool:
    """Clause 16 / 17 gate: every wall whose SFRS declaration is missing or true,
    and every earthquake case, unless the case layer verified the NBC 4.1.8.1
    simplified-procedure exemption (register I-22)."""
    return not wall.seismic_exempt_4_1_8_1 and (wall.sfrs_wall is not False or wall.earthquake_case)


def _checks(
    wall: WallInput,
    section: SectionProperties,
    axial_cap: float,
    tension_cap: float,
    moment_resistance: float | None,
    shear: ShearResult,
    sliding: SlidingResult,
    detailing: DetailingResult,
    stability: StabilityResult,
    self_weight: float,
    pd: float | None,
    flexure: _Flexure | None = None,
) -> tuple[Check, ...]:
    flexure = flexure or _Flexure()
    checks: list[Check] = []

    def add_check(level: CheckLevel, title: str, detail: str, clause: str, category: str = "additional",
                  *, check_id: str = "", kind: str = "numeric", demand: float | None = None,
                  limit: float | None = None, units: str = "", comparison: str | None = None,
                  tolerance: float = 0.0, unresolved: Unresolved = "") -> None:
        checks.append(Check(level, title, detail, clause, category, check_id, kind, demand,
                            limit, units, comparison, tolerance, unresolved))

    def add(ok: bool, title: str, detail: str, clause: str, category: str = "additional",
            *, check_id: str = "", kind: str = "numeric", demand: float | None = None,
            limit: float | None = None, units: str = "", comparison: str | None = None,
            tolerance: float = 0.0, unresolved: Unresolved = "") -> None:
        add_check("pass" if ok else "blocked", title, detail, clause, category,
                  check_id=check_id, kind=kind, demand=demand, limit=limit, units=units,
                  comparison=comparison, tolerance=tolerance, unresolved="" if ok else unresolved)

    reinforced = wall.wall_type != "Unreinforced masonry"
    flex_prefix = ("7" if wall.edition == "S304-14" else "8") if not reinforced else ("10" if wall.edition == "S304-14" else "11")
    flex_clause = f"{flex_prefix}.2 / {flex_prefix}.4"
    development_text = ""
    if reinforced and flexure.kdev < 1.0:
        development_text = (f" Bars developed over {wall.available_vertical_development:.0f} mm < ld "
                            f"{flexure.vertical_ld:.1f} mm: tension stress capped at kdev fy = {flexure.kdev:.4f} x "
                            f"{wall.fy:.0f} = {flexure.kdev * wall.fy:.1f} MPa (register I-13).")
    if wall.factored_axial >= 0.0:
        add(
            wall.factored_axial <= axial_cap + 1.0e-8,
            "Maximum compression resistance",
            f"Pf = {wall.factored_axial:.2f} kN; Pr,max = {axial_cap:.2f} kN.",
            flex_clause,
            "resistance",
            check_id="ip.maximum_compression_resistance",
            kind="numeric",
            demand=wall.factored_axial,
            limit=axial_cap,
            units="kN",
            comparison="<=",
            tolerance=1e-8,
        )
    else:
        add(
            -wall.factored_axial <= tension_cap + 1.0e-8,
            "Maximum axial-tension resistance",
            f"|Pf| = {-wall.factored_axial:.2f} kN; Tr,max = {tension_cap:.2f} kN." + development_text,
            flex_clause,
            "resistance",
            check_id="ip.maximum_axial_tension_resistance",
            kind="numeric",
            demand=-wall.factored_axial,
            limit=tension_cap,
            units="kN",
            comparison="<=",
            tolerance=1e-8,
        )
    squat = reinforced and wall.height / wall.length < 1.0
    pm_clause = flex_clause + ("; " + _edition_clause(wall, "10.2.8", "11.2.8") if squat else "")
    deep_unsupported = moment_resistance is None and flexure.reason == "deep_section_model_outside_range"
    if moment_resistance is not None:
        method_text = ""
        if wall.wall_type == "Partially grouted reinforced masonry" and flexure.alternate_mr is not None:
            if wall.partial_grout_method == "Use lower of discrete and smeared":
                method_text = (f" (lower of Discrete and Smeared: {flexure.curve_method} governs, "
                               f"{flexure.alternate_method} {flexure.alternate_mr:.2f} kN-m; tool-side conservative "
                               "envelope; register I-9)")
            elif flexure.curve_method == "Discrete":
                method_text = (f" (Discrete, Table 4 Note 3: grouted and ungrouted parts treated separately; Smeared "
                               f"{flexure.alternate_mr:.2f} kN-m, a tool idealization; register I-9)")
            else:
                method_text = (f" (Smeared, tool idealization selected; Discrete, Table 4 Note 3, "
                               f"{flexure.alternate_mr:.2f} kN-m; register I-9)")
        resistance_text = f"Mr(Pf) = {moment_resistance:.2f} kN-m{method_text}."
    elif deep_unsupported:
        note = flexure.point.note if flexure.point is not None else ""
        resistance_text = (f"Mr not defined: the deep-section model ({_edition_clause(wall, '10.2.8', '11.2.8')}) is "
                           f"outside its range at this Pf ({note}); a more detailed analysis (e.g. strut-and-tie, "
                           "11.2.8 Note) is required.")
    else:
        resistance_text = "no resistance is defined at this axial load."
    pm_detail = f"Mf = {wall.factored_moment:.2f} kN-m; {resistance_text}"
    if squat:
        pm_detail += " " + _squat_text(wall, flexure.point)
    pm_detail += development_text
    add(
        moment_resistance is not None
        and wall.factored_moment <= moment_resistance + 1.0e-8,
        "Axial load plus in-plane moment",
        pm_detail,
        pm_clause,
        "resistance",
        check_id="ip.axial_load_plus_in_plane_moment",
        kind="numeric",
        demand=wall.factored_moment,
        limit=moment_resistance,
        units="kNm",
        comparison="<=",
        tolerance=1e-8,
        unresolved="unsupported" if deep_unsupported else "",
    )
    shear_clause = "7.10 / 10.10" if wall.edition == "S304-14" else "8.10 / 11.10"
    shear_detail = (f"Vf = {wall.factored_shear:.2f} kN; "
                    + (f"Vr = {shear.resistance:.2f} kN." if shear.resistance is not None else f"Vr not evaluated: {PD_REQUIRED}."))
    if not reinforced and wall.edition == "S304:24":
        shear_detail += (" vm with sqrt(f'm) per the proposed June 2025 correction to 8.10.2.3 (not verified; register I-5)."
                         if wall.apply_proposed_8_10_2_3_correction else
                         " vm per printed 8.10.2.3 (April 2024, p.57), without sqrt(f'm) (register I-5).")
    if reinforced and wall.edition == "S304-14":
        shear_detail += f" dv = {shear.effective_depth:.1f} mm: {shear.effective_depth_basis}."
    if shear.horizontal_credit_basis.startswith("Vs not credited"):
        shear_detail += f" {shear.horizontal_credit_basis}."
    add(
        shear.resistance is not None and wall.factored_shear <= shear.resistance + 1.0e-8,
        "Diagonal shear",
        shear_detail,
        shear_clause,
        "resistance",
        check_id="ip.diagonal_shear",
        kind="numeric",
        demand=wall.factored_shear,
        limit=shear.resistance,
        units="kN",
        comparison="<=",
        tolerance=1e-8,
        unresolved="incomplete" if shear.resistance is None else "",
    )
    add(
        sliding.resistance is not None and wall.factored_shear <= sliding.resistance + 1.0e-8,
        "Sliding shear",
        f"Vf = {wall.factored_shear:.2f} kN; "
        + (f"Vr,slide = {sliding.resistance:.2f} kN ({sliding.method})." if sliding.resistance is not None
           else f"Vr,slide not evaluated ({sliding.method})."),
        ("10.10.5.1" if wall.edition == "S304-14" else "11.10.5.1")
        if reinforced
        else ("7.10.5.1" if wall.edition == "S304-14" else "8.10.5"),
        "resistance",
        check_id="ip.sliding_shear",
        kind="numeric",
        demand=wall.factored_shear,
        limit=sliding.resistance,
        units="kN",
        comparison="<=",
        tolerance=1e-8,
        unresolved="incomplete" if sliding.resistance is None else "",
    )
    if shear.flexural_reinforcement_distribution_assumed:
        add(
            False,
            "Flexural reinforcement distribution declared",
            f"Two-bar wall with d = {max(section.bar_positions):.1f} mm < 0.8 lw = {0.8 * wall.length:.1f} mm: "
            "declare whether the flexural reinforcement is distributed along the length "
            "(flexural_reinforcement_distributed); dv = min(0.8 lw, d) is used until then (register I-8).",
            "10.10.2.1",
            check_id="ip.flexural_reinforcement_distribution_declared",
            kind="numeric",
            unresolved="incomplete",
        )

    if reinforced:
        assert section.bar_spacing is not None
        area = BAR_AREAS[wall.bar_size]
        count = len(section.bar_positions)
        spacing_v = section.bar_spacing
        four_t = 4.0 * wall.thickness
        if spacing_v > four_t + 1.0e-9:
            minimum = 0.00125 * wall.thickness * four_t
            minimum_ok = area >= minimum - 1.0e-8
            minimum_numbers = dict(demand=area, limit=minimum)
            minimum_detail = (f"s = {spacing_v:.1f} mm > 4t = {four_t:.0f} mm: each bar As,bar = {area:.1f} mm2 >= "
                              f"0.00125 (4t x t) = {minimum:.1f} mm2.")
        else:
            total, total_limit = count * area, 0.00125 * wall.thickness * wall.length
            tributary = min(wall.end_bar_distance + spacing_v / 2.0, four_t)
            end_limit = 0.00125 * wall.thickness * tributary
            minimum_ok = total >= total_limit - 1.0e-8 and area >= end_limit - 1.0e-8
            # Report the condition with the smaller margin.
            minimum_numbers = (dict(demand=total, limit=total_limit) if total_limit / total >= end_limit / area
                               else dict(demand=area, limit=end_limit))
            minimum_detail = (f"s = {spacing_v:.1f} mm <= 4t = {four_t:.0f} mm: total As = {total:.1f} mm2 >= "
                              f"0.00125 t lw = {total_limit:.1f} mm2 (gross-area minimum, confirmed); end bar "
                              f"{area:.1f} mm2 >= 0.00125 t min(e + s/2, 4t) = {end_limit:.1f} mm2 over "
                              f"{tributary:.1f} mm (end-bar tributary rule, provisional reading, register I-18).")
        add(
            minimum_ok,
            "Minimum vertical reinforcement",
            minimum_detail,
            "10.15.1.1" if wall.edition == "S304-14" else "11.15.1.1",
            check_id="ip.minimum_vertical_reinforcement",
            kind="numeric",
            units="mm2",
            comparison=">=",
            tolerance=1e-8,
            **minimum_numbers,
        )
        ratio = area / (
            wall.thickness * min(section.bar_spacing, 4.0 * wall.thickness)
        )
        add(
            ratio <= 0.02 + 1.0e-12,
            "Maximum vertical reinforcement",
            f"Vertical steel ratio = {100.0 * ratio:.3f}%; limit = 2.000%.",
            "10.15.2" if wall.edition == "S304-14" else "11.15.2",
            check_id="ip.maximum_vertical_reinforcement",
            kind="numeric",
            demand=ratio,
            limit=0.02,
            units="ratio",
            comparison="<=",
            tolerance=1e-12,
        )
        add(
            section.bar_spacing <= 2400.0 + 1.0e-8,
            "Maximum vertical-bar spacing",
            f"s_v = {section.bar_spacing:.1f} mm; limit = 2400 mm.",
            "10.15.1.2" if wall.edition == "S304-14" else "11.15.1.2",
            check_id="ip.maximum_vertical_bar_spacing",
            kind="numeric",
            demand=section.bar_spacing,
            limit=2400.0,
            units="mm",
            comparison="<=",
            tolerance=1e-8,
        )
        add(
            wall.end_bar_distance <= 200.0 + 1.0e-8,
            "Vertical reinforcement at wall ends",
            f"End-bar distance = {wall.end_bar_distance:.1f} mm; limit = 200 mm. The 200 mm end-cell limit is this model's interpretation of the end-of-wall requirement, not a value given by the standard.",
            "10.15.1.2" if wall.edition == "S304-14" else "11.15.1.2",
            check_id="ip.vertical_reinforcement_at_wall_ends",
            kind="numeric",
            demand=wall.end_bar_distance,
            limit=200.0,
            units="mm",
            comparison="<=",
            tolerance=1e-8,
        )
        add(
            detailing.vertical_bar_diameter <= detailing.vertical_clearance_limit + 1.0e-8,
            "Vertical bar fit in masonry cell",
            f"Bar diameter = {detailing.vertical_bar_diameter:.1f} mm; limit = {detailing.vertical_clearance_limit:.1f} mm.",
            "12.1.3" if wall.edition == "S304-14" else "13.1.3",
            check_id="ip.vertical_bar_fit_in_masonry_cell",
            kind="numeric",
            demand=detailing.vertical_bar_diameter,
            limit=detailing.vertical_clearance_limit,
            units="mm",
            comparison="<=",
            tolerance=1e-8,
        )
        add(
            wall.available_vertical_development >= detailing.vertical_development_required - 1.0e-8,
            "Vertical-bar development at critical section",
            f"Available = {wall.available_vertical_development:.0f} mm; required = "
            f"{detailing.vertical_development_required:.1f} mm ({detailing.vertical_development_basis}).",
            "12.4.2.1 / 12.4.2.3 / 12.4.2.4" if wall.edition == "S304-14" else "13.4.2.1 / 13.4.2.3 / 13.4.2.4",
            check_id="ip.vertical_bar_development_at_critical_section",
            kind="numeric",
            demand=wall.available_vertical_development,
            limit=detailing.vertical_development_required,
            units="mm",
            comparison=">=",
            tolerance=1e-8,
        )
        if wall.splice_present and detailing.lap_required is not None:
            if wall.splice_class == "A":
                add(
                    wall.class_a_splice_confirmed,
                    "Class A splice eligibility",
                    "Requires at least twice the steel area required by analysis and no more than 50% spliced within the lap length.",
                    "12.5.4.1" if wall.edition == "S304-14" else "13.5.4.1 / Table 11",
                    check_id="ip.class_a_splice_eligibility",
                    kind="review",
                )
            lap_basis = ("Class A length 1.0 ld (eligibility confirmed)" if wall.splice_class == "A" and wall.class_a_splice_confirmed
                         else "Class A requested, eligibility not confirmed: Class B length 1.3 ld used" if wall.splice_class == "A"
                         else "Class B length 1.3 ld")
            lap_basis += f"; {detailing.lap_basis}"
            compression_lap = _ties_qualify(wall)
            add(
                wall.provided_lap_length is not None
                and wall.provided_lap_length >= detailing.lap_required - 1.0e-8,
                f"Class {wall.splice_class} tension lap splice",
                (f"Provided = {wall.provided_lap_length:.0f} mm; required = {detailing.lap_required:.1f} mm ({lap_basis})."
                 if wall.provided_lap_length is not None else
                 f"Lap length not entered; required = {detailing.lap_required:.1f} mm ({lap_basis})."),
                (("12.5.4.2 / 12.5.5.1" if compression_lap else "12.5.4.2") if wall.edition == "S304-14"
                 else ("13.5.4.2 / 13.5.5.1" if compression_lap else "13.5.4.2")),
                check_id="ip.class_tension_lap_splice",
                kind="numeric",
                demand=wall.provided_lap_length,
                limit=detailing.lap_required,
                units="mm",
                comparison=">=",
                tolerance=1e-8,
                unresolved="incomplete" if wall.provided_lap_length is None else "",
            )
        add(
            wall.vertical_bars_continuous,
            "Minimum vertical reinforcement continuity",
            "Bars are declared continuous over the wall height (register I-20)." if wall.vertical_bars_continuous else
            ("Bars declared not continuous and no continuing-bar layout entered: the 10.15.1.1 minimum must be "
             "continuous; terminated-bar layouts (continuing bars, cut-off height, 12.4.8.4 embedment, 12.4.8.5 "
             "cut-off shear) are not modelled (register I-20)."),
            "10.15.1.2(a)/(b)" if wall.edition == "S304-14" else "11.15.1.2 a)/b)",
            check_id="ip.minimum_vertical_reinforcement_continuity",
            kind="numeric",
            unresolved="unsupported",
        )
        if wall.vertical_bars_continuous:
            add(True, "Vertical-bar continuity or termination extension", "Bars are continuous over the wall height.",
                "12.4.8.3" if wall.edition == "S304-14" else "13.4.8.3",
                check_id="ip.vertical_bar_continuity_or_termination_extension", kind="numeric")
        else:
            add(
                wall.available_termination_extension >= detailing.termination_extension_required - 1.0e-8,
                "Vertical-bar continuity or termination extension",
                f"Available extension = {wall.available_termination_extension:.0f} mm; required = "
                f"{detailing.termination_extension_required:.0f} mm = max(d, 12 db) past the flexural cut-off.",
                "12.4.8.3" if wall.edition == "S304-14" else "13.4.8.3",
                check_id="ip.vertical_bar_continuity_or_termination_extension",
                kind="numeric",
                demand=wall.available_termination_extension,
                limit=detailing.termination_extension_required,
                units="mm",
                comparison=">=",
                tolerance=1e-8,
            )
        if wall.compression_reinforcement_tied:
            _, longitudinal_diameter = BAR_PROPERTIES[wall.bar_size]
            tie_limit = min(
                16.0 * longitudinal_diameter,
                48.0 * wall.tie_diameter,
                wall.thickness,
            )
            credited = _ties_qualify(wall)
            shortfalls = []
            if wall.tie_spacing > tie_limit:
                shortfalls.append(f"spacing {wall.tie_spacing:g} mm > {tie_limit:.1f} mm")
            if wall.tie_diameter < 3.65:
                shortfalls.append(f"tie diameter {wall.tie_diameter:.2f} mm < 3.65 mm")
            if not wall.tie_arrangement_confirmed:
                shortfalls.append("tie arrangement not confirmed")
            # No clause requires ties; untied compression steel is only not
            # counted (10.2.7 / 11.2.7), so this is information (F-070).
            add_check(
                "pass",
                "Compression-reinforcement ties",
                (f"Compression steel credited: {'yes' if credited else 'no'}"
                 + (f" ({'; '.join(shortfalls)})" if shortfalls else "")
                 + f". Ties {wall.tie_diameter:.2f} mm (minimum 3.65 mm) at {wall.tie_spacing:g} mm; limit "
                 f"min(16 db, 48 dt, t) = {tie_limit:.1f} mm. Without the credit Pr,max and Mr exclude the compression "
                 "steel."),
                "12.2.1 (10.2.7)" if wall.edition == "S304-14" else "13.2.1 (11.2.7)",
                check_id="ip.compression_reinforcement_ties",
                kind="info",
            )
            add(
                wall.tie_arrangement_confirmed,
                "Compression-reinforcement tie arrangement",
                ("Tie arrangement confirmed: lateral support of every bar, hooks and placement." if wall.tie_arrangement_confirmed
                 else "Confirm the tie arrangement (lateral support of every bar, hooks and placement); the compression "
                      "steel is not credited until it is confirmed."),
                "12.2.6 (12.2.2, 12.2.5)" if wall.edition == "S304-14" else "13.2.6 (13.2.2, 13.2.5)",
                check_id="ip.compression_reinforcement_tie_arrangement",
                kind="review",
            )

        horizontal_type = wall.horizontal_reinforcement_type
        maximum_spacing = _horizontal_spacing_limit(wall)
        if horizontal_type != "None":
            required = shear.horizontal_steel_required
            bare = shear.resistance_without_horizontal_steel
            spacing_clause = _edition_clause(wall, "10.15.1.4", "11.15.1.4")
            not_required = ("" if required or bare is None else
                            f"Not required for in-plane shear: Vf = {wall.factored_shear:.2f} kN <= Vr without "
                            f"horizontal steel = {bare:.2f} kN; {spacing_clause} applies only where horizontal "
                            "reinforcement is required (register I-17).")
            undecided = ("" if required is not None else
                         " Vr without horizontal steel cannot be formed from the declared inputs (e.g. the case Pd "
                         f"or the grout type is not declared), so whether {spacing_clause} requires this steel is "
                         "undecided (register I-17).")

            def requirement(ok: bool, title: str, detail: str, clause: str, *, check_id: str, kind: str = "numeric",
                            unresolved: Unresolved = "", **numbers: object) -> None:
                if not_required:
                    add_check("pass", title, f"{not_required} {detail}", clause, check_id=check_id, kind="info")
                elif undecided and not ok and kind == "numeric" and not unresolved:
                    # Ground rule 2: a detail that binds only where the steel is
                    # required is INCOMPLETE, not FAIL, while that is undecided.
                    add(ok, title, detail + undecided, clause, check_id=check_id, kind=kind,
                        unresolved="incomplete", **numbers)  # type: ignore[arg-type]
                else:
                    add(ok, title, detail, clause, check_id=check_id, kind=kind, unresolved=unresolved, **numbers)  # type: ignore[arg-type]

            add(
                wall.horizontal_reinforcement_area > 0.0,
                "Horizontal reinforcement area supplied",
                f"Av = {wall.horizontal_reinforcement_area:.1f} mm2 at each level.",
                "Reinforcement layout",
                check_id="ip.horizontal_reinforcement_area_supplied",
                kind="numeric",
                demand=wall.horizontal_reinforcement_area,
                limit=0.0,
                units="mm2",
                comparison=">",
                tolerance=0.0,
            )
            horizontal_ratio = wall.horizontal_reinforcement_area / (
                wall.thickness * min(wall.horizontal_reinforcement_spacing, 4.0 * wall.thickness)
            )
            add(
                horizontal_ratio <= 0.02 + 1.0e-12,
                "Maximum horizontal reinforcement",
                f"Horizontal steel ratio = {100.0 * horizontal_ratio:.3f}%; limit = 2.000%.",
                "10.15.2" if wall.edition == "S304-14" else "11.15.2",
                check_id="ip.maximum_horizontal_reinforcement",
                kind="numeric",
                demand=horizontal_ratio,
                limit=0.02,
                units="ratio",
                comparison="<=",
                tolerance=1e-12,
            )
            beyond = wall.horizontal_reinforcement_spacing > maximum_spacing + 1.0e-8
            requirement(
                not beyond,
                "Horizontal shear-reinforcement spacing",
                f"Type = {horizontal_type}; s = {wall.horizontal_reinforcement_spacing:.1f} mm; limit = {maximum_spacing:.1f} mm."
                + (" Beyond the limit the steel is not credited (Vs = 0; register I-17)." if beyond else ""),
                spacing_clause,
                check_id="ip.horizontal_shear_reinforcement_spacing",
                demand=wall.horizontal_reinforcement_spacing,
                limit=maximum_spacing,
                units="mm",
                comparison="<=",
                tolerance=1e-8,
            )
            if horizontal_type == "Bond beam bars":
                size = wall.horizontal_bar_size
                anchorage = wall.bond_beam_anchorage
                hook_too_large = anchorage == "standard_hook" and size not in HOOK_ANCHORED_BAR_SIZES
                if anchorage is None:
                    anchorage_text = ("End anchorage of the bond-beam bars not declared (standard stirrup hook for 15M and "
                                      "smaller, or mechanical anchorage developing fy); Vs is not credited.")
                elif hook_too_large:
                    anchorage_text = (f"A standard stirrup hook anchors 15M and smaller bars only; {size} bond-beam bars "
                                      "need mechanical anchorage developing fy; Vs is not credited.")
                elif anchorage == "standard_hook":
                    anchorage_text = f"{size} bars anchored by a standard stirrup hook around the end vertical bar (declared)."
                else:
                    anchorage_text = f"{size} bars anchored by mechanical anchorage developing fy (declared)."
                requirement(
                    anchorage is not None and not hook_too_large,
                    "Horizontal shear-reinforcement anchorage",
                    anchorage_text,
                    (_edition_clause(wall, "12.4.11.2(b)", "13.4.11.2 b)") if hook_too_large
                     else _edition_clause(wall, "12.4.11.2", "13.4.11.2")),
                    check_id="ip.horizontal_shear_reinforcement_anchorage",
                    unresolved="incomplete" if anchorage is None else "",
                )
            else:
                requirement(
                    wall.horizontal_anchorage_confirmed,
                    "Horizontal shear-reinforcement anchorage",
                    "Mechanical anchorage of the joint reinforcement developing fy at both wall ends is confirmed."
                    if wall.horizontal_anchorage_confirmed else
                    "Joint reinforcement is not credited until mechanical anchorage developing fy at both wall ends is confirmed.",
                    _edition_clause(wall, "12.4.11.2(b)", "13.4.11.2 b)"),
                    check_id="ip.horizontal_shear_reinforcement_anchorage",
                    kind="review",
                )
            requirement(
                wall.horizontal_continuity_confirmed,
                "Horizontal reinforcement continuity",
                "Continuity along the wall and at the top connection is confirmed." if wall.horizontal_continuity_confirmed else "Continuity along the wall and at the top connection has not been confirmed.",
                spacing_clause,
                check_id="ip.horizontal_reinforcement_continuity",
                kind="review",
            )
            if horizontal_type == "Bond beam bars":
                add(
                    detailing.horizontal_bar_diameter is not None
                    and detailing.horizontal_bar_diameter <= detailing.vertical_clearance_limit + 1.0e-8,
                    "Bond-beam bar fit in masonry space",
                    f"Bar diameter = {detailing.horizontal_bar_diameter:.1f} mm; limit = {detailing.vertical_clearance_limit:.1f} mm.",
                    "12.1.3" if wall.edition == "S304-14" else "13.1.3",
                    check_id="ip.bond_beam_bar_fit_in_masonry_space",
                    kind="numeric",
                    demand=detailing.horizontal_bar_diameter,
                    limit=detailing.vertical_clearance_limit,
                    units="mm",
                    comparison="<=",
                    tolerance=1e-8,
                )
                requirement(
                    wall.horizontal_reinforcement_area >= BAR_AREAS[wall.horizontal_bar_size] - 1.0e-8,
                    "Bond-beam reinforcement area",
                    f"Provided Av = {wall.horizontal_reinforcement_area:.1f} mm2; one {wall.horizontal_bar_size} bar requires {BAR_AREAS[wall.horizontal_bar_size]:.1f} mm2.",
                    "Reinforcement layout",
                    check_id="ip.bond_beam_reinforcement_area",
                    demand=wall.horizontal_reinforcement_area,
                    limit=BAR_AREAS[wall.horizontal_bar_size],
                    units="mm2",
                    comparison=">=",
                    tolerance=1e-8,
                )
                ld_horizontal = detailing.horizontal_development_required
                if ld_horizontal is None:
                    requirement(
                        False,
                        "Bond-beam-bar development",
                        f"{_bond_beam_layout(wall)[2]}; the development length cannot be formed and Vs is not credited "
                        "(dcs(b) = 2/3 of the c/c spacing of the bars being developed; the course spacing is not a "
                        "bar spacing).",
                        "12.4.2.3" if wall.edition == "S304-14" else "13.4.2.3",
                        check_id="ip.bond_beam_bar_development",
                        unresolved="incomplete",
                    )
                else:
                    requirement(
                        wall.available_horizontal_development >= ld_horizontal - 1.0e-8,
                        "Bond-beam-bar development",
                        f"Available = {wall.available_horizontal_development:.0f} mm; required = {ld_horizontal:.1f} mm "
                        f"({detailing.horizontal_development_basis}); {_k1_text(wall)}.",
                        "12.4.2.3 / 12.4.2.4" if wall.edition == "S304-14" else "13.4.2.3 / 13.4.2.4",
                        check_id="ip.bond_beam_bar_development",
                        demand=wall.available_horizontal_development,
                        limit=ld_horizontal,
                        units="mm",
                        comparison=">=",
                        tolerance=1e-8,
                    )
            if horizontal_type == "Joint reinforcement":
                joint_limit = min(5.0, wall.mortar_joint_thickness / 2.0)
                too_small = wall.joint_wire_diameter < 3.0
                add(
                    3.0 <= wall.joint_wire_diameter <= joint_limit + 1.0e-8,
                    "Joint-reinforcement wire diameter",
                    f"Diameter = {wall.joint_wire_diameter:.2f} mm; permitted range = 3.0 to {joint_limit:.2f} mm.",
                    "12.1.4" if wall.edition == "S304-14" else "13.1.4",
                    check_id="ip.joint_reinforcement_wire_diameter",
                    kind="numeric",
                    demand=wall.joint_wire_diameter,
                    limit=3.0 if too_small else joint_limit,
                    units="mm",
                    comparison=">=" if too_small else "<=",
                    tolerance=0.0 if too_small else 1e-8,
                )
                ld_wire = detailing.horizontal_development_required
                wire_clause = _edition_clause(wall, "12.4.1.3 / 12.4.2.1 / 12.4.2.3", "13.4.1.3 / 13.4.2.1 / 13.4.2.3")
                if ld_wire is None:
                    requirement(
                        False,
                        "Joint-reinforcement development",
                        "Type N mortar: the in-mortar f'gr for wire development is given only for Type S mortar (10 MPa); "
                        "declare joint_reinforcement_fgr. The joint reinforcement is not credited.",
                        wire_clause,
                        check_id="ip.joint_reinforcement_development",
                        unresolved="incomplete",
                    )
                else:
                    requirement(
                        wall.available_horizontal_development >= ld_wire - 1.0e-8,
                        "Joint-reinforcement development",
                        f"Available = {wall.available_horizontal_development:.0f} mm; required = {ld_wire:.1f} mm "
                        f"({detailing.horizontal_development_basis}).",
                        wire_clause,
                        check_id="ip.joint_reinforcement_development",
                        demand=wall.available_horizontal_development,
                        limit=ld_wire,
                        units="mm",
                        comparison=">=",
                        tolerance=1e-8,
                    )
        elif shear.required_horizontal_area is not None and shear.required_horizontal_area > 1.0e-8:
            add(
                False,
                "Required horizontal shear reinforcement",
                f"Av = {shear.required_horizontal_area:.1f} mm2 is required at s = {wall.horizontal_reinforcement_spacing:.0f} mm "
                "(the entered horizontal spacing), but no reinforcement type is selected (Av provided = 0).",
                "10.10.2.1" if wall.edition == "S304-14" else "11.10.2.1",
                check_id="ip.required_horizontal_shear_reinforcement",
                kind="numeric",
                demand=shear.required_horizontal_area,
                limit=0.0,
                units="mm2",
                comparison="<=",
                tolerance=0.0,
            )

    if shear.grout_assumed:
        add(
            False,
            "Grout type declared",
            "The S304:24 general shear route is required by axial tension and the grout type is not declared: "
            "fine grout (ga = 1.7) was used. Declare the grout type (register I-15).",
            "11.10.2.6.2",
            check_id="ip.grout_type_declared",
            kind="numeric",
            unresolved="incomplete",
        )

    # Column definition (S304-14 2.2; S304:24 3.1): h > 5(t + 10) and lw < 3(t + 10).
    # Partially grouted and hollow elements are not permitted as columns; a
    # fully grouted element is checked as a grouted-solid wall element (I-19).
    column_t = wall.thickness + 10.0
    column_height = wall.height if wall.unsupported_height is None else wall.unsupported_height
    tall = column_height > 5.0 * column_t + 1.0e-9
    column_sized = tall and wall.length < 3.0 * column_t - 1.0e-9
    restricted = wall.wall_type == "Partially grouted reinforced masonry" or (
        wall.wall_type == "Unreinforced masonry" and wall.unreinforced_condition == "Hollow ungrouted")
    geometry_text = (f"unsupported height h = {column_height:.0f} mm vs 5(t + 10) = {5.0 * column_t:.0f} mm; "
                     f"lw = {wall.length:.0f} mm vs 3(t + 10) = {3.0 * column_t:.0f} mm")
    if not column_sized:
        column_text = f"{geometry_text}: not column-sized."
    elif restricted:
        column_text = (f"{geometry_text}: column-sized element; partially grouted or hollow elements are not "
                       "permitted as columns (register I-19).")
    else:
        column_text = f"{geometry_text}: column-sized; fully grouted, checked as a grouted-solid wall element."
    if wall.wall_type == "Unreinforced masonry":
        column_clause = "2.2 (column); 7.1.2" if wall.edition == "S304-14" else "3.1 (column); 8.1.2"
    else:
        column_clause = "2.2 (column); 10.15.4.3" if wall.edition == "S304-14" else "3.1 (column); 11.15.4.3"
    add(
        not (column_sized and restricted),
        "Column dimension limits",
        column_text,
        column_clause,
        check_id="ip.column_dimension_limits",
        kind="numeric",
        **(dict(demand=wall.length, limit=3.0 * column_t, units="mm", comparison=">=", tolerance=1e-9)
           if restricted and tall else
           dict(demand=column_height, limit=5.0 * column_t, units="mm", comparison="<=", tolerance=1e-9)
           if restricted else {}),
    )

    if wall.wall_type == "Unreinforced masonry" and wall.horizontal_reinforcement_type != "None":
        unreinforced_horizontal_ratio = wall.horizontal_reinforcement_area / (
            wall.thickness * min(wall.horizontal_reinforcement_spacing, 4.0 * wall.thickness)
        )
        add(
            unreinforced_horizontal_ratio <= 0.02 + 1.0e-12,
            "Maximum horizontal reinforcement",
            f"Horizontal steel ratio = {100.0 * unreinforced_horizontal_ratio:.3f}%; limit = 2.000%.",
            "10.15.1.3 / 10.15.1.4 applied by analogy" if wall.edition == "S304-14" else "11.15.1.3 / 11.15.1.4 applied by analogy",
            check_id="ip.maximum_horizontal_reinforcement",
            kind="numeric",
            demand=unreinforced_horizontal_ratio,
            limit=0.02,
            units="ratio",
            comparison="<=",
            tolerance=1e-12,
        )
        unreinforced_h_limit = (
            600.0
            if wall.horizontal_reinforcement_type == "Joint reinforcement"
            else min(
                2400.0,
                0.5 * wall.length,
                0.5 * wall.height if wall.edition == "S304:24" else math.inf,
            )
        )
        add(
            wall.horizontal_reinforcement_spacing <= unreinforced_h_limit + 1.0e-8,
            "Horizontal reinforcement spacing",
            f"Type = {wall.horizontal_reinforcement_type}; s = {wall.horizontal_reinforcement_spacing:.1f} mm; limit = {unreinforced_h_limit:.1f} mm.",
            "10.15.1.3 / 10.15.1.4 applied by analogy" if wall.edition == "S304-14" else "11.15.1.3 / 11.15.1.4 applied by analogy",
            check_id="ip.horizontal_reinforcement_spacing",
            kind="numeric",
            demand=wall.horizontal_reinforcement_spacing,
            limit=unreinforced_h_limit,
            units="mm",
            comparison="<=",
            tolerance=1e-8,
        )
        add(
            wall.horizontal_anchorage_confirmed and wall.horizontal_continuity_confirmed,
            "Horizontal reinforcement continuity and anchorage",
            "Continuity and anchorage are confirmed." if wall.horizontal_anchorage_confirmed and wall.horizontal_continuity_confirmed else "Confirm continuity and anchorage before relying on horizontal reinforcement.",
            "10.15.1.3 / 10.15.1.4 applied by analogy" if wall.edition == "S304-14" else "11.15.1.3 / 11.15.1.4 applied by analogy",
            check_id="ip.horizontal_reinforcement_continuity_and_anchorage",
            kind="review",
        )
        if wall.horizontal_reinforcement_type == "Bond beam bars" and detailing.horizontal_development_required is not None:
            add(
                detailing.horizontal_bar_diameter is not None
                and detailing.horizontal_bar_diameter <= detailing.vertical_clearance_limit + 1.0e-8,
                "Bond-beam bar fit in masonry space",
                f"Bar diameter = {detailing.horizontal_bar_diameter:.1f} mm; limit = {detailing.vertical_clearance_limit:.1f} mm.",
                "12.1.3" if wall.edition == "S304-14" else "13.1.3",
                check_id="ip.bond_beam_bar_fit_in_masonry_space",
                kind="numeric",
                demand=detailing.horizontal_bar_diameter,
                limit=detailing.vertical_clearance_limit,
                units="mm",
                comparison="<=",
                tolerance=1e-8,
            )
            add(
                wall.available_horizontal_development >= detailing.horizontal_development_required - 1.0e-8,
                "Bond-beam-bar development",
                f"Available = {wall.available_horizontal_development:.0f} mm; required = "
                f"{detailing.horizontal_development_required:.1f} mm ({detailing.horizontal_development_basis}); "
                f"{_k1_text(wall)}.",
                "12.4.2.3" if wall.edition == "S304-14" else "13.4.2.3",
                check_id="ip.bond_beam_bar_development",
                kind="numeric",
                demand=wall.available_horizontal_development,
                limit=detailing.horizontal_development_required,
                units="mm",
                comparison=">=",
                tolerance=1e-8,
            )
        if wall.horizontal_reinforcement_type == "Joint reinforcement":
            joint_limit = min(5.0, wall.mortar_joint_thickness / 2.0)
            too_small = wall.joint_wire_diameter < 3.0
            add(
                3.0 <= wall.joint_wire_diameter <= joint_limit + 1.0e-8,
                "Joint-reinforcement wire diameter",
                f"Diameter = {wall.joint_wire_diameter:.2f} mm; permitted range = 3.0 to {joint_limit:.2f} mm.",
                "12.1.4" if wall.edition == "S304-14" else "13.1.4",
                check_id="ip.joint_reinforcement_wire_diameter",
                kind="numeric",
                demand=wall.joint_wire_diameter,
                limit=3.0 if too_small else joint_limit,
                units="mm",
                comparison=">=" if too_small else "<=",
                tolerance=0.0 if too_small else 1e-8,
            )

    if wall.bond_pattern == "Stack pattern":
        stack_spacing = 800.0 if wall.horizontal_reinforcement_type == "Bond beam bars" else 400.0
        stack_credited = _horizontal_area_credited(wall, section) > 0.0
        add(
            stack_credited
            and wall.horizontal_reinforcement_spacing <= stack_spacing,
            "Stack-pattern head-joint reinforcement",
            f"Continuous developed horizontal reinforcement is required; maximum spacing = {stack_spacing:.0f} mm"
            + ("." if stack_credited else f"; {shear.horizontal_credit_basis or 'no horizontal reinforcement'}."),
            "10.10.4" if wall.edition == "S304-14" else "11.10.4",
            check_id="ip.stack_pattern_head_joint_reinforcement",
            kind="numeric",
            **(dict(demand=wall.horizontal_reinforcement_spacing, limit=stack_spacing, units="mm", comparison="<=",
                    tolerance=0.0) if stack_credited else {}),
        )
        if wall.stack_joint_area is not None:
            maximum_crossing_area = wall.horizontal_reinforcement_area * (math.floor(wall.height / wall.horizontal_reinforcement_spacing) + 1)
            add(
                wall.stack_joint_area <= maximum_crossing_area + 1.0e-8,
                "Stack-joint crossing area consistency",
                f"Entered total = {wall.stack_joint_area:.1f} mm2; upper bound from wall height, Av and spacing = {maximum_crossing_area:.1f} mm2.",
                "Reinforcement layout",
                check_id="ip.stack_joint_crossing_area_consistency",
                kind="numeric",
                demand=wall.stack_joint_area,
                limit=maximum_crossing_area,
                units="mm2",
                comparison="<=",
                tolerance=1e-8,
            )
        stack_basis = ("Vr = phi_m 0.7 Ch (10.10.4)" if wall.edition == "S304-14"
                       else "Vr = mu Ch without phi_m, as printed in 11.10.4 (p.91) (register I-4)")
        add(
            wall.stack_vertical_shear is not None and wall.stack_joint_area is not None
            and wall.stack_vertical_shear <= (shear.stack_vertical_resistance or 0.0) + 1.0e-8,
            "Stack-pattern vertical joint shear",
            f"Vertical joint demand = {wall.stack_vertical_shear} kN; resistance = {shear.stack_vertical_resistance:.2f} kN "
            f"({stack_basis}). Enter the total area and vertical shear for the same assessed head-joint length.",
            "10.10.4" if wall.edition == "S304-14" else "11.10.4",
            "resistance",
            check_id="ip.stack_pattern_vertical_joint_shear",
            kind="numeric",
            demand=wall.stack_vertical_shear,
            limit=shear.stack_vertical_resistance,
            units="kN",
            comparison="<=",
            tolerance=1e-8,
        )

    if section.face_shell_thickness is not None:
        add(
            section.face_shell_declared,
            "Unit face-shell thickness declared",
            f"tf = {section.face_shell_thickness:.1f} mm for the ungrouted effective area ({section.face_shell_source})."
            + ("" if section.face_shell_declared else
               " Declare the unit's face-shell thickness: CSA A165.1 was not supplied and the reference value is unverified."),
            ("10.3; Table 4 Note 2" if wall.edition == "S304-14" else "11.3; 3.1 (minimum face-shell thickness)")
            if wall.wall_type != "Unreinforced masonry" else ("7.3" if wall.edition == "S304-14" else "8.3; 3.1"),
            check_id="ip.unit_face_shell_declared",
            kind="numeric",
            unresolved="incomplete",
        )
    if wall.wall_type == "Partially grouted reinforced masonry":
        tf_text = ("" if section.face_shell_thickness is None else
                   f" tf used in this edition = {section.face_shell_thickness:.1f} mm ({section.face_shell_source}).")
        add(
            wall.unit_geometry_confirmed,
            "Partial-grout unit geometry",
            ("Face-shell thickness, cell width, mortar-bedded area, and grout layout are confirmed." if wall.unit_geometry_confirmed else "Confirm face-shell thickness, cell width, mortar-bedded area, and grout layout.")
            + tf_text,
            "Table 4 Notes 2-3" if wall.edition == "S304-14" else "Table 4 Note 3",
            check_id="ip.partial_grout_unit_geometry",
            kind="review",
        )
    if section.bar_positions or wall.horizontal_reinforcement_area > 0.0:
        add(False, "A371 reinforcement clearance and placement",
            "A371 clearance and placement limits have not been verified because that standard was not supplied. "
            "Confirm the applicable construction requirements independently; the numeric S304 bar-fit checks "
            "do not establish A371 compliance (register I-21).",
            "CSA A371 (not supplied)", check_id="ip.a371_clearance_and_placement", kind="review")
    grout_weight, grout_status, grout_text = flexure.bond_beam_grout
    if grout_status is not None:
        add(
            grout_status == "declared",
            "Self-weight basis",
            (f"Self-weight = {self_weight:.2f} kN including the {grout_text}." if grout_status == "declared" else
             f"Self-weight = {self_weight:.2f} kN without bond-beam grout: {grout_text}. The case layer uses this weight "
             "for D_cnt (Pd) and the factored dead-load rows."),
            "2.2 (bond beam); NBC 4.1.4.1" if wall.edition == "S304-14" else "3.1 (bond beam); NBC 4.1.4.1",
            check_id="ip.self_weight_basis",
            kind="numeric",
            unresolved="incomplete",
        )

    add(
        wall.base_connection_confirmed,
        "Wall-to-foundation connection",
        "Base force transfer and reinforcement continuity are confirmed." if wall.base_connection_confirmed else "Confirm base force transfer, dowels, development, and foundation interface details.",
        "Wall-level connection",
        check_id="ip.wall_to_foundation_connection",
        kind="review",
    )
    if not wall.free_standing:
        add(
            wall.top_connection_confirmed,
            "Wall-to-floor/roof connection",
            "Top lateral-load transfer and vertical continuity are confirmed." if wall.top_connection_confirmed else "Confirm top lateral-load transfer, anchorage, and vertical continuity.",
            "Wall-level connection",
            check_id="ip.wall_to_floor_roof_connection",
            kind="review",
        )
    add(
        stability.oop_check_provided,
        "Concurrent minor-axis/OOP check supplied",
        "OOP utilization is linked to this wall-level result." if stability.oop_check_provided else "Run the OOP wall tool for the same axial load and section, then enter its utilization.",
        "7.8 / 10.8" if wall.edition == "S304-14" else "8.8 / 11.8",
        "external",
        check_id="ip.concurrent_minor_axis_oop_check_supplied",
        kind="diagnostic",
    )
    if stability.oop_check_provided:
        add(
            stability.oop_stability_passed,
            "Weak-axis slenderness and stability",
            "The linked OOP stability check passes." if stability.oop_stability_passed else "The linked OOP stability check does not pass.",
            "7.8 / 10.8" if wall.edition == "S304-14" else "8.8 / 11.8",
            "external",
            check_id="ip.weak_axis_slenderness_and_stability",
            kind="diagnostic",
        )
        add(
            stability.combined_utilization is not None
            and stability.combined_utilization <= 1.0 + 1.0e-8,
            "Conservative major/minor interaction screen",
            (
                f"In-plane utilization + OOP utilization = {stability.combined_utilization:.3f}; limit = 1.000."
                if stability.combined_utilization is not None
                else "No combined resistance is available at the entered axial load and moment."
            ),
            "Rational wall-level interaction screen",
            "external",
            check_id="ip.conservative_major_minor_interaction_screen",
            kind="diagnostic",
        )

    # Seismic gate (plan WP3; register I-22). The case layer declares the SFRS
    # role, the earthquake case and the 4.1.8.1 exemption; the engine never
    # evaluates NBC eligibility itself.
    seismic_clause = "16" if wall.edition == "S304-14" else "17"
    provisions = SEISMIC_PROVISIONS[wall.edition]
    if wall.seismic_exempt_4_1_8_1:
        add_check(
            "pass",
            "Seismic provisions",
            (f"NBC 4.1.8.1 simplified procedure declared with verified eligibility: the seismic requirements of the "
             f"design standard (Clause {seismic_clause}) do not apply, NBC 4.1.8.1.(15) (register I-22)."),
            "NBC 4.1.8.1.(15)",
            check_id="ip.seismic_provisions_not_evaluated",
            kind="info",
        )
    elif _seismic_gate_active(wall):
        reasons = []
        if wall.sfrs_wall is None:
            reasons.append("the SFRS declaration of the wall is missing (declare whether it is part of the SFRS)")
        elif wall.sfrs_wall:
            reasons.append("the wall is declared part of the SFRS")
        if wall.earthquake_case:
            reasons.append("this case has a non-zero earthquake factor")
        add(
            False,
            "Seismic provisions not evaluated",
            (f"Clause {seismic_clause} seismic design provisions are not implemented, and {' and '.join(reasons)}: "
             f"{provisions}. This is a coverage gap that no review record clears; only the NBC 4.1.8.1 simplified "
             "procedure with verified eligibility (4.1.8.1.(15)) removes it (register I-22)."),
            seismic_clause,
            check_id="ip.seismic_provisions_not_evaluated",
            kind="numeric",
            unresolved="incomplete" if wall.sfrs_wall is None else "unsupported",
        )
    if _seismic_gate_active(wall):
        if wall.bond_pattern == "Stack pattern":
            add(
                False,
                "Seismic running bond",
                (f"Stack pattern: shear walls designed to Clause {seismic_clause} shall be built in 50 % running bond "
                 f"unless testing or analysis shows otherwise ({seismic_clause}.1.2); record the test or analysis "
                 "reference that permits stack pattern."),
                f"{seismic_clause}.1.2",
                check_id="ip.seismic_running_bond",
                kind="review",
            )
        if reinforced and _ties_qualify(wall):
            longitudinal = BAR_PROPERTIES[wall.bar_size][1]
            hinge_limit = min(6.0 * longitudinal, 24.0 * wall.tie_diameter, 0.5 * wall.thickness)
            add(
                False,
                "Seismic compression-steel ties",
                (f"Compression steel is credited with ties at {wall.tie_spacing:g} mm (12.2.1 / 13.2.1). In the plastic "
                 f"hinge region of an Rd 2.0 or 3.0 wall, {seismic_clause}.6.5 requires the {seismic_clause}.11 "
                 f"buckling-prevention ties (hoops or seismic cross ties) at s <= min(6 db, 24 dt, 0.5 t) = "
                 f"{hinge_limit:.1f} mm with the {seismic_clause}.11.6 Ash; confirm that the section is outside the "
                 "hinge region or the wall is Rd 1.5, or that such ties are provided."),
                f"{seismic_clause}.6.5 / {seismic_clause}.11.4",
                check_id="ip.seismic_compression_steel_ties",
                kind="review",
            )
    if wall.wall_type == "Unreinforced masonry":
        permitted = wall.urm_seismic_permitted or wall.seismic_exempt_4_1_8_1
        add(
            permitted,
            "URM seismic permission",
            ("Unreinforced shear walls declared permitted for the site and building (category / hazard index and "
             "height)." if wall.urm_seismic_permitted else
             "NBC 4.1.8.1 simplified procedure with the 4.1.8.1.(4) URM limits verified." if permitted else
             (("Unreinforced loadbearing or lateral-load-resisting walls are not permitted where IE Fa Sa(0.2) >= 0.35 "
               "(4.5.1, 16.2.1)" if wall.edition == "S304-14" else
               "Unreinforced loadbearing or lateral-load-resisting walls are not permitted in Seismic Category SC3 "
               "or SC4 (5.5.1, 17.2.1)")
              + ", nor above the NBC Table 4.1.8.9 URM heights or for High Importance / post-disaster buildings "
                "(4.1.8.10); confirm from the declared seismic data that URM is permitted (register I-31).")),
            ("4.5.1; 16.2.1; NBC Table 4.1.8.9" if wall.edition == "S304-14" else "5.5.1; 17.2.1; NBC Table 4.1.8.9"),
            check_id="ip.urm_seismic_permission",
            kind="review",
        )
    infill_clause = "10.12; 7.13" if wall.edition == "S304-14" else "11.12; 8.13"
    if wall.wall_role == "infill_shear_wall":
        add(
            False,
            "Infill shear wall role",
            ("Infill shear walls are not evaluated: Pd = 0 in shear, strut sliding Pstrut, frame anchorage, the "
             "no-opening / no-gap condition and the diagonal-strut design are not implemented (register I-30)."),
            infill_clause,
            check_id="ip.infill_shear_wall_role",
            kind="numeric",
            unresolved="unsupported",
        )
    else:
        add(
            wall.wall_role is not None,
            "Infill shear wall role",
            (f"Wall role declared: {wall.wall_role.replace('_', ' ')} (not an infill panel)." if wall.wall_role is not None
             else "Declare the wall role (loadbearing, non-loadbearing or infill shear wall); infill panels are "
                  "evaluated differently (Pd = 0, strut action; register I-30)."),
            infill_clause,
            check_id="ip.infill_shear_wall_role",
            kind="numeric",
            unresolved="incomplete",
        )
    pd_basis = _pd(wall)[2]
    checks.append(
        Check(
            "pass",
            "Factored dead-load compression",
            (f"Pd = {pd:.2f} kN ({pd_basis}); dead-load factor {PD_DEAD_LOAD_FACTOR} in every combination "
             "(S304-14 2.3 / 10.10.2.1; S304:24 Clause 3); NBC 2025 4.1.3.2.(5) 1.0D in load case 5 not "
             f"applied (register I-1). Calculated wall self-weight = {self_weight:.2f} kN."
             if pd is not None else
             f"Pd not supplied: {PD_REQUIRED}. Calculated wall self-weight = {self_weight:.2f} kN."),
            "7.10 / 10.10" if wall.edition == "S304-14" else "8.10 / 11.10",
            check_id="ip.factored_dead_load_compression",
            kind="info",
        )
    )
    if pd is not None and pd > wall.factored_axial + 1.0e-6:
        checks.append(
            Check(
                "pass",
                "Case Pd above Pf",
                f"Pd = {pd:.2f} kN exceeds Pf = {wall.factored_axial:.2f} kN. Confirm that this case's "
                "tensile lateral axial load is included in Pd; the composition cannot be checked from Pd alone.",
                "7.10 / 10.10" if wall.edition == "S304-14" else "8.10 / 11.10",
                check_id="ip.case_pd_above_pf",
                kind="info",
            )
        )
    scope_clauses = (("bearing 10.13, corner-bar columns 10.9, toothed joints 10.1.3" if wall.edition == "S304-14"
                      else "bearing 11.13, corner-bar columns 11.9, toothed joints 11.1.3"))
    checks.append(
        Check(
            "pass",
            "Solid rectangular wall scope",
            (f"Excluded by scope: flanged walls, openings, building-level load distribution, {scope_clauses}. Seismic "
             "provisions are reported by ip.seismic_provisions_not_evaluated."),
            "Declared scope",
            check_id="ip.solid_rectangular_wall_scope",
            kind="info",
        )
    )
    return tuple(checks)


def _k1_text(wall: WallInput) -> str:
    """Bar-location factor k1 of the bond-beam bars and its declared basis (register I-16)."""
    if wall.horizontal_top_bar:
        return "k1 = 1.3 (declared: more than 300 mm of fresh grout below the bars; register I-16)"
    return "k1 = 1.0 (declared: 300 mm or less of fresh grout below the bars; register I-16)"


def _fmt(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def demand_utilization(demand: float, resistance: float | None) -> float | None:
    if resistance is None or resistance < 0.0:
        return None
    return demand / resistance if resistance > 0.0 else (0.0 if demand == 0.0 else None)


def _moment_resistance_at_demand(
    wall: WallInput, section: SectionProperties
) -> tuple[float | None, str, InteractionPoint | None]:
    """(Mr at Pf or None, reason, section point). The reason is "ok",
    "outside_axial_range" or "deep_section_model_outside_range" (register I-10)."""
    axial = wall.factored_axial
    tension_cap = _tension_cap(wall, section)
    if axial < -tension_cap or axial > _axial_cap(wall, section):
        return None, "outside_axial_range", None
    if wall.wall_type == "Unreinforced masonry":
        point = _unreinforced_point(wall, section, axial)
        return point.m, "ok", point
    if axial == -tension_cap:
        return 0.0, "ok", None
    kdev = _development_factor(wall, section)
    lower = 1.0e-9
    upper = wall.length / min(beta_one(s.fm) for s in section.segments)
    while section_point_at_c(wall, upper, section, development_factor=kdev).p < axial:
        upper *= 2.0
    for _ in range(70):
        middle = (lower + upper) / 2.0
        if section_point_at_c(wall, middle, section, development_factor=kdev).p < axial:
            lower = middle
        else:
            upper = middle
    point = section_point_at_c(wall, (lower + upper) / 2.0, section, development_factor=kdev)
    if not point.valid:
        return None, "deep_section_model_outside_range", point
    return point.m, "ok", point


def calculate_wall(wall: WallInput) -> WallResult:
    physical_section = section_properties(wall, "Discrete")
    discrete_curve, discrete_cap = interaction_curve(wall, physical_section)
    discrete_mr, discrete_reason, discrete_point = _moment_resistance_at_demand(wall, physical_section)
    section = physical_section
    curve = discrete_curve
    cap = discrete_cap
    moment_resistance = discrete_mr
    moment_reason, moment_point = discrete_reason, discrete_point
    alternate_curve: tuple[InteractionPoint, ...] | None = None
    alternate_mr: float | None = None
    curve_method = "Discrete"
    alternate_curve_method = None
    if wall.wall_type == "Partially grouted reinforced masonry":
        alternate_curve_method = "Smeared"
        smeared_section = section_properties(wall, "Smeared")
        smeared_curve, smeared_cap = interaction_curve(wall, smeared_section)
        smeared_mr, smeared_reason, smeared_point = _moment_resistance_at_demand(wall, smeared_section)
        if wall.partial_grout_method == "Smeared":
            curve_method, alternate_curve_method = "Smeared", "Discrete"
            section, curve, cap, moment_resistance = (
                smeared_section,
                smeared_curve,
                smeared_cap,
                smeared_mr,
            )
            moment_reason, moment_point = smeared_reason, smeared_point
            alternate_curve, alternate_mr = discrete_curve, discrete_mr
        elif wall.partial_grout_method == "Discrete":
            alternate_curve, alternate_mr = smeared_curve, smeared_mr
        else:
            alternate_curve, alternate_mr = smeared_curve, smeared_mr
            cap = min(discrete_cap, smeared_cap)
            if discrete_mr is None or smeared_mr is None:
                moment_resistance = None
                if discrete_mr is None:
                    moment_reason, moment_point = discrete_reason, discrete_point
                else:
                    moment_reason, moment_point = smeared_reason, smeared_point
            elif smeared_mr < discrete_mr:
                curve_method, alternate_curve_method = "Smeared", "Discrete"
                section, curve, moment_resistance = smeared_section, smeared_curve, smeared_mr
                moment_reason, moment_point = smeared_reason, smeared_point
                alternate_curve, alternate_mr = discrete_curve, discrete_mr
            else:
                moment_resistance = discrete_mr
    tension_cap = _tension_cap(wall, section)
    kdev = _development_factor(wall, physical_section)
    pm_utilization = demand_utilization(wall.factored_moment, moment_resistance)
    self_weight = _wall_self_weight(wall, physical_section)
    bond_beam_grout = _bond_beam_grout(wall, physical_section)
    pd, _, pd_basis = _pd(wall)
    shear = _shear_result(wall, physical_section, pd)
    if wall.horizontal_reinforcement_type != "None" and wall.horizontal_reinforcement_area > 0.0:
        # Vr with no horizontal steel decides whether the steel is required
        # for in-plane shear (10.15.1.4 / 11.15.1.4; register I-17, F-066).
        try:
            bare = _shear_result(wall, physical_section, pd, credit_horizontal=False).resistance
        except CalculationError:
            # e.g. the S304:24 General route without steel needs the undeclared
            # grout type: the no-steel Vr is unknown and the requirement undecided.
            bare = None
        required = (True if wall.bond_pattern == "Stack pattern" else None if bare is None
                    else wall.factored_shear > bare + 1.0e-8)
    else:
        bare = shear.resistance
        required = None if bare is None else wall.factored_shear > bare + 1.0e-8
    shear = replace(shear, resistance_without_horizontal_steel=bare, horizontal_steel_required=required)
    sliding = _sliding_result(wall, physical_section, pd)
    detailing = _detailing_result(wall, physical_section)
    sliding_utilization = demand_utilization(wall.factored_shear, sliding.resistance)
    stability = StabilityResult(
        combined_utilization=(
            pm_utilization + wall.oop_utilization
            if pm_utilization is not None and wall.oop_check_provided
            else None
        ),
        oop_check_provided=wall.oop_check_provided,
        oop_stability_passed=wall.oop_stability_passed,
    )
    flexure = _Flexure(
        reason=moment_reason,
        point=moment_point,
        curve_method=curve_method,
        alternate_method=alternate_curve_method,
        alternate_mr=alternate_mr,
        kdev=kdev,
        vertical_ld=detailing.vertical_development_required,
        bond_beam_grout=bond_beam_grout,
    )
    checks = _checks(
        wall,
        physical_section,
        cap,
        tension_cap,
        moment_resistance,
        shear,
        sliding,
        detailing,
        stability,
        self_weight,
        pd,
        flexure,
    )
    reinforced = wall.wall_type != "Unreinforced masonry"
    flex_clause = ("7" if wall.edition == "S304-14" else "8") if not reinforced else ("10" if wall.edition == "S304-14" else "11")
    shear_clause = (
        "7.10" if wall.wall_type == "Unreinforced masonry" else "10.10"
    ) if wall.edition == "S304-14" else (
        "8.10" if wall.wall_type == "Unreinforced masonry" else "11.10"
    )
    table4_row = "; Table 4 row '30 or more'" if wall.nominal_unit_strength >= 30.0 else ""
    if not reinforced:
        mr_equation = "elastic/cracked section envelope"
    else:
        mr_equation = "strain-compatible P-M curve; reduced arm for squat walls (register I-10)"
        if wall.wall_type == "Partially grouted reinforced masonry":
            mr_equation += (f"; {curve_method} section"
                            + (" (Table 4 Note 3: grouted and ungrouted parts treated separately)" if curve_method == "Discrete"
                               else " (tool idealization)")
                            + ("" if alternate_mr is None else f"; {alternate_curve_method} Mr = {alternate_mr:.3f} kN-m")
                            + ("; lower of the two: tool-side conservative envelope"
                               if wall.partial_grout_method == "Use lower of discrete and smeared" else "")
                            + " (register I-9)")
        if moment_reason == "deep_section_model_outside_range":
            mr_equation += f"; not defined: {moment_point.note if moment_point else ''}"
    squat = reinforced and wall.height / wall.length < 1.0
    grout_weight, grout_status, grout_text = bond_beam_grout
    self_weight_equation = "unit material plus grout volume"
    if grout_status == "declared":
        self_weight_equation += f"; includes the {grout_text}"
    elif grout_status == "undeclared":
        self_weight_equation += "; bond-beam course grout not included (courses not declared)"
    trace = [
        TraceEntry("f'm,ug", f"{section.fm_ungrouted:.3f}", "MPa", "CSA Table 4 concrete-block value" + table4_row, "Table 4"),
        TraceEntry("f'm,g", f"{section.fm_grouted:.3f}", "MPa", "CSA Table 4 grouted-block value" + table4_row, "Table 4"),
        TraceEntry("f'm,eff", f"{section.weighted_fm:.3f}", "MPa", "sum(f'm,i Ai) / Ae", "Table 4 partial-grout note"),
        TraceEntry("Ae", f"{section.area:.1f}", "mm2", "mortar-bedded area plus grouted void area", f"{flex_clause}.3"),
        TraceEntry("tf (Ae)", _fmt(section.face_shell_thickness, 1), "mm",
                   section.face_shell_source or "no ungrouted segment in this section", f"{flex_clause}.3"),
        TraceEntry("least clear cell", _fmt(wall.least_clear_cell_dimension, 1), "mm", "declared least clear dimension of the reinforced cell",
                   "12.1.3" if wall.edition == "S304-14" else "13.1.3"),
        TraceEntry("gamma_g", f"{section.gamma_g:.4f}", "-", "1.0 fully grouted; otherwise min(Ae/Ag, 0.5)", shear_clause),
        TraceEntry("Pr,max", f"{cap:.3f}", "kN", "0.80 [sum(0.85 phi_m f'm,i Ai) + eligible compression-steel net force]", f"{flex_clause}.4"),
        TraceEntry("Mr(Pf)", "-" if moment_resistance is None else f"{moment_resistance:.3f}", "kN-m", mr_equation, f"{flex_clause}.2"),
    ]
    if reinforced:
        trace.append(TraceEntry(
            "kdev", f"{kdev:.4f}", "-",
            f"min(1, available {wall.available_vertical_development:.0f} mm / ld {detailing.vertical_development_required:.1f} mm): "
            "tension-bar stress <= kdev fy in Mr and Tr; S304:24 dowel force x kdev; S304-14 dowels all-or-nothing "
            "(register I-13)",
            "12.4.1.1; 12.5.4.4(b)" if wall.edition == "S304-14" else "13.4.1.1; 13.5.4.4 b); 11.10.2.6.3 c)"))
    if squat:
        trace.append(TraceEntry(
            "d (squat)", _fmt(moment_point.reduced_depth if moment_point is not None else None, 1), "mm",
            _squat_text(wall, moment_point), "10.2.8" if wall.edition == "S304-14" else "11.2.8"))
    trace += [
        TraceEntry("dv", f"{shear.effective_depth:.1f}", "mm", shear.effective_depth_basis, shear_clause),
        TraceEntry("M/(Vdv)", f"{shear.moment_shear_ratio:.4f}", "-", "limited to 0.25 through 1.0", shear_clause),
        TraceEntry("Vm", _fmt(shear.masonry_resistance), "kN", shear.method, shear_clause),
        TraceEntry("Vs", f"{shear.steel_resistance:.3f}", "kN",
                   shear.method + (f"; {shear.horizontal_credit_basis}" if shear.horizontal_credit_basis else ""), shear_clause),
        TraceEntry("Vr", _fmt(shear.resistance), "kN", "min(Vm + Vs, Vr,max)", shear_clause),
        TraceEntry("Vr (no horizontal steel)", _fmt(shear.resistance_without_horizontal_steel), "kN",
                   "Vr with Av = 0: horizontal steel required for in-plane shear when Vf exceeds it "
                   f"({ {True: 'required', False: 'not required', None: 'undecided'}[shear.horizontal_steel_required]}; "
                   "register I-17)",
                   "10.15.1.4" if wall.edition == "S304-14" else "11.15.1.4"),
        TraceEntry("self-weight", f"{self_weight:.3f}", "kN", self_weight_equation, "wall-level dead load"),
        TraceEntry("bond-beam grout", f"{grout_weight:.3f}", "kN",
                   grout_text or "no bond-beam course grout (no bond beams, or no ungrouted length)",
                   "2.2 (bond beam)" if wall.edition == "S304-14" else "3.1 (bond beam)"),
        TraceEntry("Pd", _fmt(pd), "kN", f"{pd_basis}; Pd_case = {PD_DEAD_LOAD_FACTOR} D_cnt + min(0, N_lat - N_cb) + N_cb, "
                   "factor 0.9 in every combination (register I-1); Vm uses 0.25 max(0, Pd)", shear_clause),
        TraceEntry("beta", "-" if shear.beta is None else f"{shear.beta:.6f}", "-", shear.method, shear_clause),
        TraceEntry("theta", "-" if shear.theta_degrees is None else f"{shear.theta_degrees:.3f}", "deg", shear.method, shear_clause),
        TraceEntry("epsilon_x", "-" if shear.epsilon_x is None else f"{shear.epsilon_x:.7f}", "-",
                   "S304:24 general shear method" + (f"; {shear.epsilon_x_basis}" if shear.epsilon_x_basis else ""), shear_clause),
        TraceEntry("ze", "-" if shear.ze is None else f"{shear.ze:.3f}", "mm",
                   "S304:24 general shear method" + (f"; {shear.ze_basis}" if shear.ze_basis else ""), shear_clause),
        TraceEntry("Vr,slide", _fmt(sliding.resistance), "kN", sliding.method, shear_clause + ".5"),
        TraceEntry("Vr,vertical joint", "-" if shear.stack_vertical_resistance is None else f"{shear.stack_vertical_resistance:.3f}", "kN", "stack-pattern vertical joint resistance; checked separately from horizontal Vf", shear_clause + ".4"),
        TraceEntry("vertical ld", f"{detailing.vertical_development_required:.3f}", "mm",
                   "CSA straight deformed-bar development" + (f"; {detailing.vertical_development_basis}"
                                                               if detailing.vertical_development_basis else ""),
                   "12.4.2.3" if wall.edition == "S304-14" else "13.4.2.3"),
        TraceEntry("horizontal ld", _fmt(detailing.horizontal_development_required), "mm",
                   (f"joint-wire development in mortar; {detailing.horizontal_development_basis}"
                    if wall.horizontal_reinforcement_type == "Joint reinforcement" else
                    "CSA straight deformed-bar development of the bond-beam bars"
                    + (f"; {detailing.horizontal_development_basis}" if detailing.horizontal_development_basis else "")
                    + f"; {_k1_text(wall)}"),
                   "12.4.2.3; 12.4.2.5(a)" if wall.edition == "S304-14" else "13.4.2.3; 13.4.2.5(a)"),
    ]
    if detailing.lap_required is not None:
        trace.append(TraceEntry("lap required", f"{detailing.lap_required:.3f}", "mm", detailing.lap_basis,
                                "12.5.4.2; 12.5.5.1" if wall.edition == "S304-14" else "13.5.4.2; 13.5.5.1"))
    return WallResult(
        wall=wall,
        section=section,
        curve=curve,
        alternate_curve=alternate_curve,
        axial_cap=cap,
        tension_cap=tension_cap,
        moment_resistance=moment_resistance,
        alternate_moment_resistance=alternate_mr,
        pm_utilization=pm_utilization,
        shear=shear,
        sliding=sliding,
        detailing=detailing,
        stability=stability,
        self_weight=self_weight,
        pd=pd,
        sliding_resistance=sliding.resistance,
        sliding_utilization=sliding_utilization,
        checks=checks,
        trace=tuple(trace),
        curve_method=curve_method,
        alternate_curve_method=alternate_curve_method,
        pd_basis=pd_basis,
        moment_resistance_reason=moment_reason,
        development_factor=kdev,
        bond_beam_grout_weight=grout_weight,
    )


__all__ = [
    "BAR_AREAS",
    "BAR_PROPERTIES",
    "BOND_BEAM_ANCHORAGES",
    "HOOK_ANCHORED_BAR_SIZES",
    "INTERPRETATIONS",
    "JOINT_WIRE_MORTAR_FGR",
    "PD_DEAD_LOAD_FACTOR",
    "SEISMIC_PROVISIONS",
    "TABLE_LD_COEFFICIENT",
    "TABLE_LD_OTHER_CASES",
    "TENSION_HALF_BOUNDARY_BAR",
    "UNIT_FACE_SHELL_REFERENCE",
    "WALL_ROLES",
    "CalculationError",
    "MissingWeightDeclaration",
    "UnsupportedWeightInterval",
    "Check",
    "DetailingResult",
    "DevelopmentLength",
    "DowelArea",
    "developed_dowel_area",
    "InteractionPoint",
    "SectionProperties",
    "ShearResult",
    "SlidingResult",
    "StabilityResult",
    "TraceEntry",
    "WallInput",
    "WallResult",
    "WallWeightInterval",
    "beta_one",
    "calculate_wall",
    "demand_utilization",
    "interaction_curve",
    "masonry_strengths",
    "moment_at_axial_load",
    "resolved_face_shell_thickness",
    "section_point_at_c",
    "section_properties",
    "ties_qualify",
    "validate_bond_beam_courses",
    "wall_self_weight",
    "wall_weight_between",
]
