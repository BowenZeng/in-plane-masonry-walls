"""Streamlit inputs and views for concurrent load-case batches."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, replace
import math

import altair as alt
import pandas as pd
import streamlit as st

import batch_reporting as reports
import in_plane_engine as engine
import load_combinations as lc

DIRECT = "Enter factored cases"
GENERATED = "Generate from unfactored actions"


def _initialize() -> None:
    defaults = {
        "load_mode": DIRECT, "direct_version": 0, "generated_version": 0,
        "direct_rows": [{"Case ID": "CASE-1", "Name": "Original case", "Enabled": True,
                         "P (kN)": 200.0, "M (kN-m)": 1000.0, "V (kN)": 200.0,
                         "Joint V (kN)": None, "D for Pd (kN)": None}],
        "contribution_rows": [{"Load": key, "P (kN)": 150.0 if key == "D" else 0.0,
                               "M (kN-m)": 0.0, "V (kN)": 0.0, "Joint V (kN)": None}
                              for key in lc.LOAD_TYPES],
        "factor_rows": [{"Combination ID": c.combination_id, "Name": c.name,
                         "Enabled": c.enabled, **c.factors} for c in lc.REFERENCE_COMBINATIONS],
        "generated_weight": lc.ADD_SELF_WEIGHT,
        "direct_supplements": {}, "generated_supplements": {}, "copied_origins": {},
        "original_case_migrated": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _action_columns() -> dict:
    return {key: st.column_config.NumberColumn(key, required=True, default=0.0, format="%.3f")
            for key in ("P (kN)", "M (kN-m)", "V (kN)")} | {
        "Joint V (kN)": st.column_config.NumberColumn(
            "Joint V (kN)", help="Signed vertical head-joint shear, distinct from horizontal V. Blank means unknown.", format="%.3f"),
        "D for Pd (kN)": st.column_config.NumberColumn(
            "D for Pd (kN)", help="Optional total unfactored dead load INCLUDING self-weight. Blank uses sidebar D plus its self-weight setting.", format="%.3f"),
    }


def _direct_editor() -> None:
    st.caption("Whole-wall concurrent actions, already factored, including applicable second-order effects. "
               "Compression is positive. M and V may be signed. Apply edits to check every enabled row.")
    with st.form("direct_load_form"):
        frame = st.data_editor(pd.DataFrame(st.session_state.direct_rows,
                                            columns=["Case ID", "Name", "Enabled", "P (kN)", "M (kN-m)", "V (kN)",
                                                     "Joint V (kN)", "D for Pd (kN)"]),
                               key=f"direct_editor_{st.session_state.direct_version}",
                               num_rows="dynamic", hide_index=True, width="stretch",
                               column_config=_action_columns() | {
                                   "Case ID": st.column_config.TextColumn(required=True),
                                   "Enabled": st.column_config.CheckboxColumn(default=True),
                               })
        if st.form_submit_button("Apply factored cases"):
            st.session_state.direct_rows = frame.to_dict("records")
            st.session_state.direct_version += 1
            st.rerun()


def _generated_editor() -> None:
    st.caption("Enter signed, unfactored P/M/V contributions at this section. Generation assumes linear superposition; "
               "each W/E reversal reverses its entire vector, including axial load. Zero is an entered value; blank is missing.")
    with st.form("generated_load_form"):
        weight = st.radio("Generated-mode self-weight", [lc.ADD_SELF_WEIGHT, lc.SELF_WEIGHT_INCLUDED],
                          index=0 if st.session_state.generated_weight == lc.ADD_SELF_WEIGHT else 1,
                          key=f"weight_editor_{st.session_state.generated_version}", horizontal=True)
        contributions = st.data_editor(pd.DataFrame(st.session_state.contribution_rows),
                                       key=f"contribution_editor_{st.session_state.generated_version}",
                                       disabled=["Load"], hide_index=True, width="stretch",
                                       column_config=_action_columns())
        st.markdown("**Reference formulas and custom factors**")
        st.caption("These 14 OOP reference formulas are editable and are not an exhaustive or code-certified NBCC set. "
                   "Confirm the project basis, edit/add formulas as needed, and explicitly enter 0 for absent terms.")
        factors = st.data_editor(pd.DataFrame(st.session_state.factor_rows,
                                              columns=["Combination ID", "Name", "Enabled", *lc.LOAD_TYPES]),
                                 key=f"factor_editor_{st.session_state.generated_version}",
                                 num_rows="dynamic", hide_index=True, width="stretch",
                                 column_config={key: st.column_config.NumberColumn(min_value=0.0, required=True, default=0.0)
                                                for key in lc.LOAD_TYPES} | {
                                     "Combination ID": st.column_config.TextColumn(required=True),
                                     "Enabled": st.column_config.CheckboxColumn(default=True)})
        if st.form_submit_button("Apply unfactored actions and combinations"):
            st.session_state.contribution_rows = contributions.to_dict("records")
            st.session_state.factor_rows = factors.to_dict("records")
            st.session_state.generated_weight = weight
            st.session_state.generated_version += 1
            st.rerun()


def _copy_generated(cases: tuple[lc.LoadCase, ...]) -> None:
    st.session_state.direct_rows = lc.direct_rows_from_cases(cases)
    st.session_state.copied_origins = {case.case_id: asdict(case) for case in cases}
    st.session_state.direct_supplements = dict(st.session_state.generated_supplements)
    st.session_state.direct_version += 1
    st.session_state.original_case_migrated = True
    st.session_state.load_mode = DIRECT


def _load_cases(wall: engine.WallInput, mode: str, section: str, basis: str) -> tuple[lc.LoadCase, ...]:
    if mode == DIRECT:
        cases = lc.factored_cases_from_rows(st.session_state.direct_rows, section=section, load_basis=basis)
        origins = st.session_state.copied_origins
        return tuple(replace(c, provenance={**c.provenance, "copied_from": origins[c.case_id]},
                             source="Direct factored case copied from generated actions")
                     if c.case_id in origins else c for c in cases)
    definitions = lc.combinations_from_rows(st.session_state.factor_rows)
    try:
        contributions = lc.contributions_from_rows(st.session_state.contribution_rows)
        weight = engine.wall_self_weight(wall)
        return lc.generate_load_cases(contributions, definitions, self_weight=weight,
                                      self_weight_treatment=st.session_state.generated_weight,
                                      section=section, load_basis=basis)
    except (engine.CalculationError, ValueError, TypeError) as error:
        return tuple(lc.LoadCase(c.combination_id, c.name, lc.Action(), enabled=c.enabled,
                                 source="Generated input error", section=section, load_basis=basis,
                                 factors=c.factors, error=str(error),
                                 provenance={"contributions": st.session_state.contribution_rows})
                     for c in definitions)


def _supplement_editor(wall: engine.WallInput, case: lc.LoadCase, storage_key: str) -> None:
    supplements = st.session_state[storage_key]
    previous = supplements.get(case.case_id, lc.CaseSupplement())
    fingerprint = lc.case_fingerprint(wall, case)
    with st.expander("Case-specific inputs and linked OOP results", expanded=False):
        st.caption(f"Applies only to {case.case_id}. Shared geometry is in the sidebar. "
                   "Save after checking the values against the current wall, section and concurrent actions.")
        if previous.reviewed_for and previous.reviewed_for != fingerprint:
            st.warning("The wall or case has changed. These supplemental inputs are stale until reviewed.")
        with st.form(f"supplement_{storage_key}_{case.case_id}"):
            override_pd = st.checkbox("Use direct factored Pd override", value=previous.pd_override is not None)
            pd_value = st.number_input("Direct factored Pd (kN)", min_value=0.0,
                                       value=float(previous.pd_override or 0.0), step=10.0)
            fraction = st.number_input("Uncracked effective-area fraction Auc/Ae", min_value=0.0, max_value=1.0,
                                       value=float(previous.uncracked_area_fraction), step=0.05,
                                       help="New cases default to zero bond credit. Establish this fraction for the particular load case.")
            override_stack = False
            stack = 0.0
            if wall.bond_pattern == "Stack pattern":
                override_stack = st.checkbox("Override this case's vertical head-joint demand", value=previous.stack_v is not None)
                stack = st.number_input("Factored vertical head-joint shear override (kN)", value=float(previous.stack_v or 0.0))
            oop = {}
            for column, edition in zip(st.columns(2), lc.EDITIONS):
                with column:
                    old = previous.oop.get(edition)
                    provided = st.checkbox(f"{edition} linked OOP result supplied", value=old is not None)
                    utilization = st.number_input(f"{edition} OOP utilization", min_value=0.0,
                                                  value=float(old.utilization if old else 0.0), step=0.05)
                    stability = st.checkbox(f"{edition} OOP stability passes", value=old.stability_passed if old else False)
                    reference = st.text_input(f"{edition} OOP analysis reference", value=old.reference if old else "")
                    if provided:
                        oop[edition] = lc.LinkedOOP(utilization, stability, reference)
            if st.form_submit_button("Save and review case inputs"):
                supplements[case.case_id] = lc.review_supplement(
                    wall, case, lc.CaseSupplement(pd_value if override_pd else None, fraction,
                                                  stack if override_stack else None, oop))
                st.rerun()


def pm_chart(batch: lc.BatchResult, selected_id: str | None) -> alt.Chart | None:
    curves, seen = [], set()
    for member in batch.results:
        if member.result is None or member.edition in seen:
            continue
        seen.add(member.edition)
        result = member.result
        for label, curve in ((result.curve_method, result.curve),
                             (result.alternate_curve_method, result.alternate_curve)):
            if curve is not None:
                curves.extend({"Edition": member.edition, "Curve": label, "Order": index,
                               "M": point.m, "P": point.p} for index, point in enumerate(curve))
    if not curves:
        return None
    demands = []
    for case in batch.cases:
        if not case.enabled or case.error or not all(math.isfinite(v) for v in (case.actions.p, case.actions.m)):
            continue
        statuses = {member.edition: member.resistance_status for member in batch.results if member.case is case}
        overall = next((status for status in ("FAIL", "INVALID", "INCOMPLETE", "PASS") if status in statuses.values()), "INVALID")
        demands.append({"Case ID": case.case_id, "M": abs(case.actions.m), "P": case.actions.p,
                        "Signed M": case.actions.m, "Signed V": case.actions.v, "Result": overall,
                        "Selection": "Selected" if case.case_id == selected_id else "Other case", **statuses})
    curve_frame = pd.DataFrame(curves)
    demand_frame = pd.DataFrame(demands)
    all_m = [row["M"] for row in curves + demands]
    all_p = [row["P"] for row in curves + demands]
    x = alt.X("M:Q", title="|Mf| / moment resistance (kN-m)", scale=alt.Scale(domain=[0, max(1.0, max(all_m) * 1.05)]))
    y = alt.Y("P:Q", title="Pf / axial resistance (kN; compression +)",
              scale=alt.Scale(domain=[min(0, min(all_p)) * 1.05, max(1.0, max(all_p) * 1.05)]))
    lines = alt.Chart(curve_frame).mark_line(strokeWidth=2).encode(
        x=x, y=y, color=alt.Color("Edition:N", scale=alt.Scale(domain=list(lc.EDITIONS), range=["#0b5cab", "#d97706"])),
        strokeDash=alt.StrokeDash("Curve:N", scale=alt.Scale(domain=["Discrete", "Smeared"], range=[[1, 0], [6, 4]])),
        order="Order:Q", tooltip=["Edition:N", "Curve:N", "M:Q", "P:Q"])
    points = alt.Chart(demand_frame).mark_point(size=110, filled=True).encode(
        x=x, y=y, shape=alt.Shape("Selection:N", scale=alt.Scale(domain=["Selected", "Other case"], range=["cross", "circle"])),
        color=alt.Color("Result:N", scale=alt.Scale(domain=["PASS", "FAIL", "INCOMPLETE", "INVALID"],
                                                    range=["#15803d", "#dc2626", "#b45309", "#6b7280"])),
        tooltip=["Case ID:N", "P:Q", "Signed M:Q", "Signed V:Q",
                 alt.Tooltip(field="S304-14", type="nominal"), alt.Tooltip(field="S304:24", type="nominal")])
    return (lines + points).resolve_scale(color="independent").properties(height=480).interactive()


def _selected_details(batch: lc.BatchResult, selected_id: str | None) -> None:
    members = [r for r in batch.results if r.case.case_id == selected_id]
    if not members:
        return
    st.subheader("Selected case details")
    st.caption(f"{selected_id} — selection changes only this detail view and the highlighted point.")
    tabs = st.tabs([r.edition for r in members])
    for tab, member in zip(tabs, members):
        with tab:
            st.write(f"IP resistance: {member.resistance_status} · Additional checks: {member.additional_status}")
            if member.issues:
                st.write("; ".join(member.issues))
            if member.result is None:
                continue
            result = member.result
            st.dataframe(pd.DataFrame([asdict(c) for c in member.resistance_checks]), hide_index=True,
                         width="stretch", key=f"selected_resistance_{member.edition}")
            st.dataframe(pd.DataFrame([{"Status": reports.check_status(c), "Category": c.category,
                                        "Check": c.title, "Result": c.detail, "Clause": c.clause}
                                       for c in result.checks]), hide_index=True, width="stretch",
                         key=f"selected_checks_{member.edition}")
            st.caption(f"Combined IP/OOP interaction screen: {result.stability.combined_utilization if result.stability.combined_utilization is not None else 'not evaluated'}. "
                       "This is the existing rational interaction screen, not a complete wall-compliance verdict.")
            with st.expander("Section model and reinforcement layout"):
                st.write({"Effective area (mm2)": result.section.area,
                          "Gross area (mm2)": result.section.gross_area,
                          "Centroid (mm)": result.section.centroid,
                          "Inertia (mm4)": result.section.inertia,
                          "Bar positions (mm)": result.section.bar_positions})
            with st.expander("Technical calculation trace"):
                st.dataframe(pd.DataFrame([asdict(t) for t in result.trace]), hide_index=True,
                             width="stretch", key=f"selected_trace_{member.edition}")


def render_batch(wall: engine.WallInput) -> None:
    _initialize()
    st.subheader("Load cases at the checked section")
    mode = st.radio("Load entry mode", [DIRECT, GENERATED], key="load_mode", horizontal=True)
    section = st.text_input("Checked section / sliding plane identifier", value="Base", key="section_label")
    basis = st.text_input("Project load basis / analysis reference", value="Project basis to be confirmed", key="load_basis")
    if mode == DIRECT:
        _direct_editor()
    else:
        _generated_editor()
    cases = _load_cases(wall, mode, section, basis)
    if mode == GENERATED:
        st.caption("If combination-specific second-order analysis is needed, copy these cases and enter final concurrent P/M/V in direct mode. "
                   "Copying replaces the direct-mode table; generation provenance and D-for-Pd values are retained.")
        st.button("Copy generated cases to direct mode", on_click=_copy_generated, args=(cases,),
                  disabled=not cases or any(c.error for c in cases))
    storage_key = "direct_supplements" if mode == DIRECT else "generated_supplements"
    if mode == DIRECT and not st.session_state.original_case_migrated:
        original = next((c for c in cases if c.case_id == "CASE-1"), None)
        if original is not None:
            st.session_state[storage_key][original.case_id] = lc.review_supplement(
                wall, original, lc.CaseSupplement(uncracked_area_fraction=0.5))
        st.session_state.original_case_migrated = True
    counts = Counter(c.case_id for c in cases if c.enabled)
    selectable = {c.case_id: c for c in cases if c.enabled and c.case_id and counts[c.case_id] == 1}
    selected_id = None
    if selectable:
        if st.session_state.get("inspected_case") not in selectable:
            st.session_state.inspected_case = next(iter(selectable))
        selected_id = st.selectbox("Case to inspect / review", list(selectable), key="inspected_case")
        _supplement_editor(wall, selectable[selected_id], storage_key)
    batch = lc.check_load_cases(wall, cases, st.session_state[storage_key])
    st.subheader("All-case resistance results")
    if batch.all_ip_pass:
        st.success("All entered IP resistance cases pass.")
    elif batch.results:
        counts = Counter(r.resistance_status for r in batch.results)
        st.warning("Case / edition evaluations: " + ", ".join(f"{n} {status}" for status, n in sorted(counts.items())))
    else:
        st.info("No enabled cases to evaluate.")
    st.caption("IP resistance status covers the implemented section checks. Detailing, connections, OOP, analysis assumptions "
               "and project load-combination coverage must also be resolved; this is not a full CSA compliance verdict.")
    if batch.results:
        frame = pd.DataFrame(reports.result_rows(batch))
        display_columns = ["Case ID", "Edition", "P (kN)", "Signed M (kN-m)", "Signed V (kN)", "Pd (kN)",
                           "Pr,max (kN)", "Tr,max (kN)", "Mr(Pf) (kN-m)", "Vr diagonal (kN)", "Vr sliding (kN)",
                           "Vr stack joint (kN)", "Axial compression utilization", "Axial tension utilization",
                           "P-M utilization", "Diagonal shear utilization", "Sliding utilization", "Stack-joint shear utilization",
                           "IP resistance", "Additional checks", "Supplement review", "Issues"]
        st.dataframe(frame[display_columns], hide_index=True, width="stretch", key="case_results")
        with st.expander("Case actions and combination provenance"):
            st.dataframe(frame[["Case ID", "Edition", "Section", "Load basis", "Source", "Factors", "Direction",
                                "P (kN)", "Signed M (kN-m)", "Signed V (kN)", "Self-weight treatment",
                                "Calculated self-weight (kN)", "D for Pd (kN)", "Pd (kN)", "Input fingerprint"]],
                         hide_index=True, width="stretch", key="case_provenance")
        st.subheader("Governing cases by check")
        st.dataframe(pd.DataFrame(reports.governing_rows(batch)), hide_index=True, width="stretch", key="governing_results")
        st.caption("Unranked or incomplete cases prevent an all-cases pass even when a numeric governing ratio is shown.")
        chart = pm_chart(batch, selected_id)
        if chart is not None:
            st.subheader("P-M interaction — all cases")
            st.altair_chart(chart, width="stretch")
            st.caption("Moment is plotted as a magnitude after signed combination. Partial-grout curves are labelled Discrete/Smeared; "
                       "the governing method is evaluated separately at each case's Pf. Point colour shows the worst IP resistance status across editions.")
        _selected_details(batch, selected_id)
    st.subheader("Export all cases")
    names = {"cases": "All-case results", "governing": "Governing cases", "checks": "Checks", "trace": "Calculation traces", "pm_points": "P-M points"}
    downloads = reports.exports(batch)
    for column, (key, label) in zip(st.columns(5), names.items()):
        column.download_button(f"{label} CSV", downloads[key], f"in_plane_{key}.csv", "text/csv", disabled=not batch.results)
