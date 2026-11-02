from __future__ import annotations

import importlib

import streamlit as st

import in_plane_engine as engine
import load_combinations
import batch_reporting
import batch_ui

importlib.invalidate_caches()
engine = importlib.reload(engine)
load_combinations = importlib.reload(load_combinations)
batch_reporting = importlib.reload(batch_reporting)
batch_ui = importlib.reload(batch_ui)
BAR_AREAS = engine.BAR_AREAS
WallInput = engine.WallInput

BUILD_MARKER = "2026-10-05.01"
SOURCE_URL = "https://github.com/BowenZeng/in-plane-masonry-walls"


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
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("In-plane masonry wall design")
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
        length = geometry[0].number_input("Length lw (mm)", min_value=400.0, value=5000.0, step=100.0, format="%.0f")
        height = geometry[1].number_input("Height hw (mm)", min_value=400.0, value=5000.0, step=100.0, format="%.0f")
        thickness = st.selectbox("Actual thickness t (mm)", [140.0, 190.0, 240.0, 290.0], index=1)
        bond_pattern = st.selectbox("Bond pattern", ["Running bond", "Stack pattern"])
        free_standing = st.checkbox("Free-standing cantilever (no top or side supports)", value=False)

        st.header("Masonry")
        material = st.columns(2)
        unit_strength = material[0].selectbox("Specified unit strength (MPa)", [10.0, 15.0, 20.0, 25.0, 30.0], index=1)
        mortar = material[1].selectbox("Mortar", ["Type S", "Type N"])
        unreinforced_condition = st.selectbox(
            "Unreinforced section condition",
            ["Hollow ungrouted", "Fully grouted hollow", "Solid units"],
            disabled=wall_type != "Unreinforced masonry",
        )
        precracked = st.checkbox("Treat unreinforced wall as cracked under all combinations", value=False, disabled=wall_type != "Unreinforced masonry")
        partial_method = st.selectbox(
            "Partial-grout flexural method",
            ["Use lower of discrete and smeared", "Discrete", "Smeared"],
            disabled=wall_type != "Partially grouted reinforced masonry",
        )
        references = engine.UNIT_FACE_SHELL_REFERENCE
        uses_face_shell = wall_type == "Partially grouted reinforced masonry" or (
            wall_type == "Unreinforced masonry" and unreinforced_condition == "Hollow ungrouted")
        face_shell = st.number_input(
            "Mortar-bedded face-shell thickness tf (mm) - reference value, confirm",
            min_value=1.0,
            max_value=float(thickness / 2.0 - 0.1),
            value=float(references["mortar_bedded"][thickness]),
            step=0.1,
            disabled=not uses_face_shell,
            help="S304-14 effective area and the clear-cell bound. Prefilled from the comparison workbook "
                 "(CSA A165.1 not supplied; unverified): confirm against the selected unit.",
        )
        minimum_face_shell = st.number_input(
            "S304:24 minimum face-shell thickness (mm), Clause 3.1",
            min_value=1.0,
            max_value=float(thickness / 2.0 - 0.1),
            value=None,
            step=0.1,
            disabled=not uses_face_shell,
            help="S304:24 effective area. Blank = not declared: S304:24 uses the unverified reference value "
                 f"({references['minimum'][thickness]:g} mm) and its results stay INCOMPLETE.",
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
            disabled=not uses_face_shell,
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
        tie_arrangement = st.checkbox("Tie hooks, placement, and lateral support of bars confirmed", value=False, disabled=not reinforced or not compression_tied)
        developed = st.checkbox(
            "Vertical bars develop yield on both sides of sliding plane",
            value=True,
            disabled=not reinforced,
        )
        developed_count = int(st.number_input("Developed vertical bars at sliding plane", min_value=0, max_value=bar_count if reinforced else 0, value=min(5, bar_count) if reinforced else 0, step=1, disabled=not reinforced or not developed))

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
        horizontal_continuous = st.checkbox("Horizontal reinforcement continuous along wall and at top", value=False, disabled=horizontal_type == "None", help="For design joint reinforcement, confirm full anchorage, Class B splices and corner/intersection detailing.")
        top_bar_choice = st.selectbox(
            "Fresh grout below the bond-beam bars (bar-location factor k1)",
            ["Not declared (k1 = 1.3 used)", "More than 300 mm: k1 = 1.3", "300 mm or less: k1 = 1.0"],
            disabled=horizontal_type != "Bond beam bars",
            help="12.4.2.5(a) / 13.4.2.5(a). Declare the condition; until then the larger k1 = 1.3 is used.")
        horizontal_top_bar = not top_bar_choice.startswith("300 mm or less")
        shear_method = st.selectbox("S304:24 shear method", ["Use lower of simplified and general", "Simplified", "General"])
        grout_choice = st.selectbox("Grout type for general shear", ["Not declared", "Coarse", "Fine"],
                                    help="ze = ga zs with ga 1.4 (coarse) or 1.7 (fine). Not declared: the General method "
                                         "is refused, a general route forced by axial tension uses fine grout and is "
                                         "INCOMPLETE, and the 'lower of' candidate uses 1.4 bounded by the simplified route.")
        grout_type = None if grout_choice == "Not declared" else grout_choice
        squat_distribution = st.checkbox("Squat-wall shear is distributed along the full length", value=False, disabled=height >= length, help="Required to use the increased 2014 shear cap, or the increased unreinforced 2024 shear cap.")
        stack_supplied = False
        stack_area = 0.0
        if bond_pattern == "Stack pattern":
            with st.expander("Vertical head-joint reinforcement", expanded=True):
                stack_supplied = st.checkbox("Head-joint crossing reinforcement is supplied", value=False)
                stack_area = st.number_input("Total developed area crossing assessed head joint (mm2)", min_value=0.0, value=0.0, step=100.0)
                st.caption("Enter the concurrent vertical joint shear with each load case, or each unfactored contribution.")

        st.header("Direct-case counteracting dead load")
        dead_above = st.number_input("Unfactored counteracting dead load above wall D_cnt (kN)", min_value=0.0, value=150.0, step=10.0,
                                     help="Permanent dead load only: no partition allowance and no counteractive soil.")
        include_self_weight = st.checkbox("Add the calculated wall self-weight to D_cnt", value=True)
        st.caption("Used for direct cases without their own D_cnt. Each case's Pd = 0.9 D_cnt + min(0, N_lat - N_cb) + N_cb "
                   "needs that case's signed lateral axial load N_lat (entered in the case table); without it sliding and "
                   "the Pd-dependent shear routes are INCOMPLETE. Entered Pf is never increased automatically.")

        st.header("Sliding shear")
        sliding_surface = st.selectbox(
            "Sliding surface roughness",
            ["Masonry / roughened concrete (mu = 1.0)", "Smooth concrete / bare steel (mu = 0.7)"],
        )
        sliding_plane = st.selectbox("Sliding plane", ["Base/support joint", "Between masonry courses"])
        st.caption("Uncracked-area credit is entered and reviewed separately for each case.")

        with st.expander("Development, splices, and constructability"):
            in_situ_grout = st.number_input("In-situ grout strength f'gr (MPa)", min_value=1.0, value=20.0, step=1.0)
            clear_cell = st.number_input("Least clear reinforced-cell dimension (mm) - reference value, confirm", min_value=1.0,
                                         value=float(min(100.0, thickness - 2.0 * face_shell)), step=5.0,
                                         help="Prefilled with min(100, t - 2 tf); declare the selected unit's clear cell.")
            epoxy = st.checkbox("Vertical bars are epoxy coated", value=False)
            vertical_development = st.number_input("Available vertical development (mm)", min_value=1.0, value=1200.0, step=50.0)
            horizontal_development = st.number_input("Available horizontal development (mm)", min_value=1.0, value=600.0, step=50.0)
            splice_present = st.checkbox("Vertical tension lap splice present", value=False)
            splice_class = st.selectbox("Splice class", ["B", "A"], disabled=not splice_present)
            class_a_confirmed = st.checkbox("Class A: As provided/required >= 2 and at most 50% spliced", value=False, disabled=not splice_present or splice_class != "A")
            lap_length = st.number_input("Provided lap length (mm)", min_value=1.0, value=None, step=50.0, disabled=not splice_present,
                                         help="Blank = not entered: the lap-splice check is INCOMPLETE.")
            bars_continuous = st.checkbox("Vertical bars continuous over wall height", value=True)
            termination_extension = st.number_input("Available extension past flexural cutoff (mm)", min_value=0.0, value=5000.0, step=100.0, disabled=bars_continuous)
            mortar_joint = st.number_input("Mortar-joint thickness (mm)", min_value=1.0, value=10.0, step=1.0)

        with st.expander("Wall connections"):
            base_connection = st.checkbox("Wall-to-foundation force transfer confirmed", value=False)
            top_connection = st.checkbox("Wall-to-floor/roof force transfer confirmed", value=False, disabled=free_standing)

        with st.expander("Self-weight material inputs"):
            masonry_density = st.number_input("Masonry material density (kg/m3)", min_value=1.0, value=2100.0, step=50.0)
            grout_density = st.number_input("Grout density (kg/m3)", min_value=1.0, value=2350.0, step=50.0)
            solid_fraction = st.number_input("Unit solid fraction", min_value=0.01, max_value=1.0, value=0.56, step=0.01)
            height_above = st.number_input("Wall height above checked section (mm)", min_value=0.0, max_value=float(height), value=float(height), step=100.0, help="Use zero at the top joint; enter only the height above an intermediate joint.")

    common = dict(
        wall_type=wall_type,
        length=float(length),
        height=float(height),
        thickness=float(thickness),
        nominal_unit_strength=float(unit_strength),
        mortar_type=mortar,
        bond_pattern=bond_pattern,
        free_standing=bool(free_standing),
        unreinforced_condition=unreinforced_condition,
        unreinforced_precracked=bool(precracked and not reinforced),
        partial_grout_method=partial_method,
        unit_geometry_confirmed=bool(unit_geometry_confirmed),
        face_shell_thickness=float(face_shell),
        minimum_face_shell_thickness=None if minimum_face_shell is None else float(minimum_face_shell),
        grouted_cell_width=float(grouted_cell_width),
        bar_size=bar_size,
        bar_count=bar_count if reinforced else 0,
        end_bar_distance=float(end_distance),
        fy=float(fy),
        compression_reinforcement_tied=bool(compression_tied and reinforced),
        tie_arrangement_confirmed=bool(tie_arrangement and compression_tied and reinforced),
        tie_diameter=float(tie_diameter),
        tie_spacing=float(tie_spacing),
        horizontal_reinforcement_type=horizontal_type,
        horizontal_bar_size=horizontal_bar_size,
        horizontal_reinforcement_area=float(av) if horizontal_type != "None" else 0.0,
        horizontal_reinforcement_spacing=float(shear_spacing),
        horizontal_fy=float(horizontal_fy),
        horizontal_anchorage_confirmed=bool(horizontal_anchored and horizontal_type != "None"),
        horizontal_continuity_confirmed=bool(horizontal_continuous and horizontal_type != "None"),
        horizontal_top_bar=bool(horizontal_top_bar and horizontal_type == "Bond beam bars"),
        shear_method=shear_method,
        grout_type=grout_type,
        squat_shear_distribution_confirmed=bool(squat_distribution),
        stack_joint_area=float(stack_area) if stack_supplied else None,
        stack_vertical_shear=None,
        factored_axial=0.0,
        factored_moment=0.0,
        factored_shear=0.0,
        dead_load_compression=None,
        unfactored_dead_load_above=float(dead_above),
        include_wall_self_weight_in_pd=bool(include_self_weight),
        height_above_section=float(height_above),
        masonry_density=float(masonry_density),
        grout_density=float(grout_density),
        unit_solid_fraction=float(solid_fraction),
        sliding_friction=1.0 if "1.0" in sliding_surface else 0.7,
        sliding_plane=sliding_plane,
        uncracked_area_fraction=0.0,
        vertical_bars_developed_at_base=bool(developed and reinforced),
        developed_vertical_bar_count=developed_count if reinforced and developed else 0,
        in_situ_grout_strength=float(in_situ_grout),
        least_clear_cell_dimension=float(clear_cell),
        epoxy_coated_vertical_bars=bool(epoxy),
        available_vertical_development=float(vertical_development),
        available_horizontal_development=float(horizontal_development),
        splice_present=bool(splice_present and reinforced),
        splice_class=splice_class,
        class_a_splice_confirmed=bool(class_a_confirmed),
        provided_lap_length=None if lap_length is None else float(lap_length),
        vertical_bars_continuous=bool(bars_continuous),
        available_termination_extension=float(termination_extension),
        joint_wire_diameter=float(joint_wire_diameter),
        mortar_joint_thickness=float(mortar_joint),
        base_connection_confirmed=bool(base_connection),
        top_connection_confirmed=bool(top_connection),
        oop_check_provided=False,
        oop_stability_passed=False,
    )
    batch_ui.render_batch(WallInput(**common))

    st.caption(f"Build {BUILD_MARKER}")


if __name__ == "__main__":
    main()
