from __future__ import annotations

import importlib

import altair as alt
import pandas as pd
import streamlit as st

import in_plane_engine as engine


importlib.invalidate_caches()
engine = importlib.reload(engine)
BAR_AREAS = engine.BAR_AREAS
CalculationError = engine.CalculationError
WallInput = engine.WallInput
WallResult = engine.WallResult
calculate_wall = engine.calculate_wall


BUILD_MARKER = "2026-08-29.03"
SOURCE_URL = "https://github.com/BowenZeng/in-plane-masonry-walls"


def _fmt(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value:,.{digits}f}"


def _status(result: WallResult) -> tuple[str, str]:
    blocked = sum(check.level == "blocked" for check in result.checks)
    warnings = sum(check.level == "warning" for check in result.checks)
    if blocked:
        return f"{blocked} BLOCKED", "#b42318"
    if warnings:
        return "REVIEW REQUIRED", "#b54708"
    return "PASS", "#177245"


def _summary(results: dict[str, WallResult]) -> pd.DataFrame:
    rows = []
    for edition, result in results.items():
        rows.extend(
            [
                (edition, "Ungrouted f'm", result.section.fm_ungrouted, "MPa"),
                (edition, "Grouted f'm", result.section.fm_grouted, "MPa"),
                (edition, "Effective f'm", result.section.weighted_fm, "MPa"),
                (edition, "Effective area Ae", result.section.area, "mm2"),
                (edition, "Calculated wall self-weight", result.self_weight, "kN"),
                (edition, "Shear dead-load compression Pd", result.pd, "kN"),
                (edition, "Maximum axial resistance Pr,max", result.axial_cap, "kN"),
                (edition, "Maximum axial-tension resistance", result.tension_cap, "kN"),
                (edition, "Moment resistance Mr(Pf)", result.moment_resistance, "kN-m"),
                (edition, "Alternate partial-grout Mr(Pf)", result.alternate_moment_resistance, "kN-m"),
                (edition, "P-M utilization", result.pm_utilization, "-"),
                (edition, "Diagonal shear resistance Vr", result.shear.resistance, "kN"),
                (
                    edition,
                    "Diagonal shear utilization",
                    result.wall.factored_shear / result.shear.resistance
                    if result.shear.resistance > 0.0
                    else None,
                    "-",
                ),
                (edition, "Sliding shear resistance", result.sliding_resistance, "kN"),
                (edition, "Sliding shear utilization", result.sliding_utilization, "-"),
                (edition, "Shear beta", result.shear.beta, "-"),
                (edition, "Shear theta", result.shear.theta_degrees, "deg"),
                (edition, "Required horizontal area", result.shear.required_horizontal_area, "mm2"),
                (edition, "Required vertical development", result.detailing.vertical_development_required, "mm"),
                (edition, "Combined in-plane plus OOP utilization", result.stability.combined_utilization, "-"),
            ]
        )
    frame = pd.DataFrame(rows, columns=["Edition", "Quantity", "Value", "Unit"])
    return frame


def _comparison_table(results: dict[str, WallResult]) -> pd.DataFrame:
    indexed = _summary(results).pivot(index=["Quantity", "Unit"], columns="Edition", values="Value")
    for edition in ("S304-14", "S304:24"):
        if edition not in indexed:
            indexed[edition] = None
    indexed = indexed[["S304-14", "S304:24"]]
    indexed["Change"] = indexed["S304:24"] - indexed["S304-14"]
    return indexed.reset_index()


def _curve_frame(results: dict[str, WallResult]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for edition, result in results.items():
        for curve_label, curve in (
            ("Selected", result.curve),
            ("Alternate", result.alternate_curve),
        ):
            if curve is None:
                continue
            for order, point in enumerate(curve):
                rows.append(
                    {
                        "Edition": edition,
                        "Curve": curve_label,
                        "Order": order,
                        "Branch": point.branch,
                        "Moment resistance (kN-m)": point.m,
                        "Axial resistance (kN)": point.p,
                    }
                )
    return pd.DataFrame(rows)


def _pm_chart(results: dict[str, WallResult]) -> alt.Chart:
    curves = _curve_frame(results)
    x_max = max(curves["Moment resistance (kN-m)"].max(), next(iter(results.values())).wall.factored_moment) * 1.05
    y_max = max(curves["Axial resistance (kN)"].max(), next(iter(results.values())).wall.factored_axial) * 1.05
    demand = pd.DataFrame(
        [
            {
                "Moment resistance (kN-m)": next(iter(results.values())).wall.factored_moment,
                "Axial resistance (kN)": next(iter(results.values())).wall.factored_axial,
                "Label": "Factored demand",
            }
        ]
    )
    lines = (
        alt.Chart(curves)
        .mark_line(strokeWidth=2.5)
        .encode(
            x=alt.X(
                "Moment resistance (kN-m):Q",
                title="Factored moment resistance (kN-m)",
                scale=alt.Scale(domain=[0.0, x_max]),
            ),
            y=alt.Y(
                "Axial resistance (kN):Q",
                title="Factored axial resistance (kN)",
                scale=alt.Scale(domain=[0.0, y_max]),
            ),
            color=alt.Color(
                "Edition:N",
                scale=alt.Scale(
                    domain=["S304-14", "S304:24"],
                    range=["#0b5cab", "#d97706"],
                ),
            ),
            strokeDash=alt.StrokeDash(
                "Curve:N",
                scale=alt.Scale(domain=["Selected", "Alternate"], range=[[1, 0], [6, 4]]),
            ),
            order="Order:Q",
            tooltip=[
                "Edition:N",
                "Branch:N",
                alt.Tooltip("Moment resistance (kN-m):Q", format=",.1f"),
                alt.Tooltip("Axial resistance (kN):Q", format=",.1f"),
            ],
        )
    )
    marker = (
        alt.Chart(demand)
        .mark_point(size=130, filled=True, color="#1f2937", shape="cross")
        .encode(
            x="Moment resistance (kN-m):Q",
            y="Axial resistance (kN):Q",
            tooltip=[
                "Label:N",
                alt.Tooltip("Moment resistance (kN-m):Q", format=",.1f"),
                alt.Tooltip("Axial resistance (kN):Q", format=",.1f"),
            ],
        )
    )
    return (lines + marker).properties(height=500).interactive()


def _check_frame(result: WallResult) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Status": check.level.upper(),
                "Check": check.title,
                "Result": check.detail,
                "Clause": check.clause,
            }
            for check in result.checks
        ]
    )


def _trace_frame(result: WallResult) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Variable": item.variable,
                "Value": item.value,
                "Unit": item.unit,
                "Basis": item.equation,
                "Clause": item.clause,
            }
            for item in result.trace
        ]
    )


def _edition_notes(wall_type: str) -> list[str]:
    notes = [
        "Table 4 concrete-masonry strengths are evaluated separately for each edition.",
        "S304-14 reinforced flexure is Clause 10; S304:24 moves it to Clause 11. Strong-axis compression uses the same 0.85 phi_m f'm block, while the 2024 wording removes the 2014 direction factor from vertically spanning walls.",
        "For squat reinforced walls, S304:24 adds the distance to the tensile-steel centroid to the effective-depth limit.",
        "S304:24 replaces the 2014 reinforced-wall shear equation with beta-theta modified-compression-field provisions; both simplified and general routes are calculated and can be compared.",
        "S304:24 base sliding shear uses mu C without the S304-14 phi_m multiplier.",
        "S304:24 adds 0.5hw to the bond-beam spacing limit for horizontal shear reinforcement.",
    ]
    if wall_type == "Unreinforced masonry":
        notes.append(
            "For unreinforced in-plane shear, S304:24 Clause 8.10.2.3 omits the 2014 sqrt(f'm) multiplier in vm; the edition curves otherwise retain the elastic/cracked eccentricity branches."
        )
    return notes


def _downloads(results: dict[str, WallResult]) -> tuple[bytes, bytes, bytes]:
    summary = _summary(results).to_csv(index=False).encode("utf-8")
    curves = _curve_frame(results).to_csv(index=False).encode("utf-8")
    checks = pd.concat(
        [frame.assign(Edition=edition) for edition, frame in ((e, _check_frame(r)) for e, r in results.items())],
        ignore_index=True,
    ).to_csv(index=False).encode("utf-8")
    return summary, curves, checks


def main() -> None:
    st.set_page_config(
        page_title="In-plane masonry wall design",
        page_icon="▥",
        layout="wide",
    )
    st.markdown(
        """
        <style>
        .block-container {max-width: 1320px; padding-top: 2rem;}
        div[data-testid="stMetric"] {border: 1px solid #d8dee8; border-radius: .65rem; padding: .8rem 1rem;}
        .status-card {border: 1px solid #d8dee8; border-radius: .65rem; padding: .9rem 1rem; background: #f8fafc;}
        .status-card h3 {margin: 0 0 .25rem 0; font-size: 1.05rem; color: #1f2937;}
        .status-card p {margin: 0; font-weight: 700;}
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("In-plane masonry wall design")
    st.caption(
        f"CSA S304-14 / CSA S304:24 comparison · validated draft · build {BUILD_MARKER}"
    )
    st.write(
        "Rectangular strong-axis wall checks for unreinforced, fully grouted reinforced, and partially grouted reinforced concrete masonry."
    )
    st.link_button("Source code", SOURCE_URL)

    with st.sidebar:
        st.header("Wall definition")
        wall_type = st.selectbox(
            "Wall type",
            [
                "Fully grouted reinforced masonry",
                "Partially grouted reinforced masonry",
                "Unreinforced masonry",
            ],
        )
        geometry = st.columns(2)
        length = geometry[0].number_input("Length lw (mm)", min_value=400.0, value=5000.0, step=100.0)
        height = geometry[1].number_input("Height hw (mm)", min_value=400.0, value=5000.0, step=100.0)
        thickness = st.selectbox("Actual thickness t (mm)", [140.0, 190.0, 240.0, 290.0], index=1)
        bond_pattern = st.selectbox("Bond pattern", ["Running bond", "Stack pattern"])

        st.header("Masonry")
        material = st.columns(2)
        unit_strength = material[0].selectbox("Specified unit strength (MPa)", [10.0, 15.0, 20.0, 25.0, 30.0], index=1)
        mortar = material[1].selectbox("Mortar", ["Type S", "Type N"])
        unreinforced_condition = st.selectbox(
            "Unreinforced section condition",
            ["Hollow ungrouted", "Fully grouted hollow", "Solid units"],
            disabled=wall_type != "Unreinforced masonry",
        )
        partial_method = st.selectbox(
            "Partial-grout flexural method",
            ["Use lower of discrete and smeared", "Discrete", "Smeared"],
            disabled=wall_type != "Partially grouted reinforced masonry",
        )
        face_defaults = {140.0: 26.0, 190.0: 36.2, 240.0: 38.6, 290.0: 41.4}
        face_shell = st.number_input(
            "Effective face-shell thickness tf (mm)",
            min_value=1.0,
            max_value=float(thickness / 2.0 - 0.1),
            value=float(face_defaults[thickness]),
            step=0.1,
            disabled=wall_type == "Fully grouted reinforced masonry",
            help="Physical unit geometry used for ungrouted effective area; verify against the selected unit.",
        )
        grouted_cell_width = st.number_input(
            "Grouted-cell width along wall (mm)",
            min_value=1.0,
            value=200.0,
            step=10.0,
            disabled=wall_type != "Partially grouted reinforced masonry",
        )
        unit_geometry_confirmed = st.checkbox(
            "Selected unit geometry is confirmed",
            value=False,
        )

        st.header("Vertical reinforcement")
        reinforced = wall_type != "Unreinforced masonry"
        steel = st.columns(2)
        bar_size = steel[0].selectbox("Vertical bar", list(BAR_AREAS), index=1, disabled=not reinforced)
        bar_count = int(steel[1].number_input("Number of bars", min_value=2, value=5, step=1, disabled=not reinforced))
        end_distance = st.number_input(
            "End bar to wall end (mm)", min_value=1.0, value=100.0, step=10.0, disabled=not reinforced
        )
        fy = st.number_input("Steel yield strength fy (MPa)", min_value=200.0, max_value=400.0, value=400.0, step=10.0, disabled=not reinforced)
        compression_tied = st.checkbox("Credit tied compression reinforcement", value=False, disabled=not reinforced)
        ties = st.columns(2)
        tie_diameter = ties[0].number_input("Tie diameter (mm)", min_value=3.0, value=3.65, step=0.05, disabled=not compression_tied)
        tie_spacing = ties[1].number_input("Tie spacing (mm)", min_value=1.0, value=200.0, step=25.0, disabled=not compression_tied)
        developed = st.checkbox(
            "Vertical bars develop yield across base sliding plane",
            value=True,
            disabled=not reinforced,
        )
        developed_count = int(st.number_input("Developed vertical bars at sliding plane", min_value=0, value=min(5, bar_count) if reinforced else 0, step=1, disabled=not reinforced or not developed))

        st.header("Horizontal shear reinforcement")
        horizontal_type = st.selectbox(
            "Horizontal reinforcement type",
            ["None", "Bond beam bars", "Joint reinforcement"],
        )
        horizontal = st.columns(2)
        av = horizontal[0].number_input("Area Av at each level (mm2)", min_value=0.0, value=0.0, step=1.0, disabled=horizontal_type == "None")
        shear_spacing = horizontal[1].number_input("Vertical spacing s (mm)", min_value=1.0, value=200.0, step=50.0, disabled=horizontal_type == "None")
        horizontal_fy = st.number_input("Horizontal steel fy (MPa)", min_value=200.0, value=400.0, step=10.0, disabled=horizontal_type == "None")
        horizontal_bar_size = st.selectbox("Bond-beam bar size", list(BAR_AREAS), disabled=horizontal_type != "Bond beam bars")
        joint_wire_diameter = st.number_input("Joint-wire diameter (mm)", min_value=3.0, value=3.65, step=0.05, disabled=horizontal_type != "Joint reinforcement")
        horizontal_anchored = st.checkbox("Horizontal reinforcement anchored at both wall ends", value=False, disabled=horizontal_type == "None")
        horizontal_continuous = st.checkbox("Horizontal reinforcement continuous along wall and at top", value=False, disabled=horizontal_type == "None")
        horizontal_top_bar = st.checkbox("More than 300 mm fresh grout below horizontal bar", value=False, disabled=horizontal_type != "Bond beam bars")
        shear_method = st.selectbox("S304:24 shear method", ["Use lower of simplified and general", "Simplified", "General"])
        grout_type = st.selectbox("Grout type for general shear", ["Coarse", "Fine"])

        st.header("Factored actions")
        actions = st.columns(2)
        pf = actions[0].number_input("Axial load Pf (kN; compression +)", value=200.0, step=10.0)
        mf = actions[1].number_input("In-plane moment Mf (kN-m)", min_value=0.0, value=1000.0, step=50.0)
        vf = actions[0].number_input("In-plane shear Vf (kN)", min_value=0.0, value=200.0, step=10.0)
        dead_above = actions[1].number_input("Unfactored dead load above wall D (kN)", min_value=0.0, value=150.0, step=10.0)
        include_self_weight = st.checkbox("Include calculated wall self-weight in Pd = 0.9D", value=True)
        override_pd = st.checkbox("Use direct factored Pd override", value=False)
        pd_override = st.number_input("Direct factored Pd (kN)", min_value=0.0, value=150.0, step=10.0, disabled=not override_pd)

        st.header("Sliding shear")
        sliding_surface = st.selectbox(
            "Sliding surface roughness",
            ["Masonry / roughened concrete (mu = 1.0)", "Smooth concrete / bare steel (mu = 0.7)"],
        )
        sliding_plane = st.selectbox("Sliding plane", ["Base/support joint", "Between masonry courses"])
        uncracked_fraction = st.number_input("Uncracked effective-area fraction Auc/Ae", min_value=0.0, max_value=1.0, value=0.5, step=0.05, disabled=sliding_plane != "Between masonry courses")

        with st.expander("Development, splices, and constructability"):
            in_situ_grout = st.number_input("In-situ grout strength f'gr (MPa)", min_value=1.0, value=20.0, step=1.0)
            clear_cell = st.number_input("Least clear reinforced-cell dimension (mm)", min_value=1.0, value=100.0, step=5.0)
            epoxy = st.checkbox("Vertical bars are epoxy coated", value=False)
            vertical_development = st.number_input("Available vertical development (mm)", min_value=1.0, value=1200.0, step=50.0)
            horizontal_development = st.number_input("Available horizontal development (mm)", min_value=1.0, value=600.0, step=50.0)
            splice_present = st.checkbox("Vertical tension lap splice present", value=False)
            splice_class = st.selectbox("Splice class", ["B", "A"], disabled=not splice_present)
            lap_length = st.number_input("Provided lap length (mm)", min_value=1.0, value=1200.0, step=50.0, disabled=not splice_present)
            bars_continuous = st.checkbox("Vertical bars continuous over wall height", value=True)
            termination_extension = st.number_input("Available extension past flexural cutoff (mm)", min_value=0.0, value=5000.0, step=100.0, disabled=bars_continuous)
            mortar_joint = st.number_input("Mortar-joint thickness (mm)", min_value=1.0, value=10.0, step=1.0)

        with st.expander("Wall connections and OOP interface"):
            base_connection = st.checkbox("Wall-to-foundation force transfer confirmed", value=False)
            top_connection = st.checkbox("Wall-to-floor/roof force transfer confirmed", value=False)
            oop_provided = st.checkbox("Linked OOP wall result supplied", value=False)
            oop_utilization = st.number_input("OOP utilization", min_value=0.0, value=0.0, step=0.05, disabled=not oop_provided)
            oop_stability = st.checkbox("OOP weak-axis stability passes", value=False, disabled=not oop_provided)

        with st.expander("Self-weight material inputs"):
            masonry_density = st.number_input("Masonry material density (kg/m3)", min_value=1.0, value=2100.0, step=50.0)
            grout_density = st.number_input("Grout density (kg/m3)", min_value=1.0, value=2350.0, step=50.0)
            solid_fraction = st.number_input("Unit solid fraction", min_value=0.01, max_value=1.0, value=0.56, step=0.01)
        st.caption("Inputs are applied unchanged to both editions; only edition rules and table strengths differ.")

    common = dict(
        wall_type=wall_type,
        length=float(length),
        height=float(height),
        thickness=float(thickness),
        nominal_unit_strength=float(unit_strength),
        mortar_type=mortar,
        bond_pattern=bond_pattern,
        unreinforced_condition=unreinforced_condition,
        partial_grout_method=partial_method,
        unit_geometry_confirmed=bool(unit_geometry_confirmed),
        face_shell_thickness=float(face_shell),
        grouted_cell_width=float(grouted_cell_width),
        bar_size=bar_size,
        bar_count=bar_count if reinforced else 0,
        end_bar_distance=float(end_distance),
        fy=float(fy),
        compression_reinforcement_tied=bool(compression_tied),
        tie_diameter=float(tie_diameter),
        tie_spacing=float(tie_spacing),
        horizontal_reinforcement_type=horizontal_type,
        horizontal_bar_size=horizontal_bar_size,
        horizontal_reinforcement_area=float(av),
        horizontal_reinforcement_spacing=float(shear_spacing),
        horizontal_fy=float(horizontal_fy),
        horizontal_anchorage_confirmed=bool(horizontal_anchored),
        horizontal_continuity_confirmed=bool(horizontal_continuous),
        horizontal_top_bar=bool(horizontal_top_bar),
        shear_method=shear_method,
        grout_type=grout_type,
        factored_axial=float(pf),
        factored_moment=float(mf),
        factored_shear=float(vf),
        dead_load_compression=float(pd_override) if override_pd else None,
        unfactored_dead_load_above=float(dead_above),
        include_wall_self_weight_in_pd=bool(include_self_weight),
        masonry_density=float(masonry_density),
        grout_density=float(grout_density),
        unit_solid_fraction=float(solid_fraction),
        sliding_friction=1.0 if "1.0" in sliding_surface else 0.7,
        sliding_plane=sliding_plane,
        uncracked_area_fraction=float(uncracked_fraction),
        vertical_bars_developed_at_base=bool(developed and reinforced),
        developed_vertical_bar_count=developed_count if reinforced and developed else 0,
        in_situ_grout_strength=float(in_situ_grout),
        least_clear_cell_dimension=float(clear_cell),
        epoxy_coated_vertical_bars=bool(epoxy),
        available_vertical_development=float(vertical_development),
        available_horizontal_development=float(horizontal_development),
        splice_present=bool(splice_present),
        splice_class=splice_class,
        provided_lap_length=float(lap_length),
        vertical_bars_continuous=bool(bars_continuous),
        available_termination_extension=float(termination_extension),
        joint_wire_diameter=float(joint_wire_diameter),
        mortar_joint_thickness=float(mortar_joint),
        base_connection_confirmed=bool(base_connection),
        top_connection_confirmed=bool(top_connection),
        oop_check_provided=bool(oop_provided),
        oop_utilization=float(oop_utilization),
        oop_stability_passed=bool(oop_stability),
    )
    results: dict[str, WallResult] = {}
    errors: list[str] = []
    for edition in ("S304-14", "S304:24"):
        try:
            results[edition] = calculate_wall(WallInput(edition=edition, **common))
        except CalculationError as error:
            errors.append(f"{edition}: {error}")
    if errors:
        for error in errors:
            st.error(error)
        st.stop()

    status_columns = st.columns(2)
    for column, (edition, result) in zip(status_columns, results.items()):
        label, color = _status(result)
        with column:
            st.markdown(
                f'<div class="status-card"><h3>{edition}</h3><p style="color:{color}">{label}</p></div>',
                unsafe_allow_html=True,
            )

    result14, result24 = results.values()
    moment_metrics = st.columns(2)
    moment_metrics[0].metric("2014 Mr(Pf) (kN-m)", _fmt(result14.moment_resistance))
    moment_metrics[1].metric("2024 Mr(Pf) (kN-m)", _fmt(result24.moment_resistance))
    shear_metrics = st.columns(2)
    shear_metrics[0].metric("2014 diagonal shear Vr (kN)", _fmt(result14.shear.resistance))
    shear_metrics[1].metric("2024 diagonal shear Vr (kN)", _fmt(result24.shear.resistance))

    st.subheader("Shear branch details")
    shear_rows = []
    for edition, result in results.items():
        shear_rows.append(
            {
                "Edition": edition,
                "Method": result.shear.method,
                "Pd (kN)": result.pd,
                "beta": result.shear.beta,
                "theta (deg)": result.shear.theta_degrees,
                "Vm (kN)": result.shear.masonry_resistance,
                "Vs (kN)": result.shear.steel_resistance,
                "Vr,max (kN)": result.shear.maximum_resistance,
                "Vr (kN)": result.shear.resistance,
                "Av required (mm2)": result.shear.required_horizontal_area,
                "Av,min 2024 (mm2)": result.shear.minimum_horizontal_area_2024,
                "Simplified Vr (kN)": result.shear.simplified_resistance,
                "General Vr (kN)": result.shear.general_resistance,
            }
        )
    st.dataframe(pd.DataFrame(shear_rows), hide_index=True, width="stretch")

    st.subheader("P-M interaction")
    st.altair_chart(_pm_chart(results), width="stretch")
    st.caption(
        "The cross marks the factored demand. Tied compression steel is credited only when selected and tie checks pass. For partial grouting, the alternate curve compares the discrete and smeared routes."
    )

    st.subheader("Edition comparison")
    st.dataframe(
        _comparison_table(results),
        width="stretch",
        hide_index=True,
        column_config={
            "S304-14": st.column_config.NumberColumn(format="%.3f"),
            "S304:24": st.column_config.NumberColumn(format="%.3f"),
            "Change": st.column_config.NumberColumn(format="%+.3f"),
        },
    )

    with st.expander("What changes between editions", expanded=True):
        for note in _edition_notes(wall_type):
            st.markdown(f"- {note}")

    st.subheader("Section checks")
    tabs = st.tabs(list(results))
    for tab, (edition, result) in zip(tabs, results.items()):
        with tab:
            st.dataframe(_check_frame(result), width="stretch", hide_index=True)
            if result.shear.required_horizontal_area is not None:
                st.caption(
                    f"At s = {result.wall.horizontal_reinforcement_spacing:.0f} mm, the calculated area needed for Vf is {_fmt(result.shear.required_horizontal_area, 1)} mm2 before the maximum-shear cap."
                )

    with st.expander("Section model and reinforcement layout"):
        model = result14.section
        rows = [
            ("Effective area Ae", model.area, "mm2"),
            ("Gross area Ag", model.gross_area, "mm2"),
            ("Effective centroid", model.centroid, "mm from left end"),
            ("Effective inertia", model.inertia, "mm4"),
            ("Grouted fraction of Ae", model.grout_fraction, "-"),
            ("Vertical bar spacing", model.bar_spacing, "mm"),
        ]
        st.dataframe(pd.DataFrame(rows, columns=["Quantity", "Value", "Unit"]), hide_index=True, width="stretch")
        if model.bar_positions:
            st.write("Vertical bar positions from the left end (mm):", ", ".join(_fmt(x, 1) for x in model.bar_positions))

    with st.expander("Technical calculation trace"):
        trace_tabs = st.tabs(list(results))
        for tab, (edition, result) in zip(trace_tabs, results.items()):
            with tab:
                st.dataframe(_trace_frame(result), width="stretch", hide_index=True)

    summary_csv, curves_csv, checks_csv = _downloads(results)
    st.subheader("Export")
    download_columns = st.columns(3)
    download_columns[0].download_button("Download comparison CSV", summary_csv, "in_plane_wall_comparison.csv", "text/csv")
    download_columns[1].download_button("Download P-M points CSV", curves_csv, "in_plane_wall_pm_points.csv", "text/csv")
    download_columns[2].download_button("Download checks CSV", checks_csv, "in_plane_wall_checks.csv", "text/csv")

    st.warning(
        "Validated draft for solid rectangular wall-level design. Flanged walls, openings, building-level load distribution, and seismic system classification are excluded. A qualified designer remains responsible for the entered actions, materials, connection details, and linked OOP result."
    )


if __name__ == "__main__":
    main()
