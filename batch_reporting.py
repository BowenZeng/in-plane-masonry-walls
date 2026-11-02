"""Tabular batch reports shared by the UI and CSV exports."""
from __future__ import annotations

import csv
from dataclasses import asdict
from io import StringIO
import json
import math
from typing import Mapping, Sequence

if __package__:
    from .load_combinations import BatchResult, CaseResult
else:
    from load_combinations import BatchResult, CaseResult


def _json(value: object) -> str:
    def clean(item):
        if isinstance(item, float) and not math.isfinite(item):
            return str(item)
        if isinstance(item, dict):
            return {key: clean(val) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(val) for val in item]
        return item
    return json.dumps(clean(value), sort_keys=True, default=str, allow_nan=False)


def check_status(check) -> str:
    """Batch/UI status of one engine check; the single mapping for exports and the UI.

    A blocked numeric check is UNSUPPORTED (capability missing) or INCOMPLETE
    (declaration missing) when the engine says so, otherwise BLOCKED (not met).
    Review items are BLOCKED until confirmed; info items never pass or fail.
    """
    if check.kind == "info":
        return "INFO"
    if check.level == "pass":
        return "PASS"
    if check.kind != "review" and check.unresolved == "incomplete":
        return "INCOMPLETE"
    if check.kind != "review" and check.unresolved == "unsupported":
        return "UNSUPPORTED"
    return "BLOCKED"


def context(member: CaseResult) -> dict[str, object]:
    case = member.case
    return {
        "Case ID": case.case_id, "Case": case.name, "Edition": member.edition,
        "Section": case.section, "Load basis": case.load_basis, "Source": case.source,
        "Factors": _json(case.factors), "Direction": case.direction,
        "P (kN)": case.actions.p if not case.error else None,
        "Signed M (kN-m)": case.actions.m if not case.error else None,
        "Signed V (kN)": case.actions.v if not case.error else None,
        "Self-weight treatment": case.self_weight_treatment,
        "D for Pd (kN)": case.dead_load_for_pd,
        "Input fingerprint": member.fingerprint, "Supplement review": member.supplement_status,
        "Supplement inputs": _json(asdict(member.supplied_inputs)),
        "Provenance": _json(case.provenance),
    }


def result_rows(batch: BatchResult) -> list[dict[str, object]]:
    rows = []
    for member in batch.results:
        result = member.result
        row = {**context(member), "IP resistance": member.resistance_status,
               "Additional checks": member.additional_status,
               "Issues": "; ".join(member.issues),
               "Pd (kN)": result.pd if result else None,
               "Calculated self-weight (kN)": result.self_weight if result else None,
               "Pr,max (kN)": result.axial_cap if result else None,
               "Tr,max (kN)": result.tension_cap if result else None,
               "Mr(Pf) (kN-m)": result.moment_resistance if result else None,
               "Vr diagonal (kN)": result.shear.resistance if result else None,
               "Vr sliding (kN)": result.sliding.resistance if result else None,
               "Vr stack joint (kN)": result.shear.stack_vertical_resistance if result else None,
               "Flexural method at Pf": result.curve_method if result else None,
               "Combined IP/OOP interaction screen": result.stability.combined_utilization if result else None}
        for name in ("Axial compression", "Axial tension", "P-M", "Diagonal shear", "Sliding", "Stack-joint shear"):
            check = next((c for c in member.resistance_checks if c.check == name), None)
            row[f"{name} utilization"] = check.utilization if check else None
            row[f"{name} status"] = check.status if check else "Not evaluated"
        rows.append(row)
    return rows


def governing_rows(batch: BatchResult) -> list[dict[str, object]]:
    return [{"Edition": row.edition, "Check": row.check,
             "Governing case IDs": ", ".join(row.case_ids), "Utilization": row.utilization,
             "Unranked / incomplete case IDs": ", ".join(row.unranked_case_ids)}
            for row in batch.governing]


def check_rows(batch: BatchResult) -> list[dict[str, object]]:
    rows = []
    for member in batch.results:
        header = context(member)
        for check in member.resistance_checks:
            rows.append({**header, "Category": "Batch resistance", "Check": check.check,
                         "Status": check.status, "Demand": check.demand, "Resistance": check.resistance,
                         "Utilization": check.utilization})
        if member.result is not None:
            rows.extend({**header, "Category": check.category, "Check": check.title,
                         "Status": check_status(check), "Result": check.detail, "Clause": check.clause}
                        for check in member.result.checks)
        rows.extend({**header, "Category": "Input / review", "Check": "Case evaluation",
                     "Status": member.resistance_status, "Result": issue} for issue in member.issues)
    return rows


def trace_rows(batch: BatchResult) -> list[dict[str, object]]:
    rows = []
    for member in batch.results:
        header = context(member)
        if member.result is None:
            rows.append({**header, "Variable": "Evaluation", "Value": "; ".join(member.issues),
                         "Basis": member.resistance_status})
            continue
        result = member.result
        rows.append({**header, "Variable": "WallInput", "Value": _json(asdict(result.wall)),
                     "Unit": "mm, kN, kN-m, MPa; see input field names",
                     "Basis": "Exact engine input for this case and edition", "Clause": "Input"})
        rows.extend({**header, "Variable": item.variable, "Value": item.value,
                     "Unit": item.unit, "Basis": item.equation, "Clause": item.clause}
                    for item in result.trace)
    return rows


def curve_rows(batch: BatchResult) -> list[dict[str, object]]:
    rows = []
    for member in batch.results:
        result = member.result
        if result is None:
            continue
        header = context(member)
        for label, curve in ((result.curve_method, result.curve),
                             (result.alternate_curve_method, result.alternate_curve)):
            if curve is None:
                continue
            rows.extend({**header, "Curve": label, "Order": order, "Branch": point.branch,
                         "Moment resistance (kN-m)": point.m, "Axial resistance (kN)": point.p}
                        for order, point in enumerate(curve))
    return rows


def csv_bytes(rows: Sequence[Mapping[str, object]]) -> bytes:
    if not rows:
        return b""
    columns = list(dict.fromkeys(key for row in rows for key in row))
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def exports(batch: BatchResult) -> dict[str, bytes]:
    return {"cases": csv_bytes(result_rows(batch)), "governing": csv_bytes(governing_rows(batch)),
            "checks": csv_bytes(check_rows(batch)), "trace": csv_bytes(trace_rows(batch)),
            "pm_points": csv_bytes(curve_rows(batch))}
