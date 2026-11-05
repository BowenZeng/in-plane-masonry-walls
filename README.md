# In-plane masonry wall design

Independent Streamlit design aid comparing CSA S304-14 and CSA S304:24 for rectangular concrete-masonry walls under in-plane axial load, strong-axis moment, diagonal shear, and sliding shear.

## Multiple load combinations

Choose **Enter factored cases** for externally calculated concurrent P/M/V, or
**Generate from unfactored actions** for signed D/L/S/W/E/H/Other contributions
and the declared parts (D_partition, D_soil, L_roof, L_deck, S_deck, W_fixed,
E_fixed). Apply the input table to evaluate every enabled case for both editions.
Selecting a case only changes the detailed checks and highlighted chart point.

Generated mode offers three ULS sets. **NBC 2025 Table 4.1.3.2.-A** is the
default and has no default conditions: declare the importance category,
occupancy, roof live load, parking deck, dead-load parts and fixed parts of W
and E in its conditions form before any row is generated. The set includes the
companion-zero row of every principal load (4.1.3.2.(3)), 1.25D and 0.9D in
cases 2 to 4, 1.5H in cases 2 to 4 only, and 1.0D + 1.0E in case 5 (no
0.9D + E row); the ordinary conditions (one L, one S, no parts) give 21 formulas
and 33 directional cases. The **legacy 14-row reference set** (23 cases) is
labelled by its content and is not an NBC 2025 set; **custom factors** are
entered by the user (magnitudes only). Crane loads are not generated: enter
crane cases as direct factored cases. W/E vectors are reversed in full,
including axial load; fixed parts never reverse. A non-zero contribution that
no enabled row applies makes every generated case invalid.

Case 5 also evaluates every applicable omission of the separately declared
partition allowance and non-resisting soil. These appended siblings use
`_DP0`, `_DS0` and `_DP0_DS0` IDs; permanent D stays at 1.0 and existing IDs
keep their order. If D_soil is entered, declare whether it is saturated before
NBC ULS generation: case 1 uses 1.5 for saturated soil and 1.4 for explicitly
nonsaturated soil. The declaration starts unresolved; custom/legacy and SLS
factors do not acquire this prerequisite.

Generated self-weight is either added once to D or declared already included.
Direct Pf is never increased automatically. Each case's Pd = 0.9 D_cnt +
min(0, N_lat - N_cb) + N_cb, where D_cnt is the permanent dead load plus the
self-weight above the section (no partition allowance, soil only where it
resists), N_lat the case's signed W/E axial load and N_cb its declared
coupling-beam part, including fixed E axial force; live and snow loads never enter Pd. Direct rows without
N_lat leave sliding and the Pd-dependent shear routes INCOMPLETE. Case-specific
overrides and linked OOP results must be reviewed again when their wall or
source actions change.

The **seismic declaration** (SFRS or not, SFRS wall type, shear-wall role,
earthquake analysis basis) has no default. While it is missing, for an SFRS
wall, and for every case with an earthquake factor (also after copying such a
case to direct mode), a banner names S304-14 Clause 16 / S304:24 Clause 17:
these provisions are not implemented, so the cases are a coverage gap and never
read "CHECKS CLEAR". The NBC 4.1.8.1 simplified-procedure exemption is not
offered in the UI because its eligibility test needs declared hazard values.

The results identify separate governors for axial compression/tension, P–M,
diagonal shear, sliding, and applicable stack-joint shear. Invalid/incomplete
cases remain visible. The success message appears only when every case passes
and no numeric code limit outside the resistance checks is blocked; otherwise a
warning lists the blocked limits. An IP resistance pass is separate from
outstanding detailing, connections and OOP checks, and is not a complete CSA
compliance verdict.

Sidebar inputs keep entered values when other inputs change: the developed
bar count (blank = all bars) is never raised and is reduced to the bar count
with a notice; the height above the checked section is kept when hw changes,
follows hw only while it equals hw (base section), and is reduced to hw with a
notice; the face-shell and clear-cell reference values follow the unit
thickness only until edited. Declarations the engine needs (bond-beam end
anchorage, bars per bond beam and their clear spacing, bond-beam courses above
the section, joint-reinforcement f'gr in Type N mortar, distributed flexural
reinforcement, grout type, lap length, S304:24 minimum face shell) start blank:
blank means not declared and gives no credit.

Bond-beam course elevations are bottoms of courses measured above the checked
IP section. Duplicate or overlapping courses are rejected; touching courses
are allowed and valid courses are clipped at the section and wall top.
`wall_self_weight` retains the above-section total, including course-count
inputs. `wall_weight_between` integrates uniform weight and exact course
intersections at absolute wall elevations for station adjustments. A count
cannot locate grout at an interior station, and the above-section inventory
cannot establish courses below that section; these cases require additional
declarations or an explicitly calculated station force at the calling interface.

See the [load-case guide](docs/load-combinations.md) for worked examples, input
conventions, second-order analysis, exports, and the Python batch API.

## Validated-draft scope

- Unreinforced hollow, fully grouted hollow, or solid-unit masonry.
- Fully grouted reinforced masonry.
- Partially grouted reinforced masonry using discrete and smeared methods with a lower-result option.
- Strain-compatible P-M interaction diagrams with multiple vertical bars, with direct force-equilibrium solutions at the entered axial load.
- Compression and axial-tension demands for reinforced walls, tied compression reinforcement, and squat-wall effective-depth treatment that cannot increase resistance.
- S304:24 simplified and general in-plane shear methods, 2014 shear, separate stack-pattern vertical joint checks, and bond-beam or joint reinforcement.
- Base or between-course sliding shear, uncracked masonry bond, developed vertical steel, and required dowel area.
- Calculated wall self-weight above the checked section and code-defined Pd with a direct override. Net uplift reduces sliding-plane clamping.
- Vertical/horizontal reinforcement area and spacing, development, anchorage, lap, termination, tie, wire-size, and masonry-clearance checks.
- Wall-to-foundation and wall-to-floor/roof confirmation gates.
- Required wall-level interface to the existing OOP tool for concurrent minor-axis utilization and weak-axis stability, with a separate utilization for each edition.
- Free-standing-wall tensile limits, prior cracking of unreinforced walls, and explicit tie and Class A splice eligibility.

The current release intentionally excludes flanged walls, openings, coupled walls, infill-frame action, building-level load distribution and torsion, and seismic system classification/capacity design.

## Run locally

```powershell
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

The app needs Streamlit 1.61.1 or later (pinned with pandas 2.3.3 and altair
6.2.2 in `requirements.txt`); below that it stops with a message. The UI tests
are skipped below 1.61.1.

## Verify

```powershell
python -m unittest discover -s tests -v
python verify_in_plane.py
python verify_independent.py
python -m py_compile in_plane_engine.py load_combinations.py batch_reporting.py batch_ui.py streamlit_app.py
```

The 40-case verification matrix covers prescribed cantilever and fixed-fixed section actions, squat and tall walls, compression and uplift, supported grouting conditions, both sliding planes, stack pattern, bond-beam and joint reinforcement, and deliberate detailing/connection failures. A separate solver checks 1,056 material, geometry, loading, flexural, and shear combinations independently of the production helpers. The regression suite includes real Streamlit input transitions and chart checks.

The app checks the entered wall-section actions; it does not solve a building or derive member actions from support conditions. Enter concurrent factored actions, including applicable second-order effects. Generated actions assume linear superposition; copy them to direct mode when final combination-specific analysis is needed. OOP stability, connection force transfer, and detailed reinforcement layout remain explicit designer inputs. Sampled P-M CSV points describe plotted curves; the design check uses direct equilibrium. For the partial-grout lower option, the governing method is evaluated at each case's Pf; both discrete and smeared curves are identified on the chart.

The engine uses `0.33` for the unreinforced cracked-section eccentricity limit; reference examples that substitute `1/3` differ slightly. The audit uses the supplied 2014 and April 2024 standards. The June 2025 errata were explicitly deferred by the project owner; this release does not claim to include them. The unreinforced shear route implements the printed April 2024 Clause 8.10.2.3 by default; `WallInput.apply_proposed_8_10_2_3_correction=True` restores the square root of f'm as a proposed correction that has not been verified against the corrected text, and the shear method label states which form was used. See [the technical audit](docs/validation-2026-09-11.md) for the basis, corrections, checks, and remaining model limits.

## Deployment

Live app: https://in-plane-wall-bowen.streamlit.app/

Streamlit Community Cloud deploys `main` with `streamlit_app.py` as the entry point.
