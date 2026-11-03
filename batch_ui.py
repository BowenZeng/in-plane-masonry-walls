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

nbc = lc.nbc

DIRECT = "Enter factored cases"
GENERATED = "Generate from unfactored actions"
UNDECLARED = "Not declared"

# WP1: the ULS set of generated mode. The NBC 2025 rows exist only for the
# declared conditions (no condition has a default); the legacy set is labelled
# by its content; custom rows are the user's own.
NBC_SET = "NBC 2025 Table 4.1.3.2.-A (declare the load conditions)"
LEGACY_SET = "Legacy 14-row reference set (not NBC 2025)"
CUSTOM_SET = "Custom factors"
LOAD_SETS = (NBC_SET, LEGACY_SET, CUSTOM_SET)

IMPORTANCE_OPTIONS = (UNDECLARED, *nbc.IMPORTANCE_ULS)
OCCUPANCY_OPTIONS = {
    UNDECLARED: None,
    "Ordinary (companion L as tabulated)": "ordinary",
    "Storage, equipment or service room (companion L + 0.5, 4.1.3.2.(7))": "storage_equipment_or_service_room",
}
ROOF_OPTIONS = {
    UNDECLARED: None,
    "No roof live load on this wall": "none",
    "Roof live load included in L": "included_in_L",
    "Roof live load entered separately as L_roof (4.1.5.5)": "separate",
}
DECK_OPTIONS = {
    UNDECLARED: None,
    "No parking deck": "none",
    "Vehicle-accessible parking deck (L_deck and S_deck)": "vehicle_accessible",
    "Long-term vehicle storage deck (L_deck and S_deck)": "long_term_storage",
}
DEAD_PART_OPTIONS = {
    UNDECLARED: None,
    "None: D only": frozenset(),
    "Partition allowance D_partition": frozenset({"D_partition"}),
    "Soil D_soil": frozenset({"D_soil"}),
    "D_partition and D_soil": frozenset({"D_partition", "D_soil"}),
}
FIXED_PART_OPTIONS = {
    UNDECLARED: None,
    "None: W and E fully reversible": frozenset(),
    "Fixed wind part W_fixed": frozenset({"W_fixed"}),
    "Fixed earthquake part E_fixed": frozenset({"E_fixed"}),
    "W_fixed and E_fixed": frozenset({"W_fixed", "E_fixed"}),
}
_CONDITION_FIELDS = (("importance", "importance category", None), ("occupancy", "occupancy", OCCUPANCY_OPTIONS),
                     ("roof", "roof live load", ROOF_OPTIONS), ("deck", "parking deck", DECK_OPTIONS),
                     ("dead_parts", "dead-load parts", DEAD_PART_OPTIONS),
                     ("fixed_parts", "fixed parts of W and E", FIXED_PART_OPTIONS))

# WP3: the seismic declaration of the wall (no default; register I-22).
SEISMIC_SYSTEMS = {UNDECLARED: None, "Part of the SFRS (seismic force-resisting system)": True,
                   "Not part of the SFRS": False}
SFRS_TYPES = (UNDECLARED, "Conventional construction", "Moderately ductile", "Ductile", "Unreinforced", "Other")
_ROLE_LABELS = {"loadbearing_shear_wall": "Loadbearing shear wall",
                "non_loadbearing_shear_wall": "Non-loadbearing shear wall",
                "infill_shear_wall": "Infill shear wall"}
ROLE_OPTIONS = {UNDECLARED: None, **{_ROLE_LABELS.get(role, role): role for role in engine.WALL_ROLES}}
ANALYSIS_BASES = {UNDECLARED: "undeclared",
                  "NBC 4.1.8.2 to 4.1.8.23 analysis": "nbc_4_1_8_2_to_23",
                  "NBC 4.1.8.1 simplified procedure": "nbc_4_1_8_1_simplified",
                  "Imported earthquake actions": "imported"}

_PD_RULE = ("Pd = 0.9 D_cnt + min(0, N_lat - N_cb) + N_cb in every row (register I-1): D_cnt is D plus the self-weight "
            "above the section (plus soil only where it resists, never the partition allowance), N_lat the case's signed "
            "factored W/E axial load and N_cb its declared coupling-beam part; live and snow loads never enter Pd.")
_CRANE = "Crane loads (NBC Table 4.1.3.2.-B) are not generated (F-120): enter crane cases as direct factored cases."
SET_CAPTIONS = {
    NBC_SET: ("NBC 2025 Table 4.1.3.2.-A for the declared conditions. Every principal-load row also appears without its "
              "companion load (companion-zero rows, 4.1.3.2.(3)), with 1.25D and 0.9D in cases 2 to 4; W and E rows are "
              "checked in both senses (their fixed parts never reverse). Lateral earth pressure enters cases 2 to 4 at "
              "1.5H, never reversed, and never case 1 or 5 (Note A-4.1.3.2.(4)); case 5 is 1.0D + 1.0E with no 0.9D + E "
              f"row. {_PD_RULE} {_CRANE} Only ULS rows are generated here, so S and W are the ULS-specified NBC 2025 "
              "loads (no 0.6 / I conversion)."),
    LEGACY_SET: (f"{nbc.LEGACY_LABEL}. Not an NBC 2025 set: it has no companion-zero rows and no H factor. W and E rows "
                 f"are checked in both senses. {_PD_RULE} {_CRANE}"),
    CUSTOM_SET: ("Custom factors are magnitudes (negative factors are refused); W and E rows are checked in both senses "
                 "and their fixed parts never reverse. Enter 0 for absent terms; a part column applies only where its "
                 "factor is entered. Custom rows are not checked against NBC 2025: confirm the project basis. "
                 f"{_PD_RULE} {_CRANE}"),
}


def _factor_rows(combinations) -> list[dict]:
    return [{"Combination ID": c.combination_id, "Name": c.name, "Enabled": c.enabled,
             **{key: c.factors.get(key, 0.0) for key in lc.LOAD_TYPES}} for c in combinations]


def _initialize() -> None:
    defaults = {
        "load_mode": DIRECT, "direct_version": 0, "generated_version": 0,
        "direct_rows": [{"Case ID": "CASE-1", "Name": "Original case", "Enabled": True,
                         "P (kN)": 200.0, "M (kN-m)": 1000.0, "V (kN)": 200.0,
                         "Joint V (kN)": None, lc.DCNT_COLUMN: None, lc.NLAT_COLUMN: None,
                         lc.NCB_COLUMN: None, lc.EQ_COLUMN: False}],
        "contribution_rows": [{"Load": key, "P (kN)": 150.0 if key == "D" else 0.0,
                               "M (kN-m)": 0.0, "V (kN)": 0.0, "Joint V (kN)": None}
                              for key in lc.LOAD_TYPES],
        # factor_rows mirrors the rows of the chosen set on every run (empty
        # while the NBC 2025 conditions are undeclared); custom_factor_rows is
        # the editable custom table.
        "load_set_choice": NBC_SET, "factor_rows": [],
        "custom_factor_rows": _factor_rows(lc.REFERENCE_COMBINATIONS),
        "nbc_conditions": {"importance": UNDECLARED, "occupancy": UNDECLARED, "roof": UNDECLARED,
                           "deck": UNDECLARED, "dead_parts": UNDECLARED, "fixed_parts": UNDECLARED,
                           "soil_depth_m": None, "soil_resisting": False, "soil_saturated": UNDECLARED,
                           "coupling_beam_axial": False},
        "nbc_version": 0,
        "seismic_declaration": {"system": UNDECLARED, "sfrs_type": UNDECLARED, "wall_role": UNDECLARED,
                                "analysis_basis": UNDECLARED, "urm_permitted": False},
        "seismic_version": 0,
        "generated_weight": lc.ADD_SELF_WEIGHT,
        "direct_supplements": {}, "generated_supplements": {}, "copied_origins": {},
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _action_columns() -> dict:
    return {key: st.column_config.NumberColumn(key, required=True, default=0.0, format="%.3f")
            for key in ("P (kN)", "M (kN-m)", "V (kN)")} | {
        "Joint V (kN)": st.column_config.NumberColumn(
            "Joint V (kN)", help="Signed vertical head-joint shear, distinct from horizontal V. Blank means unknown.", format="%.3f"),
        lc.DCNT_COLUMN: st.column_config.NumberColumn(
            lc.DCNT_COLUMN, help="Unfactored counteracting dead load on the section INCLUDING the self-weight above it; "
            "no partition allowance or counteractive soil. Blank uses the sidebar dead-load basis.", format="%.3f"),
        lc.NLAT_COLUMN: st.column_config.NumberColumn(
            lc.NLAT_COLUMN, help="Signed factored W/E axial load of this case (compression +, uplift -). Required for "
            "Pd = 0.9 D_cnt + min(0, N_lat - N_cb) + N_cb; blank leaves sliding and the Pd-dependent shear INCOMPLETE.",
            format="%.3f"),
        lc.NCB_COLUMN: st.column_config.NumberColumn(
            lc.NCB_COLUMN, help="Part of the lateral axial load that is coupling-beam axial load; only this part may add "
            "compression to Pd. Blank = 0.", format="%.3f"),
        lc.EQ_COLUMN: st.column_config.CheckboxColumn(
            lc.EQ_COLUMN, help="Tick when this row's actions include earthquake load: the Clause 16 / 17 seismic gate "
            "then applies even to a wall declared outside the SFRS.", default=False),
    }


def _direct_editor() -> None:
    st.caption("Whole-wall concurrent actions, already factored, including applicable second-order effects. "
               "Compression is positive. M and V may be signed. Apply edits to check every enabled row.")
    rows = pd.DataFrame(st.session_state.direct_rows,
                        columns=["Case ID", "Name", "Enabled", "P (kN)", "M (kN-m)", "V (kN)", "Joint V (kN)",
                                 lc.DCNT_COLUMN, lc.NLAT_COLUMN, lc.NCB_COLUMN, lc.EQ_COLUMN])
    rows[lc.EQ_COLUMN] = rows[lc.EQ_COLUMN].eq(True)  # Rows without the flag are not earthquake cases.
    with st.form("direct_load_form"):
        frame = st.data_editor(rows,
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


def _conditions() -> tuple[nbc.LoadConditions | None, list[str]]:
    """The declared NBC 2025 conditions, or None with the declarations still missing."""
    declared = st.session_state.nbc_conditions
    missing = [label for key, label, _ in _CONDITION_FIELDS if declared[key] == UNDECLARED]
    if missing:
        return None, missing
    parts = set(DEAD_PART_OPTIONS[declared["dead_parts"]]) | set(FIXED_PART_OPTIONS[declared["fixed_parts"]])
    roof, deck = ROOF_OPTIONS[declared["roof"]], DECK_OPTIONS[declared["deck"]]
    if roof == "separate":
        parts.add("L_roof")
    if deck != "none":
        parts |= {"L_deck", "S_deck"}
    soil = "D_soil" in parts
    saturation = declared.get("soil_saturated", UNDECLARED)
    if soil and saturation == UNDECLARED:
        return None, ["whether D_soil is saturated"]
    # Only ULS rows are generated here: S and W are the ULS-specified loads.
    return nbc.LoadConditions(
        importance_category=declared["importance"], uls_specified=True,
        occupancy=OCCUPANCY_OPTIONS[declared["occupancy"]], roof_live_load=roof, parking_deck=deck,
        parts=frozenset(parts), soil_depth_m=declared["soil_depth_m"] if soil else None,
        soil_resisting=bool(declared["soil_resisting"]) and soil,
        coupling_beam_axial=bool(declared["coupling_beam_axial"]),
        soil_saturated=(saturation == "Saturated") if soil else None), []


def _chosen_set() -> tuple[tuple[lc.Combination, ...], nbc.LoadConditions | None, str, str]:
    """(rows, conditions, set name, error) of the chosen ULS set."""
    choice = st.session_state.load_set_choice
    if choice == NBC_SET:
        conditions, missing = _conditions()
        if conditions is None:
            return (), None, "NBC 2025", ("Declare the NBC 2025 load conditions before rows are generated "
                                          f"(none has a default): {', '.join(missing)}.")
        try:
            return lc.NBC2025_COMBINATIONS(conditions), conditions, "NBC 2025", ""
        except ValueError as error:
            return (), conditions, "NBC 2025", str(error)
    if choice == LEGACY_SET:
        return lc.REFERENCE_COMBINATIONS, None, "legacy 14-row reference", ""
    return lc.combinations_from_rows(st.session_state.custom_factor_rows), None, "custom", ""


def _conditions_form() -> None:
    declared = st.session_state.nbc_conditions
    version = st.session_state.nbc_version

    def pick(column, label, options, key, help_text):
        options = list(options)
        return column.selectbox(label, options, index=options.index(declared[key]),
                                key=f"nbc_{key}_{version}", help=help_text)

    with st.form("nbc_conditions_form"):
        st.markdown("**NBC 2025 load conditions** - each changes the rows; none has a default.")
        columns = st.columns(3)
        importance = pick(columns[0], "Importance category", IMPORTANCE_OPTIONS, "importance",
                          "NBC 2025 Table 4.1.2.1. Recorded with the rows; the ULS-specified S and W already include "
                          "their importance factors.")
        occupancy = pick(columns[1], "Occupancy of the area carrying L", OCCUPANCY_OPTIONS, "occupancy",
                         "4.1.3.2.(7): storage, equipment and service rooms add 0.5 to the companion L factor.")
        roof = pick(columns[2], "Roof live load", ROOF_OPTIONS, "roof",
                    "4.1.5.5 / Note (6): a separately entered roof live load L_roof is not combined with snow.")
        deck = pick(columns[0], "Parking deck", DECK_OPTIONS, "deck",
                    "Note (6): deck snow S_deck and deck live load L_deck rows; enter both contributions.")
        dead = pick(columns[1], "Dead-load parts", DEAD_PART_OPTIONS, "dead_parts",
                    "4.1.4.1.(5)-(6), 4.1.3.2.(8): the partition allowance is never counteracting (0.9D rows and "
                    "D_cnt exclude it); case 5 also includes counteracting-part omissions. Soil takes 1.5 "
                    "(or the hs factor) in the 1.25D rows and 1.5 when saturated in case 1.")
        fixed = pick(columns[2], "Fixed parts of W and E", FIXED_PART_OPTIONS, "fixed_parts",
                     "Parts of W or E that do not reverse with the load (e.g. uplift): same factor as W / E, never "
                     "reversed, signed in N_lat.")
        soil_depth = columns[0].number_input(
            "Soil depth hs (m), D_soil only", min_value=0.01, value=declared["soil_depth_m"], step=0.1,
            key=f"nbc_soil_depth_{version}",
            help="4.1.3.2.(8): factor max(1.25, 1 + 0.6/hs) for hs > 1.2 m. Blank = 1.5 on the soil load.")
        soil_resisting = columns[1].checkbox(
            "Soil is part of the load-resisting system", value=declared["soil_resisting"],
            key=f"nbc_soil_resisting_{version}",
            help="4.1.4.1.(6): only then does soil enter the 0.9D rows and D_cnt. Unchecked = no credit.")
        soil_saturated = declared.get("soil_saturated", UNDECLARED)
        if "D_soil" in (DEAD_PART_OPTIONS.get(dead) or ()):
            saturation_options = [UNDECLARED, "Saturated", "Not saturated"]
            soil_saturated = columns[0].selectbox(
                "Soil saturation, D_soil only", saturation_options,
                index=saturation_options.index(soil_saturated), key=f"nbc_soil_saturated_{version}",
                help="4.1.3.2.(9): case 1 uses 1.5 on saturated soil and 1.4 on explicitly nonsaturated soil. "
                     "Declare this before NBC ULS rows are generated.")
        coupling = columns[2].checkbox(
            "E axial load is coupling-beam axial load", value=declared["coupling_beam_axial"],
            key=f"nbc_coupling_{version}",
            help="Only a declared coupling-beam axial load may add compression to Pd. Unchecked = no credit.")
        if st.form_submit_button("Apply NBC 2025 load conditions"):
            st.session_state.nbc_conditions = {
                "importance": importance, "occupancy": occupancy, "roof": roof, "deck": deck,
                "dead_parts": dead, "fixed_parts": fixed,
                "soil_depth_m": None if soil_depth is None else float(soil_depth),
                "soil_resisting": bool(soil_resisting), "soil_saturated": soil_saturated,
                "coupling_beam_axial": bool(coupling)}
            st.session_state.nbc_version += 1
            st.rerun()


def _copy_set_to_custom(rows: list[dict]) -> None:
    st.session_state.custom_factor_rows = [dict(row) for row in rows]
    st.session_state.load_set_choice = CUSTOM_SET
    st.session_state.pop("load_set", None)  # the selectbox re-renders from the stored choice
    st.session_state.generated_version += 1


def _set_selector() -> None:
    # The choice is stored outside the widget key so it survives direct mode,
    # where the selectbox is not rendered.
    st.session_state.load_set_choice = st.selectbox(
        "ULS load-combination set", LOAD_SETS, index=LOAD_SETS.index(st.session_state.load_set_choice),
        key="load_set", help="NBC 2025 rows need the declared conditions below; the legacy set is a reference only; "
                             "custom rows are entered by you.")


def _generated_editor(chosen) -> None:
    combinations, _, _, error = chosen
    choice = st.session_state.load_set_choice
    st.caption("Enter signed, unfactored P/M/V contributions at this section. Generation assumes linear superposition; "
               "each W/E reversal reverses its entire vector, including axial load. Zero is an entered value; blank is missing. "
               "The parts (D_partition to E_fixed) apply only in rows that carry their factor; a non-zero contribution "
               "that no enabled row applies is an error.")
    if choice == NBC_SET:
        _conditions_form()
    with st.form("generated_load_form"):
        weight = st.radio("Generated-mode self-weight", [lc.ADD_SELF_WEIGHT, lc.SELF_WEIGHT_INCLUDED],
                          index=0 if st.session_state.generated_weight == lc.ADD_SELF_WEIGHT else 1,
                          key=f"weight_editor_{st.session_state.generated_version}", horizontal=True)
        contributions = st.data_editor(pd.DataFrame(st.session_state.contribution_rows),
                                       key=f"contribution_editor_{st.session_state.generated_version}",
                                       disabled=["Load"], hide_index=True, width="stretch",
                                       column_config=_action_columns())
        factors = None
        if choice == CUSTOM_SET:
            st.markdown("**Custom factors**")
            st.caption(SET_CAPTIONS[CUSTOM_SET])
            factors = st.data_editor(pd.DataFrame(st.session_state.custom_factor_rows,
                                                  columns=["Combination ID", "Name", "Enabled", *lc.LOAD_TYPES]),
                                     key=f"factor_editor_{st.session_state.generated_version}",
                                     num_rows="dynamic", hide_index=True, width="stretch",
                                     column_config={key: st.column_config.NumberColumn(min_value=0.0, required=True, default=0.0)
                                                    for key in lc.LOAD_TYPES} | {
                                         "Combination ID": st.column_config.TextColumn(required=True),
                                         "Enabled": st.column_config.CheckboxColumn(default=True)})
        if st.form_submit_button("Apply unfactored actions and combinations"):
            st.session_state.contribution_rows = contributions.to_dict("records")
            if factors is not None:
                st.session_state.custom_factor_rows = factors.to_dict("records")
            st.session_state.generated_weight = weight
            st.session_state.generated_version += 1
            st.rerun()
    if choice != CUSTOM_SET:
        st.markdown(f"**{choice}**")
        st.caption(SET_CAPTIONS[choice])
        if error:
            st.error(error)
        rows = _factor_rows(combinations)
        if rows:
            used = [key for key in lc.LOAD_TYPES if key in nbc.BASE_TYPES or any(row[key] for row in rows)]
            st.dataframe(pd.DataFrame([{**{k: row[k] for k in ("Combination ID", "Name")}, **{k: row[k] for k in used},
                                        "Tags": ", ".join(c.tags)} for row, c in zip(rows, combinations)]),
                         hide_index=True, width="stretch", key="preset_factor_rows")
        st.button("Copy these rows to custom factors", on_click=_copy_set_to_custom, args=(rows,), disabled=not rows)


def _copy_generated(cases: tuple[lc.LoadCase, ...]) -> None:
    st.session_state.direct_rows = lc.direct_rows_from_cases(cases)
    st.session_state.copied_origins = {case.case_id: asdict(case) for case in cases}
    st.session_state.direct_supplements = dict(st.session_state.generated_supplements)
    st.session_state.direct_version += 1
    st.session_state.load_mode = DIRECT


def _load_cases(wall: engine.WallInput, mode: str, section: str, basis: str, chosen) -> tuple[lc.LoadCase, ...]:
    if mode == DIRECT:
        cases = lc.factored_cases_from_rows(st.session_state.direct_rows, section=section, load_basis=basis)
        origins = st.session_state.copied_origins
        # A direct row has no factors: a copied generated case keeps its
        # earthquake flag from its origin so the seismic gate stays (WP3).
        return tuple(replace(c, provenance={**c.provenance, "copied_from": origins[c.case_id]},
                             source="Direct factored case copied from generated actions",
                             earthquake_case=c.earthquake_case or bool(origins[c.case_id].get("earthquake_case")))
                     if c.case_id in origins else c for c in cases)
    definitions, conditions, set_name, error = chosen
    if error:
        return (lc.LoadCase("NBC 2025", "NBC 2025 rows not generated", lc.Action(), source="Generated input error",
                            section=section, load_basis=basis, error=error),)
    try:
        contributions = lc.contributions_from_rows(st.session_state.contribution_rows)
        unused = lc.unused_contributions(contributions, definitions, set_name)
        if unused:  # F-006: a load that no enabled row applies is a configuration error
            raise ValueError(" ".join(unused))
        weight = engine.wall_self_weight(wall)
        return lc.generate_load_cases(contributions, definitions, self_weight=weight,
                                      self_weight_treatment=st.session_state.generated_weight,
                                      section=section, load_basis=basis, conditions=conditions)
    except (engine.CalculationError, ValueError, TypeError) as error:
        return tuple(lc.LoadCase(c.combination_id, c.name, lc.Action(), enabled=c.enabled,
                                 source="Generated input error", section=section, load_basis=basis,
                                 factors=c.factors, error=str(error),
                                 provenance={"contributions": st.session_state.contribution_rows})
                     for c in definitions)


def _seismic_form(wall: engine.WallInput) -> None:
    declared = st.session_state.seismic_declaration
    version = st.session_state.seismic_version

    def pick(column, label, options, key, help_text):
        options = list(options)
        return column.selectbox(label, options, index=options.index(declared[key]),
                                key=f"seismic_{key}_{version}", help=help_text)

    with st.expander("Seismic declaration (S304-14 Clause 16 / S304:24 Clause 17)",
                     expanded=declared["system"] == UNDECLARED or declared["wall_role"] == UNDECLARED):
        with st.form("seismic_declaration_form"):
            columns = st.columns(2)
            system = pick(columns[0], "Seismic force-resisting system (SFRS)", SEISMIC_SYSTEMS, "system",
                          "Whether this wall is part of the SFRS. Not declared: the Clause 16 / 17 gate is INCOMPLETE "
                          "in every case; an SFRS wall is always a coverage gap (Clause 16 / 17 not implemented).")
            sfrs_type = pick(columns[1], "SFRS wall type", SFRS_TYPES, "sfrs_type",
                             "Required for an SFRS wall (conventional, moderately ductile, ductile, unreinforced); "
                             "recorded with the cases. It does not remove the gate in this release.")
            role = pick(columns[0], "Shear-wall role", ROLE_OPTIONS, "wall_role",
                        "S304-14 10.12 / S304:24 11.12: infill shear walls are not evaluated (UNSUPPORTED). "
                        "Not declared: the role item is INCOMPLETE.")
            basis = pick(columns[1], "Earthquake analysis basis", ANALYSIS_BASES, "analysis_basis",
                         "The NBC procedure that produced the E actions. The NBC 4.1.8.1 simplified-procedure "
                         "exemption needs its eligibility test on declared hazard values (4.1.8.1.(2)), which this "
                         "view does not perform, so no basis removes the gate here.")
            urm = st.checkbox(
                "Unreinforced shear walls permitted by the seismic data (unreinforced walls only)",
                value=declared["urm_permitted"], key=f"seismic_urm_{version}",
                disabled=wall.wall_type != "Unreinforced masonry",
                help="S304-14 4.5.1 / 16.2.1; S304:24 5.5.1 / 17.2.1; NBC Table 4.1.8.9: confirm from the seismic "
                     "category or hazard index and the building that URM shear walls are permitted. "
                     "Unchecked = not confirmed (the review item stays open).")
            if st.form_submit_button("Apply seismic declaration"):
                st.session_state.seismic_declaration = {"system": system, "sfrs_type": sfrs_type, "wall_role": role,
                                                        "analysis_basis": basis, "urm_permitted": bool(urm)}
                st.session_state.seismic_version += 1
                st.rerun()
        if declared["system"] != UNDECLARED and SEISMIC_SYSTEMS[declared["system"]] and declared["sfrs_type"] == UNDECLARED:
            st.error("Declare the SFRS wall type of this SFRS wall.")


def _seismic_record() -> dict[str, object]:
    declared = st.session_state.seismic_declaration
    system = SEISMIC_SYSTEMS[declared["system"]]
    return {"system": "undeclared" if system is None else "sfrs_wall" if system else "not_sfrs_component",
            "sfrs_type": declared["sfrs_type"] if system else "not applicable",
            "wall_role": ROLE_OPTIONS[declared["wall_role"]] or "undeclared",
            "analysis_basis": ANALYSIS_BASES[declared["analysis_basis"]],
            "urm_seismic_permitted": bool(declared["urm_permitted"])}


def _seismic_wall(wall: engine.WallInput) -> engine.WallInput:
    """The wall with its seismic declaration; earthquake_case stays with the case layer."""
    declared = st.session_state.seismic_declaration
    return replace(wall, sfrs_wall=SEISMIC_SYSTEMS[declared["system"]], wall_role=ROLE_OPTIONS[declared["wall_role"]],
                   urm_seismic_permitted=bool(declared["urm_permitted"]) and wall.wall_type == "Unreinforced masonry")


def _seismic_banner(container, wall: engine.WallInput, cases, batch: lc.BatchResult) -> None:
    """Banner naming Clause 16 / 17 whenever the seismic gate is active (WP3)."""
    earthquake = [c.case_id for c in cases if c.enabled and c.earthquake_case]
    engine_gate = any(check.check_id == "ip.seismic_provisions_not_evaluated" and check.level != "pass"
                      for member in batch.results if member.result is not None for check in member.result.checks)
    if wall.sfrs_wall is False and not earthquake and not engine_gate:
        return
    record = _seismic_record()
    reasons = []
    if wall.sfrs_wall is None:
        reasons.append("the SFRS declaration of the wall is missing")
    elif wall.sfrs_wall:
        sfrs_type = record["sfrs_type"]
        reasons.append("the wall is declared part of the SFRS"
                       + (f" ({sfrs_type.lower()})" if sfrs_type != UNDECLARED else " (type not declared)"))
    if earthquake:
        listed = ", ".join(earthquake[:6]) + (", ..." if len(earthquake) > 6 else "")
        reasons.append(f"{len(earthquake)} enabled case(s) carry an earthquake factor ({listed})")
    text = ("**Seismic provisions not evaluated: S304-14 Clause 16 / S304:24 Clause 17.** The seismic design provisions "
            f"are not implemented, and {' and '.join(reasons) or 'the engine gate is active'}. The affected cases are a "
            "coverage gap (UNSUPPORTED; INCOMPLETE while the SFRS declaration is missing) and never read "
            "CHECKS CLEAR; no review record clears them (register I-22).")
    if record["analysis_basis"] == "nbc_4_1_8_1_simplified":
        text += (" The NBC 4.1.8.1 simplified procedure is declared, but its eligibility (4.1.8.1.(2)) is not tested in "
                 "this view, so the gate stays.")
    container.warning(text)


def _status_message(batch: lc.BatchResult) -> None:
    """F-126: success only when every case passes and no numeric code limit is blocked."""
    limits = reports.blocked_code_limits(batch)
    if batch.all_ip_pass and not limits:
        st.success("All entered IP resistance cases pass.")
        return
    if not batch.results:
        st.info("No enabled cases to evaluate.")
        return
    if batch.all_ip_pass:
        text = "All entered IP resistance cases pass, but numeric code limits are not met or not evaluated"
    else:
        counts = Counter(r.resistance_status for r in batch.results)
        text = "Case / edition evaluations: " + ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
    if limits:
        listed = "; ".join(f"{row['Check']} ({row['Edition']} {row['Clause']}: {row['Status']}, "
                           f"{len(row['Case IDs'])} case(s))" for row in limits)
        text += f". Blocked code limits: {listed}"
    st.warning(text + ".")


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
            override_pd = st.checkbox("Use a sourced case Pd instead of the case composition",
                                      value=previous.pd_override is not None)
            pd_value = st.number_input("Signed case Pd (kN)", value=float(previous.pd_override or 0.0), step=10.0,
                                       help="0.9 x counteracting dead load plus this case's signed lateral axial load, "
                                       "from an external analysis. Negative when uplift exceeds the dead load.")
            pd_source = st.text_input("Source of the case Pd", value=previous.pd_override_source)
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
                    wall, case, lc.CaseSupplement(pd_value if override_pd else None,
                                                  pd_source if override_pd else "", fraction,
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
    _seismic_form(wall)
    wall = _seismic_wall(wall)
    banner = st.container()
    mode = st.radio("Load entry mode", [DIRECT, GENERATED], key="load_mode", horizontal=True)
    section = st.text_input("Checked section / sliding plane identifier", value="Base", key="section_label")
    basis = st.text_input("Project load basis / analysis reference", value="Project basis to be confirmed", key="load_basis")
    if mode == GENERATED:
        _set_selector()
    chosen = _chosen_set()
    st.session_state.factor_rows = _factor_rows(chosen[0])
    if mode == DIRECT:
        _direct_editor()
    else:
        _generated_editor(chosen)
    record = _seismic_record()
    cases = tuple(replace(c, provenance={**c.provenance, "seismic_declaration": record})
                  for c in _load_cases(wall, mode, section, basis, chosen))
    if mode == GENERATED:
        st.caption("If combination-specific second-order analysis is needed, copy these cases and enter final concurrent P/M/V in direct mode. "
                   "Copying replaces the direct-mode table; generation provenance, D_cnt, the signed lateral axial "
                   "loads and the earthquake flag are retained, so the copied cases keep their Pd and seismic gate.")
        st.button("Copy generated cases to direct mode", on_click=_copy_generated, args=(cases,),
                  disabled=not cases or any(c.error for c in cases))
    # No case starts with an uncracked-area credit; it is entered per case (F-045).
    storage_key = "direct_supplements" if mode == DIRECT else "generated_supplements"
    counts = Counter(c.case_id for c in cases if c.enabled)
    selectable = {c.case_id: c for c in cases if c.enabled and c.case_id and counts[c.case_id] == 1}
    selected_id = None
    if selectable:
        if st.session_state.get("inspected_case") not in selectable:
            st.session_state.inspected_case = next(iter(selectable))
        selected_id = st.selectbox("Case to inspect / review", list(selectable), key="inspected_case")
        _supplement_editor(wall, selectable[selected_id], storage_key)
    batch = lc.check_load_cases(wall, cases, st.session_state[storage_key])
    _seismic_banner(banner, wall, cases, batch)
    st.subheader("All-case resistance results")
    _status_message(batch)
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
                                "Calculated self-weight (kN)", "D_cnt (kN)", "N_lat (kN)", "Coupling axial (kN)",
                                "Case Pd (kN)", "Pd (kN)", "Input fingerprint"]],
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
