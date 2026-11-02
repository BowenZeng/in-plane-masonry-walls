"""Concurrent section actions and batch checks, independent of Streamlit.

Load sets come from nbc_load_sets: the NBC 2025 Table 4.1.3.2.-A set for the
declared conditions (NBC2025_COMBINATIONS) and the legacy 14-row reference set
(REFERENCE_COMBINATIONS, not an NBC 2025 set). Each scalar factor applies to
the whole action vector. Resistance equations remain in in_plane_engine.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import json
import math
from typing import Mapping, Sequence

if __package__:
    from . import in_plane_engine as engine
    from . import nbc_load_sets as nbc
else:
    import in_plane_engine as engine
    import nbc_load_sets as nbc

LOAD_TYPES = nbc.LOAD_TYPES
BASE_TYPES = nbc.BASE_TYPES
EDITIONS = ("S304-14", "S304:24")
DCNT_COLUMN = "Counteracting dead load D_cnt (kN)"
NLAT_COLUMN = "Signed lateral axial in this case (kN)"
NCB_COLUMN = "Coupling-beam axial in this case (kN)"
EQ_COLUMN = "Earthquake case"
ADD_SELF_WEIGHT = "Add calculated self-weight"
SELF_WEIGHT_INCLUDED = "Already included in D"
DIRECT_SELF_WEIGHT = "Entered Pf includes all factored actions; self-weight is not added"


@dataclass(frozen=True)
class Action:
    p: float = 0.0
    m: float = 0.0
    v: float = 0.0
    stack_v: float | None = None


@dataclass(frozen=True)
class Combination:
    combination_id: str
    name: str
    factors: Mapping[str, float]
    enabled: bool = True
    error: str = ""
    tags: tuple[str, ...] = ()


def _combination(row: nbc.FactorRow) -> Combination:
    return Combination(row.row_id, row.name, {key: row.factors.get(key, 0.0) for key in LOAD_TYPES},
                       tags=row.tags)


# Legacy 14-row reference set, labelled by its content (nbc.LEGACY_LABEL).
REFERENCE_COMBINATIONS = tuple(_combination(row) for row in nbc.LEGACY_REFERENCE_14)


def NBC2025_COMBINATIONS(conditions: nbc.LoadConditions) -> tuple[Combination, ...]:  # noqa: N802
    """NBC 2025 Table 4.1.3.2.-A rows for the declared conditions (21 for one L and one S)."""
    return tuple(_combination(row) for row in nbc.uls_set(conditions))


def unused_contributions(contributions: Mapping[str, "Action"], combinations: Sequence[Combination],
                         set_name: str) -> list[str]:
    """Non-zero contributions that no enabled combination applies (F-006)."""
    nonzero = [key for key, action in contributions.items()
               if any(getattr(action, c) for c in ("p", "m", "v")) or action.stack_v]
    rows = [nbc.FactorRow(c.combination_id, c.name, c.factors, enabled=c.enabled) for c in combinations]
    return nbc.unused_contribution_findings(nonzero, rows, set_name)


@dataclass(frozen=True)
class LoadCase:
    case_id: str
    name: str
    actions: Action
    enabled: bool = True
    source: str = "Direct factored case"
    section: str = "Base"
    load_basis: str = "Project-supplied factored actions"
    factors: Mapping[str, float] = field(default_factory=dict)
    direction: str = "As entered"
    self_weight_treatment: str = DIRECT_SELF_WEIGHT
    # Counteracting dead load D_cnt (kN, unfactored, including the self-weight
    # above the section); blank in a direct row uses the wall's dead-load basis.
    dead_load_for_pd: float | None = None
    provenance: Mapping[str, object] = field(default_factory=dict)
    error: str = ""
    # Signed factored W/E axial load of this case (kN, compression positive)
    # and its declared coupling-beam part. None = not declared: the
    # Pd-dependent checks are then INCOMPLETE (register I-2).
    lateral_axial: float | None = None
    coupling_axial: float = 0.0
    pd_case: float | None = None
    pd_parts: Mapping[str, float] = field(default_factory=dict)
    pd_basis: str = ""
    earthquake_case: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class LinkedOOP:
    utilization: float
    stability_passed: bool
    reference: str = ""


@dataclass(frozen=True)
class CasePd:
    value: float
    parts: Mapping[str, float]
    basis: str


@dataclass(frozen=True)
class CaseSupplement:
    # Signed case Pd from an external source; the source text is required.
    pd_override: float | None = None
    pd_override_source: str = ""
    uncracked_area_fraction: float = 0.0
    stack_v: float | None = None
    oop: Mapping[str, LinkedOOP] = field(default_factory=dict)
    reviewed_for: str = ""


@dataclass(frozen=True)
class ResistanceCheck:
    check: str
    demand: float | None
    resistance: float | None
    utilization: float | None
    status: str


@dataclass(frozen=True)
class CaseResult:
    case: LoadCase
    edition: str
    result: engine.WallResult | None
    resistance_checks: tuple[ResistanceCheck, ...]
    resistance_status: str
    additional_status: str
    issues: tuple[str, ...] = ()
    fingerprint: str = ""
    supplement_status: str = "Not supplied"
    supplied_inputs: CaseSupplement = field(default_factory=CaseSupplement)


@dataclass(frozen=True)
class GoverningResult:
    edition: str
    check: str
    case_ids: tuple[str, ...]
    utilization: float | None
    unranked_case_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class BatchResult:
    cases: tuple[LoadCase, ...]
    results: tuple[CaseResult, ...]
    governing: tuple[GoverningResult, ...]

    @property
    def all_ip_pass(self) -> bool:
        return bool(self.results) and all(r.resistance_status == "PASS" for r in self.results)


def _finite(value: float, label: str, *, nonnegative: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number.")
    if nonnegative and value < 0.0:
        raise ValueError(f"{label} must be nonnegative.")


def _validate_action(action: Action, label: str) -> None:
    for component in ("p", "m", "v"):
        _finite(getattr(action, component), f"{label} {component.upper()}")
    if action.stack_v is not None:
        _finite(action.stack_v, f"{label} vertical head-joint shear")


def case_pd(dead_counteracting: float, lateral_axial: float, coupling_axial: float = 0.0, *,
            factor: float = engine.PD_DEAD_LOAD_FACTOR, source: str = "case composition") -> CasePd:
    """Pd_case = factor D_cnt + min(0, N_lat - N_cb) + N_cb, in kN (registers I-1, I-3).

    D_cnt is the counteracting dead load on the section: permanent dead load
    plus the self-weight above it, without a partition allowance or
    counteractive soil. N_lat is the case's signed factored W/E axial load;
    a tensile value always reduces Pd. N_cb is the part of N_lat declared
    coupling-beam axial load, the only part that may add compression. Live
    and snow loads never enter Pd. The factor is 0.9 in every combination.
    """
    _finite(dead_counteracting, "Counteracting dead load D_cnt", nonnegative=True)
    _finite(lateral_axial, "Signed lateral axial load")
    _finite(coupling_axial, "Coupling-beam axial load")
    value = factor * dead_counteracting + min(0.0, lateral_axial - coupling_axial) + coupling_axial
    parts = {"dead_counteracting_kN": dead_counteracting, "lateral_axial_kN": lateral_axial,
             "coupling_beam_axial_kN": coupling_axial, "pd_factor": factor}
    basis = (f"{source}: {factor:g} x D_cnt {dead_counteracting:.2f} + min(0, N_lat {lateral_axial:.2f} "
             f"- N_cb {coupling_axial:.2f}) + N_cb = {value:.2f} kN")
    return CasePd(value, parts, basis)


def generate_load_cases(
    contributions: Mapping[str, Action],
    combinations: Sequence[Combination] = REFERENCE_COMBINATIONS,
    *,
    self_weight: float = 0.0,
    self_weight_treatment: str = ADD_SELF_WEIGHT,
    section: str = "Base",
    load_basis: str = "Legacy 14-row reference set (not NBC 2025); confirm project load basis",
    coupling_beam_axial: bool = False,
    conditions: nbc.LoadConditions | None = None,
) -> tuple[LoadCase, ...]:
    """Combine signed linear actions; retain invalid formulas as visible cases.

    Omitted load types mean zero. A missing stack_v for any contributing load
    leaves the joint demand unknown (explicit zero is distinct from missing).
    Each case carries its Pd_case (case_pd): D_cnt is D plus the added
    self-weight (plus D_soil only where the soil resists, never the partition
    allowance), N_lat the signed factored W and E axial loads of the case
    including their fixed parts; the E axial load counts as coupling-beam
    axial only when declared so (conditions.coupling_beam_axial or the flag).
    The NBC 2025 rows need their conditions (an error otherwise).
    """
    if conditions is not None:
        coupling_beam_axial = coupling_beam_axial or conditions.coupling_beam_axial
    cases = []
    duplicate_ids = {key for key, count in Counter(c.combination_id for c in combinations if c.enabled).items() if count > 1}
    for definition in combinations:
        context = dict(
            case_id=definition.combination_id, name=definition.name,
            enabled=definition.enabled, source="Generated linear combination",
            section=section, load_basis=load_basis, factors=dict(definition.factors),
            self_weight_treatment=self_weight_treatment,
            provenance={"contributions": {k: asdict(v) for k, v in contributions.items()},
                        "calculated_self_weight_kN": self_weight, "row_tags": list(definition.tags),
                        "load_set": ("NBC 2025 Table 4.1.3.2.-A" if "nbc2025" in definition.tags
                                     else nbc.LEGACY_LABEL if "legacy reference" in definition.tags
                                     else "custom factors"),
                        "conditions": asdict(conditions) if conditions is not None else None},
        )
        try:
            if definition.error:
                raise ValueError(definition.error)
            if not definition.combination_id.strip():
                raise ValueError("Combination ID is required.")
            if definition.combination_id in duplicate_ids:
                raise ValueError("Enabled combination IDs must be unique.")
            if "nbc2025" in definition.tags and conditions is None:
                raise ValueError("NBC 2025 rows need the declared load conditions.")
            unknown = (set(contributions) | set(definition.factors)) - set(LOAD_TYPES)
            if unknown:
                raise ValueError(f"Unknown load types: {', '.join(sorted(unknown))}.")
            findings = nbc.factor_findings(definition.factors)
            errors = [message for level, message in findings if level == "error"]
            if errors:
                raise ValueError(" ".join(errors))
            warnings = tuple(message for level, message in findings if level == "warning")
            if "D_soil" in contributions and contributions["D_soil"].p and conditions is None:
                raise ValueError("A soil contribution needs the declared load conditions (soil_resisting).")
            _finite(self_weight, "Calculated self-weight", nonnegative=True)
            if self_weight_treatment not in (ADD_SELF_WEIGHT, SELF_WEIGHT_INCLUDED):
                raise ValueError("Select an explicit self-weight treatment.")
            actions = {key: contributions.get(key, Action(stack_v=0.0)) for key in LOAD_TYPES}
            # D is required for Pd even if a custom formula has a zero D factor.
            _validate_action(actions["D"], "D")
            for key in LOAD_TYPES:
                if definition.factors.get(key, 0.0):
                    _validate_action(actions[key], key)
            dead = actions["D"].p + (self_weight if self_weight_treatment == ADD_SELF_WEIGHT else 0.0)
            actions["D"] = replace(actions["D"], p=dead)
            # Counteracting dead load: no partition allowance; soil only where it resists (4.1.4.1.(5)-(6)).
            dead_counteracting = dead + (actions["D_soil"].p if conditions is not None and conditions.soil_resisting else 0.0)
            for suffix, signed in nbc.expand_directions(definition.factors):
                factors = {key: signed.get(key, 0.0) for key in LOAD_TYPES}
                directions = {key: (1 if factors[key] > 0 else -1) for key in nbc.REVERSIBLE if factors[key]}
                reversible = list(directions)
                active = [key for key in LOAD_TYPES if factors[key] != 0.0]
                summed = {component: sum(factors[key] * getattr(actions[key], component)
                                         for key in active) for component in ("p", "m", "v")}
                stack = (sum(factors[key] * actions[key].stack_v for key in active)
                         if all(actions[key].stack_v is not None for key in active) else None)
                action = Action(**summed, stack_v=stack)
                _validate_action(action, "Combined action")
                direction = ", ".join(f"{key}{'+' if directions[key] > 0 else '-'}" for key in reversible)
                case_id = definition.combination_id + suffix
                lateral = sum(factors[key] * actions[key].p
                              for key in ("W", "E", "W_fixed", "E_fixed") if factors[key])
                coupling = factors["E"] * actions["E"].p if coupling_beam_axial and factors["E"] else 0.0
                pd = case_pd(dead_counteracting, lateral, coupling)
                cases.append(LoadCase(**{**context, "case_id": case_id, "factors": factors},
                                      actions=action,
                                      direction=direction or "No reversible term",
                                      dead_load_for_pd=dead_counteracting, lateral_axial=lateral,
                                      coupling_axial=coupling, pd_case=pd.value,
                                      pd_parts=pd.parts, pd_basis=pd.basis,
                                      earthquake_case=bool(definition.factors.get("E", 0.0)
                                                           or definition.factors.get("E_fixed", 0.0)),
                                      warnings=warnings))
        except (ValueError, TypeError, OverflowError) as error:
            cases.append(LoadCase(**context, actions=Action(math.nan, math.nan, math.nan), error=str(error)))
    return tuple(cases)


def case_fingerprint(wall: engine.WallInput, case: LoadCase) -> str:
    """Reordering/selection is immaterial; changed wall or source actions is not."""
    geometry = asdict(wall)
    for key in ("edition", "factored_axial", "factored_moment", "factored_shear", "earthquake_case",
                "dead_load_compression", "pd_basis", "uncracked_area_fraction", "stack_vertical_shear",
                "oop_check_provided", "oop_utilization", "oop_stability_passed"):
        geometry.pop(key)
    if case.dead_load_for_pd is not None:
        geometry.pop("unfactored_dead_load_above")
        geometry.pop("include_wall_self_weight_in_pd")
    inputs = asdict(case)
    for key in ("enabled", "name"):
        inputs.pop(key)
    encoded = json.dumps({"wall": geometry, "case": inputs}, sort_keys=True, default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def review_supplement(wall: engine.WallInput, case: LoadCase,
                      supplement: CaseSupplement) -> CaseSupplement:
    return replace(supplement, reviewed_for=case_fingerprint(wall, case))


def _resistance(check: str, demand: float | None, resistance: float | None,
                *, outside_axial_domain: bool = False) -> ResistanceCheck:
    if demand is None or resistance is None:
        return ResistanceCheck(check, demand, resistance, None,
                               "FAIL" if outside_axial_domain else "INCOMPLETE")
    # Zero resistance with positive demand is a failure, never a zero ratio.
    utilization = demand / resistance if resistance > 0.0 else (0.0 if demand == 0.0 else math.inf)
    return ResistanceCheck(check, demand, resistance, utilization,
                           "PASS" if demand <= resistance + 1.0e-8 else "FAIL")


def _resistance_checks(result: engine.WallResult) -> tuple[ResistanceCheck, ...]:
    wall = result.wall
    axial = _resistance("Axial compression" if wall.factored_axial >= 0.0 else "Axial tension",
                        abs(wall.factored_axial),
                        result.axial_cap if wall.factored_axial >= 0.0 else result.tension_cap)
    checks = [axial,
              _resistance("P-M", wall.factored_moment, result.moment_resistance,
                          outside_axial_domain=axial.status == "FAIL"),
              _resistance("Diagonal shear", wall.factored_shear, result.shear.resistance),
              _resistance("Sliding", wall.factored_shear, result.sliding.resistance)]
    if wall.bond_pattern == "Stack pattern":
        checks.append(_resistance("Stack-joint shear", wall.stack_vertical_shear,
                                  result.shear.stack_vertical_resistance if wall.stack_joint_area is not None else None))
    return tuple(checks)


def _governing(results: Sequence[CaseResult], editions: Sequence[str]) -> tuple[GoverningResult, ...]:
    governing = []
    for edition in editions:
        members = [result for result in results if result.edition == edition]
        names = {check.check for member in members for check in member.resistance_checks}
        names.update(("Axial compression", "Axial tension", "P-M", "Diagonal shear", "Sliding"))
        for name in ("Axial compression", "Axial tension", "P-M", "Diagonal shear", "Sliding", "Stack-joint shear"):
            if name not in names:
                continue
            ranked, unranked = [], []
            for member in members:
                check = next((c for c in member.resistance_checks if c.check == name), None)
                if member.result is None or (check is not None and check.utilization is None):
                    unranked.append(member.case.case_id)
                elif check is not None:
                    ranked.append((member.case.case_id, check.utilization))
            maximum = max((value for _, value in ranked), default=None)
            ids = tuple(sorted(case_id for case_id, value in ranked
                               if maximum is not None and math.isclose(value, maximum, rel_tol=1.0e-9)))
            governing.append(GoverningResult(edition, name, ids, maximum, tuple(sorted(unranked))))
    return tuple(governing)


def _resolve_pd(wall: engine.WallInput, case: LoadCase,
                supplement: CaseSupplement | None) -> tuple[float | None, str]:
    """The case Pd passed to the engine: a sourced override, the generated
    composition, or the declared direct-row components; otherwise None."""
    if supplement is not None and supplement.pd_override is not None:
        return supplement.pd_override, f"sourced override: {supplement.pd_override_source.strip()}"
    if case.dead_load_for_pd is not None:
        _finite(case.dead_load_for_pd, "Counteracting dead load D_cnt")
        if case.dead_load_for_pd < 0.0:
            raise ValueError("Counteracting dead load D_cnt cannot be negative; enter tensile actions as the "
                             "signed lateral axial load or supply a sourced case Pd.")
    if case.pd_case is not None:
        return case.pd_case, case.pd_basis
    if case.lateral_axial is None:
        return None, "not supplied"
    if case.dead_load_for_pd is not None:
        dead, source = case.dead_load_for_pd, "case composition"
    else:
        dead = wall.unfactored_dead_load_above + (
            engine.wall_self_weight(wall) if wall.include_wall_self_weight_in_pd else 0.0)
        source = "case composition with the wall's dead-load basis"
    pd = case_pd(dead, case.lateral_axial, case.coupling_axial, source=source)
    return pd.value, pd.basis


def check_load_cases(
    wall: engine.WallInput,
    cases: Sequence[LoadCase],
    supplements: Mapping[str, CaseSupplement] | None = None,
    *, editions: Sequence[str] = EDITIONS,
) -> BatchResult:
    """Evaluate every enabled case; input errors never discard other cases.

    The wall supplies shared geometry/detailing and the direct-mode dead-load
    basis. Case-dependent values on WallInput are deliberately replaced.
    """
    supplements = supplements or {}
    duplicate_ids = {key for key, count in Counter(c.case_id for c in cases if c.enabled).items() if count > 1}
    results = []
    for case in cases:
        if not case.enabled:
            continue
        fingerprint = case_fingerprint(wall, case)
        supplement = supplements.get(case.case_id, CaseSupplement())
        supplied = (supplement.pd_override is not None or supplement.uncracked_area_fraction != 0.0
                    or supplement.stack_v is not None or bool(supplement.oop))
        stale = supplied and supplement.reviewed_for != fingerprint
        supplement_status = "STALE" if stale else ("Reviewed" if supplied else "Not supplied")
        for edition in editions:
            try:
                if edition not in EDITIONS:
                    raise ValueError(f"Unsupported edition: {edition}.")
                if case.error:
                    raise ValueError(case.error)
                if not case.case_id.strip() or case.case_id in duplicate_ids:
                    raise ValueError("Enabled case IDs must be nonempty and unique.")
                if not case.section.strip():
                    raise ValueError("Identify the checked section.")
                _validate_action(case.actions, "Case")
                if supplement.pd_override is not None:
                    _finite(supplement.pd_override, "Pd override")
                    if not supplement.pd_override_source.strip():
                        raise ValueError("A Pd override is a signed case Pd from an external source; enter its source.")
                _finite(supplement.uncracked_area_fraction, "Uncracked area fraction", nonnegative=True)
                if supplement.uncracked_area_fraction > 1.0:
                    raise ValueError("Uncracked area fraction cannot exceed 1.0.")
                if supplement.stack_v is not None:
                    _finite(supplement.stack_v, "Vertical head-joint shear override")
                if stale and (supplement.pd_override is not None
                              or (wall.sliding_plane == "Between masonry courses" and supplement.uncracked_area_fraction != 0.0)
                              or (wall.bond_pattern == "Stack pattern" and supplement.stack_v is not None)):
                    results.append(CaseResult(case, edition, None, (), "INCOMPLETE", "OUTSTANDING",
                                              ("Case-specific resistance inputs are stale; review them for the changed wall/actions.",),
                                              fingerprint, supplement_status, supplement))
                    continue
                pd, pd_basis = _resolve_pd(wall, case, None if stale else supplement)
                linked = None if stale else supplement.oop.get(edition)
                if linked is not None:
                    _finite(linked.utilization, f"{edition} OOP utilization", nonnegative=True)
                stack = supplement.stack_v if not stale and supplement.stack_v is not None else case.actions.stack_v
                adjusted = replace(
                    wall, edition=edition, factored_axial=case.actions.p,
                    factored_moment=abs(case.actions.m), factored_shear=abs(case.actions.v),
                    dead_load_compression=pd, pd_basis=pd_basis, earthquake_case=case.earthquake_case,
                    uncracked_area_fraction=0.0 if stale else supplement.uncracked_area_fraction,
                    stack_vertical_shear=None if stack is None else abs(stack),
                    oop_check_provided=linked is not None,
                    oop_utilization=linked.utilization if linked else 0.0,
                    oop_stability_passed=linked.stability_passed if linked else False,
                )
                result = engine.calculate_wall(adjusted)
                checks = _resistance_checks(result)
                status = ("FAIL" if any(c.status == "FAIL" for c in checks) else
                          "INCOMPLETE" if any(c.status == "INCOMPLETE" for c in checks) else "PASS")
                issues = ("Supplemental inputs are stale; linked OOP results were not used.",) if stale else ()
                # Info items are ignored when they pass; every other unpassed
                # item (failed, review, incomplete or unsupported) is outstanding.
                additional = "OUTSTANDING" if issues or any(
                    c.level != "pass" and c.category != "resistance" for c in result.checks
                ) else "CHECKS CLEAR (limited scope)"
                results.append(CaseResult(case, edition, result, checks, status, additional,
                                          issues, fingerprint, supplement_status, supplement))
            except (engine.CalculationError, ValueError, TypeError, OverflowError) as error:
                results.append(CaseResult(case, edition, None, (), "INVALID", "OUTSTANDING",
                                          (str(error),), fingerprint, supplement_status, supplement))
    return BatchResult(tuple(cases), tuple(results), _governing(results, editions))


def _number(value: object, label: str, *, optional: bool = False) -> float | None:
    if value is None or value == "" or (isinstance(value, float) and math.isnan(value)):
        if optional:
            return None
        raise ValueError(f"{label} is required (enter 0 for zero).")
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number.")
    result = float(value)
    _finite(result, label)
    return result


def _text(value: object) -> str:
    return "" if value is None or (isinstance(value, float) and math.isnan(value)) else str(value).strip()


def factored_cases_from_rows(rows: Sequence[Mapping[str, object]], *, section: str,
                             load_basis: str) -> tuple[LoadCase, ...]:
    """Strict editor adapter: a malformed row remains in the evaluated batch."""
    cases = []
    for row in rows:
        error = ""
        try:
            action = Action(*(_number(row.get(key), key) for key in ("P (kN)", "M (kN-m)", "V (kN)")),
                            stack_v=_number(row.get("Joint V (kN)"), "Joint V", optional=True))
            dead = _number(row.get(DCNT_COLUMN), "D_cnt", optional=True)
            lateral = _number(row.get(NLAT_COLUMN), "Signed lateral axial", optional=True)
            # Only a declared coupling-beam part may add compression to Pd;
            # blank is the conservative zero.
            coupling = _number(row.get(NCB_COLUMN), "Coupling-beam axial", optional=True) or 0.0
        except (ValueError, TypeError, OverflowError) as exc:
            action, dead, lateral, coupling, error = Action(math.nan, math.nan, math.nan), None, None, 0.0, str(exc)
        cases.append(LoadCase(_text(row.get("Case ID")), _text(row.get("Name")), action,
                              enabled=row.get("Enabled") is not False,
                              section=section, load_basis=load_basis, dead_load_for_pd=dead,
                              provenance={"entered_row": dict(row)}, error=error,
                              lateral_axial=lateral, coupling_axial=coupling,
                              # The row's actions include earthquake load: the seismic gate applies (WP3).
                              earthquake_case=row.get(EQ_COLUMN) is True))
    return tuple(cases)


def combinations_from_rows(rows: Sequence[Mapping[str, object]]) -> tuple[Combination, ...]:
    combinations = []
    for row in rows:
        error = ""
        try:
            # Base types are required columns; parts are optional (blank = not used).
            factors = {key: _number(row.get(key), f"{key} factor") for key in BASE_TYPES}
            factors.update({key: _number(row.get(key), f"{key} factor", optional=True) or 0.0
                            for key in nbc.PART_TYPES})
        except (ValueError, TypeError, OverflowError) as exc:
            factors, error = {}, str(exc)
        combinations.append(Combination(_text(row.get("Combination ID")), _text(row.get("Name")),
                                        factors, row.get("Enabled") is not False, error))
    return tuple(combinations)


def contributions_from_rows(rows: Sequence[Mapping[str, object]]) -> dict[str, Action]:
    contributions = {}
    for row in rows:
        key = _text(row.get("Load"))
        if key not in LOAD_TYPES or key in contributions:
            raise ValueError(f"Each load type ({', '.join(LOAD_TYPES)}) may appear once.")
        try:
            contributions[key] = Action(*(_number(row.get(column), f"{key} {column}")
                                          for column in ("P (kN)", "M (kN-m)", "V (kN)")),
                                        stack_v=_number(row.get("Joint V (kN)"), f"{key} Joint V", optional=True))
        except (ValueError, TypeError, OverflowError):
            # Only formulas that use this contribution are invalid. A malformed
            # wind row must not suppress a valid dead-only calculation.
            contributions[key] = Action(math.nan, math.nan, math.nan)
    if not set(BASE_TYPES) <= set(contributions):
        raise ValueError(f"Supply the base load types {', '.join(BASE_TYPES)}; enter 0 for absent loads.")
    return contributions


def direct_rows_from_cases(cases: Sequence[LoadCase]) -> list[dict[str, object]]:
    """Copy totals, including D provenance, without applying factors twice."""
    return [{"Case ID": c.case_id, "Name": c.name, "Enabled": c.enabled,
             "P (kN)": c.actions.p, "M (kN-m)": c.actions.m, "V (kN)": c.actions.v,
             "Joint V (kN)": c.actions.stack_v, DCNT_COLUMN: c.dead_load_for_pd,
             NLAT_COLUMN: c.lateral_axial, NCB_COLUMN: c.coupling_axial, EQ_COLUMN: c.earthquake_case}
            for c in cases]
