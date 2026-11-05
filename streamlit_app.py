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

BUILD_MARKER = "2026-11-02.01"
SOURCE_URL = "https://github.com/BowenZeng/in-plane-masonry-walls"
# Pinned in requirements.txt (plan 7.3, F-148): width="stretch" and the AppTest
# accessors the UI tests use need this version.
STREAMLIT_FLOOR = (1, 61, 1)

URM = "Unreinforced masonry"
FULL_GROUT = "Fully grouted reinforced masonry"
PARTIAL_GROUT = "Partially grouted reinforced masonry"
BOND_BEAM_ANCHORAGE_OPTIONS = {
    "Not declared (no Vs credit)": None,
    "Standard stirrup hook (10M and 15M only)": "standard_hook",
    "Mechanical anchorage developing fy": "mechanical",
}
DISTRIBUTION_OPTIONS = {
    "Not declared": None,
    "Yes: distributed along the length": True,
    "No: not distributed along the length": False,
}


def streamlit_version(text: str) -> tuple[int, int, int]:
    """Leading numeric release of a version string: "1.61.1" -> (1, 61, 1)."""
    parts: list[int] = []
    for piece in text.split(".")[:3]:
        digits = ""
        for char in piece:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
        if len(digits) < len(piece):
            break
    parts += [0] * (3 - len(parts))
    return parts[0], parts[1], parts[2]


def require_streamlit_floor() -> None:
    """Stop with a message below the pinned Streamlit version (plan 7.3, F-148)."""
    if streamlit_version(st.__version__) < STREAMLIT_FLOOR:
        floor = ".".join(str(part) for part in STREAMLIT_FLOOR)
        st.error(f"This app needs Streamlit {floor} or later; Streamlit {st.__version__} is installed. "
                 "Install the pinned versions with: python -m pip install -r requirements.txt")
        st.stop()


def keyed_seed(key: str, seed: object, *, follow: bool = True) -> None:
    """Seed a keyed input once (plan 7.4); an edited value survives later edits.

    With follow=True an input still equal to its previous seed (never edited)
    takes the new seed when the seed changes, so an untouched reference value or
    base section keeps its meaning. follow=False never changes the value.
    """
    state = st.session_state
    shadow = f"_seed_{key}"
    if key not in state:
        state[key] = seed
    elif follow and shadow in state and state[key] == state[shadow] and seed != state[shadow]:
        state[key] = seed
    state[shadow] = seed


def clamp_keyed(key: str, lower: float | None, upper: float | None, message: str) -> None:
    """Clamp a keyed input into [lower, upper] before its widget renders, with one st.info.

    The lower bound (the widget minimum) wins when the bounds cross; the engine
    then reports the inconsistent geometry as an invalid input.
    """
    value = st.session_state.get(key)
    if value is None:
        return
    bounded = value
    if upper is not None and bounded > upper:
        bounded = upper
    if lower is not None and bounded < lower:
        bounded = lower
    if bounded != value:
        st.session_state[key] = type(value)(bounded)
        st.info(message.format(old=value, new=bounded))


def main() -> None:
    st.set_page_config(
        page_title="In-plane masonry wall design",
        page_icon="▥",
        layout="wide",
    )
    require_streamlit_floor()
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
                FULL_GROUT,
                PARTIAL_GROUT,
                URM,
            ],
        )
        geometry = st.columns(2)
        length = geometry[0].number_input("Length lw (mm)", min_value=400.0, value=5000.0, step=100.0, format="%.0f")
        height = geometry[1].number_input("Height hw (mm)", min_value=400.0, value=5000.0, step=100.0, format="%.0f")
        # 7.4 (F-029): the height above the checked section survives hw edits; an
        # untouched base section follows hw; a value above hw is clamped once.
        keyed_seed("height_above_section", float(height))
        clamp_keyed("height_above_section", 0.0, float(height),
                    "Wall height above the checked section reduced from {old:.0f} mm to hw = {new:.0f} mm.")
        unsupported = st.number_input(
            "Unsupported height between lateral supports (mm)", min_value=1.0, value=None, step=100.0,
            help="Column test, S304-14 2.2 / S304:24 3.1: a column-sized element has h > 5(t + 10) and "
                 "lw < 3(t + 10). Blank = hw (single storey), the larger value.")
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
            disabled=wall_type != URM,
        )
        precracked = st.checkbox("Treat unreinforced wall as cracked under all combinations", value=False, disabled=wall_type != URM)
        proposed_8_10_2_3 = st.checkbox(
            "S304:24 unreinforced shear: apply the proposed 8.10.2.3 correction (vm with sqrt(f'm))",
            value=False,
            disabled=wall_type != URM,
            help="Unchecked = the printed April 2024 Clause 8.10.2.3 (p.57), which omits sqrt(f'm). The June 2025 "
                 "corrections list this clause but their wording has not been verified; checked = the square-root "
                 "form as a proposed correction, named in the shear method (register I-5). Unreinforced walls only.",
        )
        partial_method = st.selectbox(
            "Partial-grout flexural method",
            ["Discrete", "Smeared", "Use lower of discrete and smeared"],
            disabled=wall_type != PARTIAL_GROUT,
            help="Discrete = Table 4 Note 3 grouted and ungrouted parts treated separately (default, register I-9); "
                 "Smeared is a tool idealization; the lower of both is a conservative envelope.",
        )
        references = engine.UNIT_FACE_SHELL_REFERENCE
        uses_face_shell = wall_type == PARTIAL_GROUT or (
            wall_type == URM and unreinforced_condition == "Hollow ungrouted")
        face_shell_limit = float(thickness / 2.0 - 0.1)
        # 7.4 / WP-unit: keyed face-shell inputs; an untouched reference value
        # follows the unit thickness, an entered value is kept and clamped once.
        keyed_seed("face_shell_thickness", float(references["mortar_bedded"][thickness]))
        clamp_keyed("face_shell_thickness", 1.0, face_shell_limit,
                    "Mortar-bedded face-shell thickness reduced from {old:g} mm to {new:g} mm: two face shells must "
                    "leave a hollow core.")
        face_shell = st.number_input(
            "Mortar-bedded face-shell thickness tf (mm) - reference value, confirm",
            min_value=1.0,
            max_value=face_shell_limit,
            step=0.1,
            key="face_shell_thickness",
            disabled=not uses_face_shell,
            help="S304-14 effective area and the clear-cell bound. Prefilled from the comparison workbook "
                 "(CSA A165.1 not supplied; unverified): confirm against the selected unit.",
        )
        keyed_seed("minimum_face_shell_thickness", None, follow=False)
        clamp_keyed("minimum_face_shell_thickness", 1.0, float(face_shell),
                    "S304:24 minimum face-shell thickness reduced from {old:g} mm to the mortar-bedded {new:g} mm.")
        minimum_face_shell = st.number_input(
            "S304:24 minimum face-shell thickness (mm), Clause 3.1",
            min_value=1.0,
            max_value=face_shell_limit,
            step=0.1,
            key="minimum_face_shell_thickness",
            disabled=not uses_face_shell,
            help="S304:24 effective area. Blank = not declared: S304:24 uses the unverified reference value "
                 f"({references['minimum'][thickness]:g} mm) and its results stay INCOMPLETE.",
        )
        grouted_cell_width = st.number_input(
            "Grouted-cell width along wall (mm)",
            min_value=1.0,
            value=200.0,
            step=10.0,
            disabled=wall_type != PARTIAL_GROUT,
        )
        unit_geometry_confirmed = st.checkbox(
            "Selected unit geometry is confirmed",
            value=False,
            disabled=not uses_face_shell,
        )

        st.header("Vertical reinforcement")
        reinforced = wall_type != URM
        steel = st.columns(2)
        bar_size = steel[0].selectbox("Vertical bar", list(BAR_AREAS), index=1, disabled=not reinforced)
        bar_count = int(steel[1].number_input("Number of bars", min_value=2, value=5, step=1, disabled=not reinforced))
        end_distance = st.number_input(
            "End bar to wall end (mm)", min_value=1.0, value=100.0, step=10.0, disabled=not reinforced
        )
        fy = st.number_input("Steel yield strength fy (MPa)", min_value=200.0, max_value=400.0, value=400.0, step=10.0, disabled=not reinforced)
        distribution_choice = st.selectbox(
            "Flexural reinforcement distributed along the length (S304-14 dv)",
            list(DISTRIBUTION_OPTIONS),
            disabled=not reinforced,
            help="S304-14 10.10.2.1: dv need not be taken less than 0.8 lw only for walls whose flexural reinforcement "
                 "is distributed along the length; otherwise dv = min(0.8 lw, d). Not declared: three or more evenly "
                 "spaced bars are read as distributed (provisional, register I-8); a two-bar wall gets no 0.8 lw "
                 "credit and the item is INCOMPLETE.",
        )
        compression_tied = st.checkbox("Credit tied compression reinforcement", value=False, disabled=not reinforced)
        ties = st.columns(2)
        tie_diameter = ties[0].number_input("Tie diameter (mm)", min_value=3.0, value=3.65, step=0.05, disabled=not compression_tied)
        tie_spacing = ties[1].number_input("Tie spacing (mm)", min_value=1.0, value=200.0, step=25.0, disabled=not compression_tied)
        tie_arrangement = st.checkbox(
            "Compression-steel tie arrangement confirmed: every bar laterally supported, hooks and placement",
            value=False,
            disabled=not reinforced or not compression_tied,
            help="S304-14 12.2.6 (12.2.2, 12.2.5) / S304:24 13.2.6 (13.2.2, 13.2.5): a review item. Size and spacing "
                 "are checked separately; the compression steel is credited only when all three hold (F-070).",
        )
        developed = st.checkbox(
            "Vertical bars develop yield on both sides of sliding plane",
            value=True,
            disabled=not reinforced,
        )
        # 7.4 (F-028): blank = all bars (the engine default); an entered count is
        # never raised automatically and is reduced to the bar count once.
        keyed_seed("developed_bar_count", None, follow=False)
        clamp_keyed("developed_bar_count", 0, bar_count,
                    "Developed vertical bars at the sliding plane reduced from {old} to the {new} bars entered; the "
                    "count is never raised automatically.")
        developed_count = st.number_input(
            "Developed vertical bars at sliding plane",
            min_value=0,
            step=1,
            key="developed_bar_count",
            placeholder="all bars",
            disabled=not reinforced or not developed,
            help=f"S304-14 10.10.5.1 / S304:24 11.10.5.1: bars developed for fy on both sides of the sliding plane. "
                 f"Blank = all {bar_count} bars. An entered count is kept when the bar count changes and is reduced "
                 "(with a notice) only when it exceeds it.",
        )

        st.header("Horizontal shear reinforcement")
        horizontal_type = st.selectbox(
            "Horizontal reinforcement type",
            ["None", "Bond beam bars", "Joint reinforcement"],
        )
        bond_beams = horizontal_type == "Bond beam bars"
        horizontal = st.columns(2)
        av = horizontal[0].number_input("Area Av at each level (mm2)", min_value=0.0, value=0.0, step=1.0, disabled=horizontal_type == "None")
        shear_spacing = horizontal[1].number_input("Vertical spacing s (mm)", min_value=1.0, value=200.0, step=50.0, disabled=horizontal_type == "None")
        horizontal_fy = st.number_input("Horizontal steel fy (MPa)", min_value=200.0, value=400.0, step=10.0, disabled=horizontal_type == "None")
        horizontal_bar_size = st.selectbox("Bond-beam bar size", list(BAR_AREAS), disabled=not bond_beams)
        anchorage_choice = st.selectbox(
            "Bond-beam bar end anchorage",
            list(BOND_BEAM_ANCHORAGE_OPTIONS),
            disabled=not bond_beams,
            help="S304-14 12.4.11.2 / S304:24 13.4.11.2: a standard stirrup hook around the end vertical bar anchors "
                 "15M and smaller bars only; mechanical anchorage develops fy for any size. Blank / not declared = no "
                 "credit: the bond-beam Vs is not credited (F-080).",
        )
        beam_layout = st.columns(2)
        bars_per_beam = beam_layout[0].number_input(
            "Bars per bond beam",
            min_value=1,
            value=None,
            step=1,
            disabled=not bond_beams,
            help="S304-14 12.4.2.3 / S304:24 13.4.2.3 (dcs): one bar develops against the bond-beam level pitch, two or "
                 "more against their spacing inside the beam; Av at each level must equal bars x Ab. Blank / not "
                 "declared = no credit for Av above one bar (F-074).",
        )
        beam_clear_spacing = beam_layout[1].number_input(
            "Clear spacing of the bars inside a bond beam (mm)",
            min_value=1.0,
            value=None,
            step=5.0,
            disabled=not bond_beams,
            help="Required for two or more bars per bond beam: dcs = 2/3 of their c/c spacing (12.4.2.3 / 13.4.2.3). "
                 "Blank / not declared = no credit (the bond-beam Vs is not credited).",
        )
        grout_courses = not (wall_type == FULL_GROUT or (wall_type == URM and unreinforced_condition == "Solid units"))
        beam_courses = beam_layout[0].number_input(
            "Bond-beam courses above the checked section",
            min_value=0,
            value=None,
            step=1,
            disabled=not bond_beams or not grout_courses,
            help="S304-14 2.2 / S304:24 3.1 (bond beam = grouted course): the grout of each course above the section "
                 "is added to the self-weight of a wall with ungrouted cells, and so to D_cnt (F-042). Blank / not "
                 "declared = no grout credit and the self-weight basis is INCOMPLETE.",
        )
        beam_course_size = beam_layout[1].number_input(
            "Bond-beam course height (mm)",
            min_value=1.0,
            value=None,
            step=10.0,
            disabled=not bond_beams or not grout_courses,
            help="Height of one grouted bond-beam course; no default (F-042). Blank / not declared = no grout credit.",
        )
        joint_wire_diameter = st.number_input("Joint-wire diameter (mm)", min_value=3.0, value=3.65, step=0.05, disabled=horizontal_type != "Joint reinforcement")
        joint_fgr = st.number_input(
            "Joint-reinforcement f'gr in Type N mortar (MPa)",
            min_value=0.1,
            value=None,
            step=1.0,
            disabled=horizontal_type != "Joint reinforcement" or mortar != "Type N",
            help="S304-14 12.4.1.3 / S304:24 13.4.1.3: wire in Type S mortar develops with f'gr = 10 MPa; Type N mortar "
                 "needs the declared value. Blank / not declared = no credit (the joint reinforcement is not credited).",
        )
        horizontal_anchored = st.checkbox(
            "Joint reinforcement (or unreinforced-wall steel) anchored at both wall ends",
            value=False,
            disabled=horizontal_type == "None" or (bond_beams and reinforced),
            help="Joint reinforcement: mechanical anchorage developing fy (12.4.11.2(b) / 13.4.11.2 b)). Bond-beam bars "
                 "of reinforced walls use the end-anchorage declaration above.",
        )
        horizontal_continuous = st.checkbox("Horizontal reinforcement continuous along wall and at top", value=False, disabled=horizontal_type == "None", help="For design joint reinforcement, confirm full anchorage, Class B splices and corner/intersection detailing.")
        top_bar_choice = st.selectbox(
            "Fresh grout below the bond-beam bars (bar-location factor k1)",
            ["Not declared (k1 = 1.3 used)", "More than 300 mm: k1 = 1.3", "300 mm or less: k1 = 1.0"],
            disabled=not bond_beams,
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
                                     help="Permanent dead load only: no partition allowance, no counteractive soil, "
                                          "no live or snow load.")
        include_self_weight = st.checkbox("Add the calculated wall self-weight to D_cnt", value=True)
        st.caption("Used for direct cases whose row leaves D_cnt blank; generated cases form D_cnt from D and the "
                   "self-weight. Each case's Pd = 0.9 D_cnt + min(0, N_lat - N_cb) + N_cb in every combination "
                   "(register I-1): N_lat is that case's signed factored W/E axial load (entered in the case table; "
                   "blank leaves sliding and the Pd-dependent shear routes INCOMPLETE) and N_cb its declared "
                   "coupling-beam part. Live and snow loads never enter Pd, and the entered Pf is never increased "
                   "automatically.")

        st.header("Sliding shear")
        sliding_surface = st.selectbox(
            "Sliding surface roughness",
            ["Masonry / roughened concrete (mu = 1.0)", "Smooth concrete / bare steel (mu = 0.7)"],
        )
        sliding_plane = st.selectbox("Sliding plane", ["Base/support joint", "Between masonry courses"])
        st.caption("Uncracked-area credit is entered and reviewed separately for each case.")

        with st.expander("Development, splices, and constructability"):
            in_situ_grout = st.number_input("In-situ grout strength f'gr (MPa)", min_value=1.0, value=20.0, step=1.0)
            hollow_core = float(thickness - 2.0 * face_shell)
            keyed_seed("least_clear_cell_dimension", float(min(100.0, hollow_core)))
            clamp_keyed("least_clear_cell_dimension", 1.0, hollow_core,
                        "Least clear cell dimension reduced from {old:g} mm to the hollow core t - 2 tf = {new:g} mm.")
            clear_cell = st.number_input("Least clear reinforced-cell dimension (mm) - reference value, confirm", min_value=1.0,
                                         step=5.0, key="least_clear_cell_dimension",
                                         help="Prefilled with min(100, t - 2 tf); declare the selected unit's clear cell. "
                                              "An entered value is kept when t or tf change.")
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
            height_above = st.number_input(
                "Wall height above checked section (mm)", min_value=0.0, max_value=float(height), step=100.0,
                key="height_above_section",
                help="Use zero at the top joint; enter only the height above an intermediate joint. Equal to hw = the "
                     "base section, which follows hw; an entered value is kept when hw changes and reduced only when "
                     "it exceeds hw.")

    common = dict(
        wall_type=wall_type,
        length=float(length),
        height=float(height),
        unsupported_height=None if unsupported is None else float(unsupported),
        thickness=float(thickness),
        nominal_unit_strength=float(unit_strength),
        mortar_type=mortar,
        bond_pattern=bond_pattern,
        free_standing=bool(free_standing),
        unreinforced_condition=unreinforced_condition,
        unreinforced_precracked=bool(precracked and not reinforced),
        partial_grout_method=partial_method,
        apply_proposed_8_10_2_3_correction=bool(proposed_8_10_2_3 and wall_type == URM),
        unit_geometry_confirmed=bool(unit_geometry_confirmed),
        face_shell_thickness=float(face_shell),
        minimum_face_shell_thickness=None if minimum_face_shell is None else float(minimum_face_shell),
        grouted_cell_width=float(grouted_cell_width),
        bar_size=bar_size,
        bar_count=bar_count if reinforced else 0,
        end_bar_distance=float(end_distance),
        fy=float(fy),
        flexural_reinforcement_distributed=DISTRIBUTION_OPTIONS[distribution_choice] if reinforced else None,
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
        horizontal_top_bar=bool(horizontal_top_bar and bond_beams),
        bond_beam_anchorage=BOND_BEAM_ANCHORAGE_OPTIONS[anchorage_choice] if bond_beams else None,
        bars_per_bond_beam=int(bars_per_beam) if bond_beams and bars_per_beam is not None else None,
        bond_beam_bar_clear_spacing=(float(beam_clear_spacing)
                                     if bond_beams and beam_clear_spacing is not None else None),
        bond_beam_levels_above_section=(int(beam_courses)
                                        if bond_beams and grout_courses and beam_courses is not None else None),
        bond_beam_course_height=(float(beam_course_size)
                                 if bond_beams and grout_courses and beam_course_size is not None else None),
        joint_reinforcement_fgr=(float(joint_fgr) if horizontal_type == "Joint reinforcement" and mortar == "Type N"
                                 and joint_fgr is not None else None),
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
        developed_vertical_bar_count=((None if developed_count is None else int(developed_count))
                                      if reinforced and developed else 0),
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
