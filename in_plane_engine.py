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

from dataclasses import dataclass
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
                      "S304:24 outside masonry surface; S304-14 'closest masonry surface' = grouted-cell wall; table route offered",
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
    partial_grout_method: str = "Use lower of discrete and smeared"
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
    horizontal_anchorage_confirmed: bool = False
    horizontal_continuity_confirmed: bool = False
    horizontal_top_bar: bool = False
    shear_method: str = "Use lower of simplified and general"
    # Printed April 2024 Clause 8.10.2.3 omits sqrt(f'm). The June 2025
    # corrections list that clause but their wording has not been read here;
    # the square-root form is a proposed correction, off by default.
    apply_proposed_8_10_2_3_correction: bool = False
    grout_type: str = "Coarse"
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
    masonry_density: float = 2100.0
    grout_density: float = 2350.0
    unit_solid_fraction: float = 0.56
    sliding_friction: float = 1.0
    sliding_plane: str = "Base/support joint"
    uncracked_area_fraction: float = 0.5
    vertical_bars_developed_at_base: bool = True
    developed_vertical_bar_count: int | None = None
    in_situ_grout_strength: float = 20.0
    least_clear_cell_dimension: float = 100.0
    epoxy_coated_vertical_bars: bool = False
    available_vertical_development: float = 1200.0
    available_horizontal_development: float = 600.0
    splice_present: bool = False
    splice_class: str = "B"
    class_a_splice_confirmed: bool = False
    provided_lap_length: float = 1200.0
    vertical_bars_continuous: bool = True
    available_termination_extension: float = 5000.0
    joint_wire_diameter: float = 3.65
    mortar_joint_thickness: float = 10.0
    base_connection_confirmed: bool = False
    top_connection_confirmed: bool = False
    oop_check_provided: bool = False
    oop_utilization: float = 0.0
    oop_stability_passed: bool = False


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


@dataclass(frozen=True)
class SlidingResult:
    resistance: float | None
    reinforced_route: float | None
    bond_route: float | None
    uncracked_area: float
    required_dowel_area: float | None
    developed_vertical_area: float
    method: str


@dataclass(frozen=True)
class DetailingResult:
    vertical_development_required: float
    horizontal_development_required: float | None
    lap_required: float | None
    termination_extension_required: float
    vertical_clearance_limit: float
    vertical_bar_diameter: float
    horizontal_bar_diameter: float | None


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
    moment_resistance_reason: str = "ok"


class CalculationError(ValueError):
    pass


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
        "least clear cell dimension": wall.least_clear_cell_dimension,
        "available vertical development": wall.available_vertical_development,
        "available horizontal development": wall.available_horizontal_development,
        "provided lap length": wall.provided_lap_length,
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
    if wall.grout_type not in ("Coarse", "Fine"):
        raise CalculationError("Choose coarse or fine grout.")
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


def _tension_cap(wall: WallInput, section: SectionProperties) -> float:
    return (
        PHI_S
        * len(section.bar_positions)
        * BAR_AREAS[wall.bar_size]
        * wall.fy
        / 1000.0
    )


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
    return mass * 9.81 / 1000.0


def wall_self_weight(wall: WallInput) -> float:
    """Physical wall weight above the checked section, in kN."""
    return _wall_self_weight(wall, section_properties(wall, "Discrete"))


PD_REQUIRED = ("case Pd required: 0.9 x counteracting dead load plus the signed lateral axial load of "
               "this case (S304-14 2.3 / 10.10.2.1; S304:24 Clause 3); not supplied, so this check is "
               "not evaluated (register I-2)")


def _pd(wall: WallInput) -> tuple[float | None, bool, str]:
    """(signed case Pd in kN or None, supplied, basis). No fallback: the engine
    cannot know the case's lateral axial load (register I-2)."""
    if wall.dead_load_compression is None:
        return None, False, "not supplied"
    return wall.dead_load_compression, True, wall.pd_basis or "supplied case Pd"


def _development_length(
    wall: WallInput,
    section: SectionProperties,
    *,
    horizontal: bool,
) -> float | None:
    if not horizontal and not section.bar_positions:
        return None
    if horizontal and wall.horizontal_reinforcement_type != "Bond beam bars":
        return None
    if horizontal and wall.horizontal_reinforcement_area <= 0.0:
        return None
    size = wall.bar_size
    area, diameter = BAR_PROPERTIES[size]
    fy = wall.fy
    spacing = section.bar_spacing or wall.length
    if horizontal:
        area, diameter = BAR_PROPERTIES[wall.horizontal_bar_size]
        fy = wall.horizontal_fy
        spacing = wall.horizontal_reinforcement_spacing
    if area <= 0.0 or diameter <= 0.0:
        return None
    # The clear-space bound is conservative for the centred-bar model. Ktr = 0:
    # Av alone does not establish Atr or n across a bond-splitting plane.
    dcs = min(wall.least_clear_cell_dimension / 2.0, wall.thickness / 2.0, 2.0 * spacing / 3.0)
    if not horizontal:
        dcs = min(dcs, wall.end_bar_distance)
    denominator = min(dcs, 2.5 * diameter)
    k1 = 1.3 if horizontal and wall.horizontal_top_bar else 1.0
    if wall.epoxy_coated_vertical_bars and not horizontal:
        clear_cover = max((wall.least_clear_cell_dimension - diameter) / 2.0, 0.0)
        k2 = 1.5 if clear_cover < 3.0 * diameter or spacing - diameter < 6.0 * diameter else 1.2
    else:
        k2 = 1.0
    k3 = 0.8 if (wall.horizontal_bar_size if horizontal else wall.bar_size) != "25M" else 1.0
    return max(
        1.15
        * min(k1 * k2, 1.7)
        * k3
        * fy
        * area
        / (denominator * math.sqrt(wall.in_situ_grout_strength)),
        300.0,
    )


def _detailing_result(
    wall: WallInput,
    section: SectionProperties,
) -> DetailingResult:
    vertical_ld = _development_length(wall, section, horizontal=False) or 0.0
    horizontal_ld = _development_length(wall, section, horizontal=True)
    lap_required = (
        max(300.0, vertical_ld * (1.0 if wall.splice_class == "A" else 1.3))
        if wall.splice_present
        else None
    )
    _, vertical_diameter = BAR_PROPERTIES[wall.bar_size]
    horizontal_diameter = None
    if wall.horizontal_reinforcement_type == "Joint reinforcement":
        horizontal_diameter = wall.joint_wire_diameter
    elif wall.horizontal_reinforcement_type == "Bond beam bars" and wall.horizontal_reinforcement_area > 0.0:
        horizontal_diameter = BAR_PROPERTIES[wall.horizontal_bar_size][1]
    extreme_depth = max(section.bar_positions) if section.bar_positions else 0.0
    return DetailingResult(
        vertical_development_required=vertical_ld,
        horizontal_development_required=horizontal_ld,
        lap_required=lap_required,
        termination_extension_required=max(extreme_depth, 12.0 * vertical_diameter),
        vertical_clearance_limit=wall.least_clear_cell_dimension / 2.0,
        vertical_bar_diameter=vertical_diameter,
        horizontal_bar_diameter=horizontal_diameter,
    )


def _horizontal_area_credited(wall: WallInput, section: SectionProperties) -> float:
    if not (wall.horizontal_anchorage_confirmed and wall.horizontal_continuity_confirmed):
        return 0.0
    if wall.horizontal_reinforcement_type == "Bond beam bars":
        ld = _development_length(wall, section, horizontal=True)
        if (
            ld is None or wall.available_horizontal_development < ld
            or wall.horizontal_reinforcement_area < BAR_AREAS[wall.horizontal_bar_size]
            or BAR_PROPERTIES[wall.horizontal_bar_size][1] > wall.least_clear_cell_dimension / 2.0
        ):
            return 0.0
    elif wall.horizontal_reinforcement_type == "Joint reinforcement":
        if not 3.0 <= wall.joint_wire_diameter <= min(5.0, wall.mortar_joint_thickness / 2.0):
            return 0.0
    else:
        return 0.0
    return wall.horizontal_reinforcement_area


def section_point_at_c(
    wall: WallInput,
    neutral_axis: float,
    section: SectionProperties | None = None,
) -> InteractionPoint:
    """Return the strain-compatible strong-axis resistance at one neutral axis."""

    section = section or section_properties(wall)
    if neutral_axis <= 0.0 or not math.isfinite(neutral_axis):
        raise CalculationError("Neutral-axis depth must be positive and finite.")

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
    for position in section.bar_positions:
        strain = EPS_MU * (position - neutral_axis) / neutral_axis
        if strain > 0.0:
            stress = min(wall.fy, E_S * strain)
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
    if squat:
        design_depth = min(0.67 * wall.length, 0.70 * wall.height)
        if wall.edition == "S304:24":
            tensile_centroid = sum(x for x, _ in tension) / len(tension)
            design_depth = min(design_depth, tensile_centroid)
    else:
        design_depth = None

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
    if not tension and axial_force > 0.0:
        moment_nmm = min(
            moment_nmm,
            axial_force * ECCENTRICITY_LIMIT * wall.length,
        )
    return InteractionPoint(
        p=axial_force / 1000.0,
        m=max(moment_nmm, 0.0) / 1_000_000.0,
        c=neutral_axis,
        branch=branch,
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
    )


def _reinforced_curve(
    wall: WallInput,
    section: SectionProperties,
    samples: int,
) -> tuple[tuple[InteractionPoint, ...], float]:
    cap = _axial_cap(wall, section)
    tension_cap = _tension_cap(wall, section)
    c_max = wall.length / min(beta_one(s.fm) for s in section.segments)
    if _ties_qualify(wall):
        c_max = max(c_max, max(section.bar_positions) * EPS_MU / (EPS_MU - wall.fy / E_S))
    depths = {c_max * i / samples for i in range(1, samples + 1)}
    yield_strain = wall.fy / E_S
    for position in section.bar_positions:
        depths.update((position, position * EPS_MU / (EPS_MU + yield_strain)))
        if _ties_qualify(wall) and yield_strain < EPS_MU:
            depths.add(position * EPS_MU / (EPS_MU - yield_strain))
    for segment in section.segments:
        depths.update((segment.x0 / beta_one(segment.fm), segment.x1 / beta_one(segment.fm)))
    raw = [section_point_at_c(wall, c, section) for c in sorted(depths) if 0.0 < c <= c_max]
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


def _shear_result(
    wall: WallInput,
    section: SectionProperties,
    pd_kN: float | None,
) -> ShearResult:
    dv = 0.8 * wall.length
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
    av = _horizontal_area_credited(wall, section)
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
            zs = (
                candidate_zs
                if vertical_area * developed_fraction >= 0.002 * wall.thickness * candidate_zs
                else dv
            )
            beta_simplified = (
                0.18
                if av >= minimum_av_2024
                else 230.0 / (1000.0 + 1.4 * zs)
            )
            theta_simplified = 42.0
            ze = (
                300.0
                if av >= minimum_av_2024
                else (1.4 if wall.grout_type == "Coarse" else 1.7) * zs
            )
            tension_half_area = developed_fraction * BAR_AREAS[wall.bar_size] * sum(
                position >= section.centroid for position in section.bar_positions
            )
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
                epsilon_x = min(0.003, 2.0 * epsilon_x)
            beta_general = 0.40 / (1.0 + 1500.0 * epsilon_x) * 1300.0 / (1000.0 + ze)
            theta_general = 29.0 + 7000.0 * epsilon_x

        simplified = capacity(beta_simplified, theta_simplified)
        general = capacity(beta_general, theta_general)
        simplified_resistance = simplified[0] / 1000.0
        general_resistance = general[0] / 1000.0
        if wall.factored_axial < 0.0:
            selected = general
            beta, theta = beta_general, theta_general
            method = "S304:24 general route required by axial tension"
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
                "8.10.2.3 with sqrt(f'm) restored, not verified against the corrected text"
            )
        elif wall.edition == "S304:24":
            vm = 0.16 * (2.0 - ratio)  # Printed April 2024 Clause 8.10.2.3 omits sqrt(f'm).
            method = "S304:24 unreinforced shear route; printed April 2024 8.10.2.3 without sqrt(f'm)"
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
    )


def _sliding_result(
    wall: WallInput,
    section: SectionProperties,
    pd_kN: float | None,
) -> SlidingResult:
    developed_count = 0
    if wall.wall_type != "Unreinforced masonry" and wall.vertical_bars_developed_at_base:
        developed_count = (
            wall.developed_vertical_bar_count
            if wall.developed_vertical_bar_count is not None
            else len(section.bar_positions)
        )
    developed_area = developed_count * BAR_AREAS[wall.bar_size]
    ld = _development_length(wall, section, horizontal=False) or 0.0
    if wall.available_vertical_development < ld:
        developed_area = 0.0
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
                             f"{method}; {PD_REQUIRED}")
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
        method=f"{method}; {basis}; C = max(0, Pd + phi_s As,dev fy)",
    )


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
) -> tuple[Check, ...]:
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

    flex_prefix = ("7" if wall.edition == "S304-14" else "8") if wall.wall_type == "Unreinforced masonry" else ("10" if wall.edition == "S304-14" else "11")
    flex_clause = f"{flex_prefix}.2 / {flex_prefix}.4"
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
            f"|Pf| = {-wall.factored_axial:.2f} kN; Tr,max = {tension_cap:.2f} kN.",
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
    add(
        moment_resistance is not None
        and wall.factored_moment <= moment_resistance + 1.0e-8,
        "Axial load plus in-plane moment",
        (
            f"Mf = {wall.factored_moment:.2f} kN-m; "
            + (
                f"Mr(Pf) = {moment_resistance:.2f} kN-m."
                if moment_resistance is not None
                else "no resistance is defined at this axial load."
            )
        ),
        flex_clause,
        "resistance",
        check_id="ip.axial_load_plus_in_plane_moment",
        kind="numeric",
        demand=wall.factored_moment,
        limit=moment_resistance,
        units="kNm",
        comparison="<=",
        tolerance=1e-8,
    )
    shear_clause = "7.10 / 10.10" if wall.edition == "S304-14" else "8.10 / 11.10"
    add(
        shear.resistance is not None and wall.factored_shear <= shear.resistance + 1.0e-8,
        "Diagonal shear",
        f"Vf = {wall.factored_shear:.2f} kN; "
        + (f"Vr = {shear.resistance:.2f} kN." if shear.resistance is not None else f"Vr not evaluated: {PD_REQUIRED}."),
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
        if wall.wall_type != "Unreinforced masonry"
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

    if wall.wall_type != "Unreinforced masonry":
        assert section.bar_spacing is not None
        area = BAR_AREAS[wall.bar_size]
        minimum = 0.00125 * wall.thickness * min(
            section.bar_spacing, 4.0 * wall.thickness
        )
        add(
            area >= minimum - 1.0e-8,
            "Minimum vertical reinforcement",
            f"As,bar = {area:.1f} mm2; minimum = {minimum:.1f} mm2 at s = {section.bar_spacing:.1f} mm.",
            "10.15.1.1" if wall.edition == "S304-14" else "11.15.1.1",
            check_id="ip.minimum_vertical_reinforcement",
            kind="numeric",
            demand=area,
            limit=minimum,
            units="mm2",
            comparison=">=",
            tolerance=1e-8,
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
            f"Available = {wall.available_vertical_development:.0f} mm; required = {detailing.vertical_development_required:.0f} mm.",
            "12.4.2.3" if wall.edition == "S304-14" else "13.4.2.3",
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
            add(
                wall.provided_lap_length >= detailing.lap_required - 1.0e-8,
                f"Class {wall.splice_class} tension lap splice",
                f"Provided = {wall.provided_lap_length:.0f} mm; required = {detailing.lap_required:.0f} mm.",
                "12.5.4.2" if wall.edition == "S304-14" else "13.5.4.2",
                check_id="ip.class_tension_lap_splice",
                kind="numeric",
                demand=wall.provided_lap_length,
                limit=detailing.lap_required,
                units="mm",
                comparison=">=",
                tolerance=1e-8,
            )
        add(
            wall.vertical_bars_continuous
            or wall.available_termination_extension >= detailing.termination_extension_required - 1.0e-8,
            "Vertical-bar continuity or termination extension",
            (
                "Bars are continuous over the wall height."
                if wall.vertical_bars_continuous
                else f"Available extension = {wall.available_termination_extension:.0f} mm; required = {detailing.termination_extension_required:.0f} mm."
            ),
            "12.4.8.3" if wall.edition == "S304-14" else "13.4.8.3",
            check_id="ip.vertical_bar_continuity_or_termination_extension",
            kind="numeric",
        )
        if wall.compression_reinforcement_tied:
            _, longitudinal_diameter = BAR_PROPERTIES[wall.bar_size]
            tie_limit = min(
                16.0 * longitudinal_diameter,
                48.0 * wall.tie_diameter,
                wall.thickness,
            )
            add(
                _ties_qualify(wall),
                "Compression-reinforcement ties",
                f"Tie diameter = {wall.tie_diameter:.2f} mm (minimum 3.65); spacing = {wall.tie_spacing:.0f} mm (limit {tie_limit:.0f}); tie arrangement confirmed = {wall.tie_arrangement_confirmed}. Compression steel is credited only if all conditions pass.",
                "12.2" if wall.edition == "S304-14" else "13.2",
                check_id="ip.compression_reinforcement_ties",
                kind="numeric",
            )

        if wall.horizontal_reinforcement_type == "Bond beam bars":
            maximum_spacing = min(2400.0, 0.5 * wall.length)
            if wall.edition == "S304:24":
                maximum_spacing = min(maximum_spacing, 0.5 * wall.height)
        elif wall.horizontal_reinforcement_type == "Joint reinforcement":
            maximum_spacing = 600.0
        else:
            maximum_spacing = math.inf
        if wall.horizontal_reinforcement_type != "None":
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
            )
            add(
                wall.horizontal_reinforcement_spacing <= maximum_spacing + 1.0e-8,
                "Horizontal shear-reinforcement spacing",
                f"Type = {wall.horizontal_reinforcement_type}; s = {wall.horizontal_reinforcement_spacing:.1f} mm; limit = {maximum_spacing:.1f} mm.",
                "10.15.1.4" if wall.edition == "S304-14" else "11.15.1.4",
                check_id="ip.horizontal_shear_reinforcement_spacing",
                kind="numeric",
                demand=wall.horizontal_reinforcement_spacing,
                limit=maximum_spacing,
                units="mm",
                comparison="<=",
                tolerance=1e-8,
            )
            add(
                wall.horizontal_anchorage_confirmed,
                "Horizontal shear-reinforcement anchorage",
                "Anchorage at both wall ends is confirmed." if wall.horizontal_anchorage_confirmed else "Horizontal steel is not credited until anchorage at both wall ends is confirmed.",
                "12.4.11" if wall.edition == "S304-14" else "13.4.11",
                check_id="ip.horizontal_shear_reinforcement_anchorage",
                kind="review",
            )
            add(
                wall.horizontal_continuity_confirmed,
                "Horizontal reinforcement continuity",
                "Continuity along the wall and at the top connection is confirmed." if wall.horizontal_continuity_confirmed else "Continuity along the wall and at the top connection has not been confirmed.",
                "10.15.1.4" if wall.edition == "S304-14" else "11.15.1.4",
                check_id="ip.horizontal_reinforcement_continuity",
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
                    wall.horizontal_reinforcement_area >= BAR_AREAS[wall.horizontal_bar_size] - 1.0e-8,
                    "Bond-beam reinforcement area",
                    f"Provided Av = {wall.horizontal_reinforcement_area:.1f} mm2; one {wall.horizontal_bar_size} bar requires {BAR_AREAS[wall.horizontal_bar_size]:.1f} mm2.",
                    "Reinforcement layout",
                    check_id="ip.bond_beam_reinforcement_area",
                    kind="numeric",
                    demand=wall.horizontal_reinforcement_area,
                    limit=BAR_AREAS[wall.horizontal_bar_size],
                    units="mm2",
                    comparison=">=",
                    tolerance=1e-8,
                )
                add(
                    wall.available_horizontal_development >= detailing.horizontal_development_required - 1.0e-8,
                    "Bond-beam-bar development",
                    f"Available = {wall.available_horizontal_development:.0f} mm; required = {detailing.horizontal_development_required:.0f} mm.",
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
                add(
                    3.0 <= wall.joint_wire_diameter <= joint_limit + 1.0e-8,
                    "Joint-reinforcement wire diameter",
                    f"Diameter = {wall.joint_wire_diameter:.2f} mm; permitted range = 3.0 to {joint_limit:.2f} mm.",
                    "12.1.4" if wall.edition == "S304-14" else "13.1.4",
                    check_id="ip.joint_reinforcement_wire_diameter",
                    kind="numeric",
                )
        elif shear.required_horizontal_area is not None and shear.required_horizontal_area > 1.0e-8:
            add(
                False,
                "Required horizontal shear reinforcement",
                f"Av = {shear.required_horizontal_area:.1f} mm2 is required at s = {wall.horizontal_reinforcement_spacing:.0f} mm, but no reinforcement type is selected.",
                "10.10.2.1" if wall.edition == "S304-14" else "11.10.2.1",
                check_id="ip.required_horizontal_shear_reinforcement",
                kind="numeric",
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
                f"Available = {wall.available_horizontal_development:.0f} mm; required = {detailing.horizontal_development_required:.0f} mm.",
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
            add(
                3.0 <= wall.joint_wire_diameter <= joint_limit + 1.0e-8,
                "Joint-reinforcement wire diameter",
                f"Diameter = {wall.joint_wire_diameter:.2f} mm; permitted range = 3.0 to {joint_limit:.2f} mm.",
                "12.1.4" if wall.edition == "S304-14" else "13.1.4",
                check_id="ip.joint_reinforcement_wire_diameter",
                kind="numeric",
            )

    if wall.bond_pattern == "Stack pattern":
        stack_spacing = 800.0 if wall.horizontal_reinforcement_type == "Bond beam bars" else 400.0
        add(
            _horizontal_area_credited(wall, section) > 0.0
            and wall.horizontal_reinforcement_spacing <= stack_spacing,
            "Stack-pattern head-joint reinforcement",
            f"Continuous developed horizontal reinforcement is required; maximum spacing = {stack_spacing:.0f} mm.",
            "10.10.4" if wall.edition == "S304-14" else "11.10.4",
            check_id="ip.stack_pattern_head_joint_reinforcement",
            kind="numeric",
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
        add(
            wall.stack_vertical_shear is not None and wall.stack_joint_area is not None
            and wall.stack_vertical_shear <= (shear.stack_vertical_resistance or 0.0) + 1.0e-8,
            "Stack-pattern vertical joint shear",
            f"Vertical joint demand = {wall.stack_vertical_shear} kN; resistance = {shear.stack_vertical_resistance:.2f} kN. Enter the total area and vertical shear for the same assessed head-joint length.",
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
    checks.append(
        Check(
            "pass",
            "Solid rectangular wall scope",
            "Flanged walls, openings, building-level load distribution, and seismic system classification are excluded by scope.",
            "Declared scope",
            check_id="ip.solid_rectangular_wall_scope",
            kind="info",
        )
    )
    return tuple(checks)


def _fmt(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def demand_utilization(demand: float, resistance: float | None) -> float | None:
    if resistance is None or resistance < 0.0:
        return None
    return demand / resistance if resistance > 0.0 else (0.0 if demand == 0.0 else None)


def _moment_resistance_at_demand(wall: WallInput, section: SectionProperties) -> float | None:
    axial = wall.factored_axial
    tension_cap = _tension_cap(wall, section)
    if axial < -tension_cap or axial > _axial_cap(wall, section):
        return None
    if wall.wall_type == "Unreinforced masonry":
        return _unreinforced_point(wall, section, axial).m
    if axial == -tension_cap:
        return 0.0
    lower = 1.0e-9
    upper = wall.length / min(beta_one(s.fm) for s in section.segments)
    while section_point_at_c(wall, upper, section).p < axial:
        upper *= 2.0
    for _ in range(70):
        middle = (lower + upper) / 2.0
        if section_point_at_c(wall, middle, section).p < axial:
            lower = middle
        else:
            upper = middle
    return section_point_at_c(wall, (lower + upper) / 2.0, section).m


def calculate_wall(wall: WallInput) -> WallResult:
    physical_section = section_properties(wall, "Discrete")
    discrete_curve, discrete_cap = interaction_curve(wall, physical_section)
    discrete_mr = _moment_resistance_at_demand(wall, physical_section)
    section = physical_section
    curve = discrete_curve
    cap = discrete_cap
    moment_resistance = discrete_mr
    alternate_curve: tuple[InteractionPoint, ...] | None = None
    alternate_mr: float | None = None
    curve_method = "Discrete"
    alternate_curve_method = None
    if wall.wall_type == "Partially grouted reinforced masonry":
        alternate_curve_method = "Smeared"
        smeared_section = section_properties(wall, "Smeared")
        smeared_curve, smeared_cap = interaction_curve(wall, smeared_section)
        smeared_mr = _moment_resistance_at_demand(wall, smeared_section)
        if wall.partial_grout_method == "Smeared":
            curve_method, alternate_curve_method = "Smeared", "Discrete"
            section, curve, cap, moment_resistance = (
                smeared_section,
                smeared_curve,
                smeared_cap,
                smeared_mr,
            )
            alternate_curve, alternate_mr = discrete_curve, discrete_mr
        elif wall.partial_grout_method == "Discrete":
            alternate_curve, alternate_mr = smeared_curve, smeared_mr
        else:
            alternate_curve, alternate_mr = smeared_curve, smeared_mr
            cap = min(discrete_cap, smeared_cap)
            if discrete_mr is None or smeared_mr is None:
                moment_resistance = None
            elif smeared_mr < discrete_mr:
                curve_method, alternate_curve_method = "Smeared", "Discrete"
                section, curve, moment_resistance = smeared_section, smeared_curve, smeared_mr
                alternate_curve, alternate_mr = discrete_curve, discrete_mr
            else:
                moment_resistance = discrete_mr
    tension_cap = _tension_cap(wall, section)
    pm_utilization = demand_utilization(wall.factored_moment, moment_resistance)
    self_weight = _wall_self_weight(wall, physical_section)
    pd, _, pd_basis = _pd(wall)
    shear = _shear_result(wall, physical_section, pd)
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
    )
    flex_clause = ("7" if wall.edition == "S304-14" else "8") if wall.wall_type == "Unreinforced masonry" else ("10" if wall.edition == "S304-14" else "11")
    shear_clause = (
        "7.10" if wall.wall_type == "Unreinforced masonry" else "10.10"
    ) if wall.edition == "S304-14" else (
        "8.10" if wall.wall_type == "Unreinforced masonry" else "11.10"
    )
    table4_row = "; Table 4 row '30 or more'" if wall.nominal_unit_strength >= 30.0 else ""
    trace = (
        TraceEntry("f'm,ug", f"{section.fm_ungrouted:.3f}", "MPa", "CSA Table 4 concrete-block value" + table4_row, "Table 4"),
        TraceEntry("f'm,g", f"{section.fm_grouted:.3f}", "MPa", "CSA Table 4 grouted-block value" + table4_row, "Table 4"),
        TraceEntry("f'm,eff", f"{section.weighted_fm:.3f}", "MPa", "sum(f'm,i Ai) / Ae", "Table 4 partial-grout note"),
        TraceEntry("Ae", f"{section.area:.1f}", "mm2", "mortar-bedded area plus grouted void area", f"{flex_clause}.3"),
        TraceEntry("tf (Ae)", _fmt(section.face_shell_thickness, 1), "mm",
                   section.face_shell_source or "no ungrouted segment in this section", f"{flex_clause}.3"),
        TraceEntry("least clear cell", f"{wall.least_clear_cell_dimension:.1f}", "mm", "declared least clear dimension of the reinforced cell",
                   "12.1.3" if wall.edition == "S304-14" else "13.1.3"),
        TraceEntry("gamma_g", f"{section.gamma_g:.4f}", "-", "1.0 fully grouted; otherwise min(Ae/Ag, 0.5)", shear_clause),
        TraceEntry("Pr,max", f"{cap:.3f}", "kN", "0.80 [sum(0.85 phi_m f'm,i Ai) + eligible compression-steel net force]", f"{flex_clause}.4"),
        TraceEntry("Mr(Pf)", "-" if moment_resistance is None else f"{moment_resistance:.3f}", "kN-m", "elastic/cracked section envelope" if wall.wall_type == "Unreinforced masonry" else "strain-compatible P-M curve; reduced arm for squat walls", f"{flex_clause}.2"),
        TraceEntry("dv", f"{shear.effective_depth:.1f}", "mm", "0.8 lw", shear_clause),
        TraceEntry("M/(Vdv)", f"{shear.moment_shear_ratio:.4f}", "-", "limited to 0.25 through 1.0", shear_clause),
        TraceEntry("Vm", _fmt(shear.masonry_resistance), "kN", shear.method, shear_clause),
        TraceEntry("Vs", f"{shear.steel_resistance:.3f}", "kN", shear.method, shear_clause),
        TraceEntry("Vr", _fmt(shear.resistance), "kN", "min(Vm + Vs, Vr,max)", shear_clause),
        TraceEntry("self-weight", f"{self_weight:.3f}", "kN", "unit material plus grout volume", "wall-level dead load"),
        TraceEntry("Pd", _fmt(pd), "kN", f"{pd_basis}; Pd_case = {PD_DEAD_LOAD_FACTOR} D_cnt + min(0, N_lat - N_cb) + N_cb, "
                   "factor 0.9 in every combination (register I-1); Vm uses 0.25 max(0, Pd)", shear_clause),
        TraceEntry("beta", "-" if shear.beta is None else f"{shear.beta:.6f}", "-", shear.method, shear_clause),
        TraceEntry("theta", "-" if shear.theta_degrees is None else f"{shear.theta_degrees:.3f}", "deg", shear.method, shear_clause),
        TraceEntry("epsilon_x", "-" if shear.epsilon_x is None else f"{shear.epsilon_x:.7f}", "-", "S304:24 general shear method", shear_clause),
        TraceEntry("ze", "-" if shear.ze is None else f"{shear.ze:.3f}", "mm", "S304:24 general shear method", shear_clause),
        TraceEntry("Vr,slide", _fmt(sliding.resistance), "kN", sliding.method, shear_clause + ".5"),
        TraceEntry("Vr,vertical joint", "-" if shear.stack_vertical_resistance is None else f"{shear.stack_vertical_resistance:.3f}", "kN", "stack-pattern vertical joint resistance; checked separately from horizontal Vf", shear_clause + ".4"),
        TraceEntry("vertical ld", f"{detailing.vertical_development_required:.3f}", "mm", "CSA straight deformed-bar development", "12.4.2.3" if wall.edition == "S304-14" else "13.4.2.3"),
    )
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
        trace=trace,
        curve_method=curve_method,
        alternate_curve_method=alternate_curve_method,
        pd_basis=pd_basis,
    )


__all__ = [
    "BAR_AREAS",
    "BAR_PROPERTIES",
    "INTERPRETATIONS",
    "PD_DEAD_LOAD_FACTOR",
    "TENSION_HALF_BOUNDARY_BAR",
    "UNIT_FACE_SHELL_REFERENCE",
    "WALL_ROLES",
    "CalculationError",
    "Check",
    "DetailingResult",
    "InteractionPoint",
    "SectionProperties",
    "ShearResult",
    "SlidingResult",
    "StabilityResult",
    "TraceEntry",
    "WallInput",
    "WallResult",
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
    "wall_self_weight",
]
