# In-plane masonry wall design

Independent Streamlit design aid comparing CSA S304-14 and CSA S304:24 for rectangular concrete-masonry walls under in-plane axial load, strong-axis moment, diagonal shear, and sliding shear.

## Multiple load combinations

Choose **Enter factored cases** for externally calculated concurrent P/M/V, or
**Generate from unfactored actions** for signed D/L/S/W/E/Other contributions.
Apply the input table to evaluate every enabled case for both editions. Selecting
a case only changes the detailed checks and highlighted chart point.

The generator includes the OOP project's 14 editable reference formulas and
automatically reverses complete W/E vectors, including axial load. Each factor
applies to P, M, V and any supplied vertical head-joint demand. The 14 formulas
produce 23 directional cases. They are a project reference set, not a complete
NBCC specification; record and confirm the applicable project load basis.

Generated self-weight is either added once to D or declared already included.
Direct Pf is never increased automatically. Pd has its own dead-load basis,
independent of the D factor in a combination. Case-specific overrides and linked
OOP results must be reviewed again when their wall or source actions change.

The results identify separate governors for axial compression/tension, P–M,
diagonal shear, sliding, and applicable stack-joint shear. Invalid/incomplete
cases remain visible. An IP resistance pass is separate from outstanding
detailing, connections and OOP checks, and is not a complete CSA compliance verdict.

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
