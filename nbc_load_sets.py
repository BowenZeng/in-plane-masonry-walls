"""NBC 2025 load sets shared by the in-plane and out-of-plane layers.

Dependency-free. The canonical copy lives in the in-plane package; the
out-of-plane package holds a byte-identical mirror written by
tools/sync_load_sets.py (a root test asserts the identity).

ULS: NBC 2025 Division B Table 4.1.3.2.-A (printed p.4-4, PDF 452) with
Sentences 4.1.3.2.(2)-(10), the companion-zero rows of 4.1.3.2.(3), the
1.25D / 0.9D variants of 4.1.3.2.(5), the storage increase of 4.1.3.2.(7),
Note (6) / Article 4.1.5.5 (p.4-11, PDF 459) for roof live load and parking
decks, lateral earth pressure 1.5H in cases 2 to 4 (Note A-4.1.3.2.(4)) and
the soil factors of 4.1.3.2.(8)-(9). SLS: Table 4.1.3.4 (p.4-6, PDF 454) with its
companion-zero rows, Note (2) storage increase and H at 1.0 (4.1.3.4.(4)).
Crane loads (Table 4.1.3.2.-B) are excluded. P and T are not generated for
S304 designs (4.1.3.2.(4)).

Factors are magnitudes. W and E rows are expanded for both signs by
expand_directions(); the fixed parts W_fixed and E_fixed take the row's W / E
factor and are never reversed. Load types other than the base types are
parts that exist only when declared (LoadConditions.parts).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations, product
import math
from typing import Mapping, Sequence

BASE_TYPES = ("D", "L", "S", "W", "E", "H", "Other")
# Declared parts: partition allowance and soil (4.1.3.2.(8), 4.1.4.1.(5)-(6)),
# separately entered roof live load and parking-deck loads (4.1.5.5), and the
# non-reversible parts of W and E (uplift, one-sided pressure).
PART_TYPES = ("D_partition", "D_soil", "L_roof", "L_deck", "S_deck", "W_fixed", "E_fixed")
LOAD_TYPES = BASE_TYPES + PART_TYPES
REVERSIBLE = ("W", "E")
FIXED_PART = {"W": "W_fixed", "E": "E_fixed"}

# Importance factors of the ULS-specified snow and wind loads (NBC 2025
# Tables 4.1.6.2.-A and 4.1.7.3.); SLS uses 0.6 for every category, so an
# entered ULS-specified S or W is converted by 0.6 / I_ULS.
IMPORTANCE_ULS = {"Low": 0.8, "Normal": 1.0, "High": 1.15, "Post-disaster": 1.25}
SLS_IMPORTANCE = 0.6
OCCUPANCIES = ("ordinary", "storage_equipment_or_service_room")
ROOF_LIVE_LOADS = ("none", "included_in_L", "separate")
PARKING_DECKS = ("none", "vehicle_accessible", "long_term_storage")
STORAGE_INCREMENT = 0.5  # 4.1.3.2.(7) on companion L; Table 4.1.3.4 Note (2): 0.35L -> 0.5L


@dataclass(frozen=True)
class LoadConditions:
    """The declarations that decide the content of the NBC 2025 rows."""
    importance_category: str
    uls_specified: bool
    occupancy: str
    roof_live_load: str
    parking_deck: str
    parts: frozenset = frozenset()
    soil_depth_m: float | None = None
    soil_resisting: bool = False
    coupling_beam_axial: bool = False
    code: str = "NBC 2025"
    soil_saturated: bool | None = None

    def problems(self) -> list[str]:
        out = []
        if self.code != "NBC 2025":
            out.append(f"Load basis {self.code!r}: only NBC 2025 is supported.")
        if self.importance_category not in IMPORTANCE_ULS:
            out.append(f"Importance category must be one of {', '.join(IMPORTANCE_ULS)}.")
        if self.occupancy not in OCCUPANCIES:
            out.append(f"Occupancy must be one of {', '.join(OCCUPANCIES)}.")
        if self.roof_live_load not in ROOF_LIVE_LOADS:
            out.append(f"Roof live load must be one of {', '.join(ROOF_LIVE_LOADS)}.")
        if self.parking_deck not in PARKING_DECKS:
            out.append(f"Parking deck must be one of {', '.join(PARKING_DECKS)}.")
        unknown = set(self.parts) - set(PART_TYPES)
        if unknown:
            out.append(f"Unknown load parts: {', '.join(sorted(unknown))}.")
        if (self.roof_live_load == "separate") != ("L_roof" in self.parts):
            out.append("A separate roof live load needs the L_roof contribution (and only then).")
        deck = self.parking_deck != "none"
        if deck != ("L_deck" in self.parts and "S_deck" in self.parts):
            out.append("A parking deck needs the L_deck and S_deck contributions (and only then).")
        if "D_soil" in self.parts and self.soil_depth_m is not None and (
                not math.isfinite(self.soil_depth_m) or self.soil_depth_m <= 0.0):
            out.append("Soil depth must be positive.")
        if self.soil_saturated is not None and type(self.soil_saturated) is not bool:
            out.append("Soil saturation must be true, false or undeclared.")
        return out

    @property
    def importance_factor(self) -> float:
        return IMPORTANCE_ULS[self.importance_category]


@dataclass(frozen=True)
class FactorRow:
    row_id: str
    name: str
    factors: Mapping[str, float]
    state: str = "ULS"
    case: int = 0
    tags: tuple[str, ...] = ()
    enabled: bool = True


def soil_factor(depth_m: float | None) -> float:
    """4.1.3.2.(8): 1.5 on soil in place of 1.25, or 1 + 0.6/hs but not less than 1.25 for hs > 1.2 m."""
    if depth_m is not None and depth_m > 1.2:
        return max(1.25, 1.0 + 0.6 / depth_m)
    return 1.5


def _text(factors: Mapping[str, float]) -> str:
    terms = []
    for key in LOAD_TYPES:
        value = factors.get(key, 0.0)
        if value:
            sign = " +/- " if key in REVERSIBLE and terms else " + " if terms else ""
            terms.append(f"{sign}{value:g}{key}")
    return "".join(terms)


def _with_dead(factors: dict, dead: float, conditions: LoadConditions, *, case: int, state: str) -> dict:
    """Factors of the declared dead-load parts for a row with dead-load factor `dead`."""
    parts = conditions.parts
    out = dict(factors, D=dead)
    if "D_partition" in parts:
        # The partition allowance is not counteracting dead load (4.1.4.1.(5)).
        out["D_partition"] = 0.0 if (state == "ULS" and dead == 0.9) else dead
    if "D_soil" in parts:
        if state == "ULS" and case == 1:
            out["D_soil"] = 1.5 if conditions.soil_saturated else 1.4
        elif state == "SLS" or case == 5:
            out["D_soil"] = dead
        elif dead == 1.25:
            out["D_soil"] = soil_factor(conditions.soil_depth_m)
        else:  # 0.9D rows: counteractive soil only when it is part of the load-resisting system (4.1.4.1.(6))
            out["D_soil"] = 0.9 if conditions.soil_resisting else 0.0
    return out


def _with_fixed(factors: dict, conditions: LoadConditions) -> dict:
    out = dict(factors)
    for key, part in FIXED_PART.items():
        if part in conditions.parts and out.get(key):
            out[part] = out[key]
    return out


def uls_set(conditions: LoadConditions) -> tuple[FactorRow, ...]:
    """NBC 2025 Table 4.1.3.2.-A rows for the declared conditions (21 formulas for one L and one S)."""
    problems = conditions.problems()
    if "D_soil" in conditions.parts and conditions.soil_saturated is None:
        problems.append("Declare whether D_soil is saturated before generating NBC ULS rows (4.1.3.2.(9)).")
    if problems:
        raise ValueError("NBC 2025 load conditions: " + " ".join(problems))
    parts = conditions.parts
    roof_separate = "L_roof" in parts
    deck = conditions.parking_deck != "none"
    long_term = conditions.parking_deck == "long_term_storage"
    l_parts = ["L"] + (["L_roof"] if roof_separate else []) + (["L_deck"] if deck else [])
    s_parts = ["S"] + (["S_deck"] if deck else [])
    store = STORAGE_INCREMENT if conditions.occupancy == "storage_equipment_or_service_room" else 0.0

    def companion_l(base, *, roof, deck_l):
        out = {}
        for p in l_parts:
            if (p == "L_roof" and not roof) or (p == "L_deck" and not deck_l):
                continue
            out[p] = base + (store if p == "L" else 0.0)
        return out

    rows: list[tuple[str, str, int, dict, tuple]] = [("N1", "1.4D", 1, {"D": 1.4}, ("principal D",))]
    for dead, tag in ((1.25, "a"), (0.9, "b")):
        all_l = {p: 1.5 for p in l_parts}
        s_companion = {p: (0.7 if (p == "S" or long_term) else 0.2) for p in s_parts}
        l_with_s = {p: 1.5 for p in l_parts if p != "L_roof"}  # the roof carries S, not L (4.1.5.5.(2))
        all_s = {p: 1.0 for p in s_parts}
        rows += [
            (f"N2{tag}0", None, 2, dict(all_l), ("principal L", "companion-zero")),
            (f"N2{tag}S", None, 2, {**l_with_s, **s_companion}, ("principal L", "companion S", "Note (6)")),
            (f"N2{tag}W", None, 2, {**all_l, "W": 0.3}, ("principal L", "companion W")),
            (f"N3{tag}0", None, 3, dict(all_s), ("principal S", "companion-zero")),
            (f"N3{tag}L", None, 3, {**all_s, **companion_l(1.0, roof=False, deck_l=long_term)},
             ("principal S", "companion L", "Note (6)")),
            (f"N3{tag}W", None, 3, {**all_s, "W": 0.3}, ("principal S", "companion W")),
            (f"N4{tag}0", None, 4, {"W": 1.0}, ("principal W", "companion-zero")),
            (f"N4{tag}L", None, 4, {"W": 1.0, **companion_l(0.5, roof=True, deck_l=True)}, ("principal W", "companion L")),
            (f"N4{tag}S", None, 4, {"W": 1.0, **{p: 0.35 for p in s_parts}}, ("principal W", "companion S")),
        ]
        for index in range(len(rows) - 9, len(rows)):
            row_id, name, case, factors, tags = rows[index]
            rows[index] = (row_id, name, case, {"D": dead, **factors}, tags + (f"{dead:g}D",))
    rows += [
        ("N50", None, 5, {"D": 1.0, "E": 1.0}, ("principal E", "companion-zero")),
        ("N5LS", None, 5, {"D": 1.0, "E": 1.0, **companion_l(0.5, roof=False, deck_l=True),
                           **{p: 0.15 for p in s_parts}}, ("principal E", "companion L and S", "Note (6)")),
    ]
    if roof_separate:
        rows.append(("N5L", None, 5, {"D": 1.0, "E": 1.0, **companion_l(0.5, roof=True, deck_l=True),
                                      **{p: 0.15 for p in s_parts if p != "S"}},
                     ("principal E", "companion L with roof live load", "Note (6)")))
    out, seen = [], set()
    for row_id, name, case, factors, tags in rows:
        factors = _with_dead(factors, factors["D"], conditions, case=case, state="ULS")
        if case in (2, 3, 4):
            factors["H"] = 1.5  # Note A-4.1.3.2.(4): 1.5H in cases 2 to 4, never reversed
        factors = _with_fixed(factors, conditions)
        key = tuple(sorted((k, v) for k, v in factors.items() if v))
        if key in seen:
            continue
        seen.add(key)
        label = name if name and not parts.intersection(("D_partition", "D_soil")) else _text(factors)
        out.append(FactorRow(row_id, label, factors, "ULS", case, ("nbc2025",) + tags))
    # Counteractive allowance/soil must also be omitted in case 5 without
    # changing permanent D from 1.0. Append siblings so existing row positions
    # and IDs remain stable. Each factor applies to the entire action vector.
    removable = [(part, suffix) for part, suffix in (("D_partition", "DP0"), ("D_soil", "DS0"))
                 if part in parts and (part != "D_soil" or not conditions.soil_resisting)]
    for row in tuple(out):
        if row.case != 5:
            continue
        for size in range(1, len(removable) + 1):
            for removed in combinations(removable, size):
                factors = dict(row.factors)
                for part, _ in removed:
                    factors[part] = 0.0
                suffix = "_" + "_".join(tag for _, tag in removed)
                out.append(FactorRow(row.row_id + suffix, _text(factors), factors, "ULS", 5,
                                     row.tags + ("counteracting parts omitted",)))
    return tuple(out)


def sls_set(conditions: LoadConditions) -> tuple[FactorRow, ...]:
    """NBC 2025 Table 4.1.3.4 rows (9 formulas). With ULS-specified S and W
    (conditions.uls_specified) their factors include the 0.6 / I_ULS conversion."""
    problems = conditions.problems()
    if problems:
        raise ValueError("NBC 2025 load conditions: " + " ".join(problems))
    lc = 0.35 + (0.15 if conditions.occupancy == "storage_equipment_or_service_room" else 0.0)
    conversion = SLS_IMPORTANCE / conditions.importance_factor if conditions.uls_specified else 1.0
    base = [
        ("S1_0", 1, {"L": 1.0}), ("S1W", 1, {"L": 1.0, "W": 0.3}), ("S1S", 1, {"L": 1.0, "S": 0.35}),
        ("S2_0", 2, {"W": 1.0}), ("S2L", 2, {"W": 1.0, "L": lc}), ("S2S", 2, {"W": 1.0, "S": 0.35}),
        ("S3_0", 3, {"S": 1.0}), ("S3W", 3, {"S": 1.0, "W": 0.3}), ("S3L", 3, {"S": 1.0, "L": lc}),
    ]
    out = []
    for row_id, case, factors in base:
        factors = dict(factors)
        for key in ("W", "S"):
            if key in factors:
                factors[key] *= conversion
        for part, parent in (("L_roof", "L"), ("L_deck", "L"), ("S_deck", "S")):
            if part in conditions.parts and parent in factors:
                factors[part] = factors[parent]
        factors = _with_dead(factors, 1.0, conditions, case=case, state="SLS")
        factors["H"] = 1.0  # 4.1.3.4.(4)
        factors = _with_fixed(factors, conditions)
        tags = ("nbc2025", "SLS", "ULS-specified S and W converted by 0.6 / I" if conditions.uls_specified
                else "SLS-specified S and W")
        out.append(FactorRow(row_id, _text(factors), factors, "SLS", case, tags))
    return tuple(out)


def _legacy(number: int, name: str, d: float, l: float = 0.0, s: float = 0.0, w: float = 0.0, e: float = 0.0) -> FactorRow:
    factors = dict(zip(("D", "L", "S", "W", "E"), (d, l, s, w, e)))
    tags = ("legacy reference",)
    if number in (2, 3):
        tags += ("matches no supplied table",)
    elif number in (6, 7):
        tags += ("S304-14 4.2.2.2 case 3 with the Note (4) companion 1.0L",)
    else:
        tags += ("S304-14 4.2.2.2 row",)
    return FactorRow(f"R{number:02}", name, factors, "ULS", 0, tags)


# The 14 rows of the earlier tool, named by content (not "NBC 2015/2020"):
# ten equal S304-14 4.2.2.2 rows; R06/R07 are its case 3 with the Note (4)
# companion 1.0L; R02/R03 match no supplied table. Not an NBC 2025 set.
LEGACY_REFERENCE_14 = (
    _legacy(1, "1.4D", 1.4),
    _legacy(2, "1.25D + 1.5L + 1.0S", 1.25, 1.5, 1.0),
    _legacy(3, "0.9D + 1.5L + 1.0S", 0.9, 1.5, 1.0),
    _legacy(4, "1.25D + 1.5L + 0.4W", 1.25, 1.5, w=0.4),
    _legacy(5, "0.9D + 1.5L + 0.4W", 0.9, 1.5, w=0.4),
    _legacy(6, "1.25D + 1.0L + 1.5S", 1.25, 1.0, 1.5),
    _legacy(7, "0.9D + 1.0L + 1.5S", 0.9, 1.0, 1.5),
    _legacy(8, "1.25D + 1.5S + 0.4W", 1.25, s=1.5, w=0.4),
    _legacy(9, "0.9D + 1.5S + 0.4W", 0.9, s=1.5, w=0.4),
    _legacy(10, "1.25D + 0.5L + 1.4W", 1.25, 0.5, w=1.4),
    _legacy(11, "0.9D + 0.5L + 1.4W", 0.9, 0.5, w=1.4),
    _legacy(12, "1.25D + 0.5S + 1.4W", 1.25, s=0.5, w=1.4),
    _legacy(13, "0.9D + 0.5S + 1.4W", 0.9, s=0.5, w=1.4),
    _legacy(14, "1.0D + 0.5L + 0.25S + 1.0E", 1.0, 0.5, 0.25, e=1.0),
)
LEGACY_LABEL = ("Legacy 14-row reference set (not NBC 2025): ten rows equal S304-14 4.2.2.2; R06/R07 are its case 3 "
                "with the Note (4) companion 1.0L; R02/R03 match no supplied table")


def expand_directions(factors: Mapping[str, float]) -> list[tuple[str, dict]]:
    """[(suffix, signed factors)] for both senses of each reversible term.

    Only W and E reverse; their fixed parts keep their sign. The suffix is
    "" for a row without W or E, otherwise e.g. "[W+]" or "[W+,E-]".
    """
    reverse = [key for key in REVERSIBLE if factors.get(key, 0.0)]
    out = []
    for signs in product((1, -1), repeat=len(reverse)):
        directions = dict(zip(reverse, signs))
        signed = {key: value * directions.get(key, 1) for key, value in factors.items()}
        suffix = "" if not reverse else "[" + ",".join(k + ("+" if directions[k] == 1 else "-") for k in reverse) + "]"
        out.append((suffix, signed))
    return out


def factor_findings(factors: Mapping[str, float], state: str = "ULS") -> list[tuple[str, str]]:
    """[(level, message)] for one row's factors; level "error" or "warning"."""
    out = []
    for key, value in factors.items():
        if key not in LOAD_TYPES:
            out.append(("error", f"Unknown load type {key!r}."))
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            out.append(("error", f"{key} factor must be a finite number."))
        elif value < 0.0:
            out.append(("error", f"{key} factor must be nonnegative."))
    if factors.get("W", 0) and factors.get("E", 0):
        out.append(("warning", "W and E in one row: NBC 2025 Table 4.1.3.2.-A has no such combination."))
    if state == "SLS" and (factors.get("E", 0) or factors.get("E_fixed", 0)):
        out.append(("error", "E in a serviceability row: NBC 2025 Table 4.1.3.4 has no earthquake row; "
                             "seismic deflections and drift are checked under 4.1.8.13."))
    return out


def unused_contribution_findings(nonzero_types: Sequence[str], rows: Sequence[FactorRow], set_name: str) -> list[str]:
    """Messages for non-zero contributions that no enabled row applies (F-006)."""
    applied = {key for row in rows if row.enabled for key, value in row.factors.items() if value}
    return [f"The {key} contribution is non-zero but no enabled row of the {set_name} set applies it."
            for key in nonzero_types if key not in applied]


__all__ = [
    "BASE_TYPES", "FIXED_PART", "IMPORTANCE_ULS", "LEGACY_LABEL", "LEGACY_REFERENCE_14", "LOAD_TYPES",
    "OCCUPANCIES", "PARKING_DECKS", "PART_TYPES", "REVERSIBLE", "ROOF_LIVE_LOADS", "SLS_IMPORTANCE",
    "FactorRow", "LoadConditions", "expand_directions", "factor_findings", "sls_set", "soil_factor",
    "uls_set", "unused_contribution_findings",
]
