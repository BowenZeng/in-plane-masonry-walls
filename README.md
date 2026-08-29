# In-plane masonry wall design

Independent Streamlit design aid comparing CSA S304-14 and CSA S304:24 for rectangular concrete-masonry walls under in-plane axial load, strong-axis moment, diagonal shear, and sliding shear.

## Validated-draft scope

- Unreinforced hollow, fully grouted hollow, or solid-unit masonry.
- Fully grouted reinforced masonry.
- Partially grouted reinforced masonry using discrete and smeared methods with a lower-result option.
- Strain-compatible P-M interaction diagrams with multiple vertical bars.
- Compression and axial-tension demands for reinforced walls, tied compression reinforcement, and squat-wall effective-depth treatment that cannot increase resistance.
- S304:24 simplified and general in-plane shear methods, 2014 shear, stack-pattern limits, bond-beam or joint reinforcement, and transparent beta/theta components.
- Base or between-course sliding shear, uncracked masonry bond, developed vertical steel, and required dowel area.
- Calculated wall self-weight and code-defined Pd with a direct override.
- Vertical/horizontal reinforcement area and spacing, development, anchorage, lap, termination, tie, wire-size, and masonry-clearance checks.
- Wall-to-foundation and wall-to-floor/roof confirmation gates.
- Required wall-level interface to the existing OOP tool for concurrent minor-axis utilization and weak-axis stability.

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
python -m py_compile in_plane_engine.py streamlit_app.py
```

The 40-case verification matrix covers cantilever and fixed-fixed demand patterns, squat and tall walls, compression and uplift, every supported grouting condition, both sliding planes, stack pattern, bond-beam and joint reinforcement, and deliberate detailing/connection failures. The unit tests reproduce the supplied fully grouted, partially grouted, squat-wall, unreinforced, self-weight, and in-plane-shear examples within calculation-rounding tolerance. The engine uses the code value `0.33` for the unreinforced eccentricity limit; hand examples that substitute `1/3` will differ slightly.

## Deployment

Streamlit Community Cloud can deploy this directory with `streamlit_app.py` as the entry point. Publish only after the calculation scope and repository target have been reviewed.
