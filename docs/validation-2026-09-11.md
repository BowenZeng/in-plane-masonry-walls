# Technical validation - 11 September 2026

Release build: `2026-09-11.01`. Baseline reviewed: `16869a04f1774191c7e9a2f0c17582acd2990da2`.

## Basis and boundary

This audit checks the implemented rectangular wall-section resistance model against the supplied CSA S304-14, April 2024 CSA S304:24, and Chapter 7 design examples. It covers unreinforced hollow, solid and fully grouted walls, reinforced fully grouted walls, and discrete/smeared partially grouted walls. Building analysis, openings, flanges, and seismic system classification are excluded by the agreed scope.

The owner explicitly deferred the June 2025 errata during this audit. The [publisher's revision history](https://store.accuristech.com/products/preview/2902024) lists changes to 8.10.2.3 and 11.10.2.5, among other clauses. The existing April 2024 unreinforced shear equation is retained on that basis; the app is not represented as implementing the revised June 2025 standard.

The source files were read without modification. PDF Inspector classified all three as TextBased. Full extraction identified encoding problems in the design example document (49 flagged page indices). Governing formulas and examples were checked visually against the page renders; garbled native text was not treated as reliable equation evidence. Local extraction records are retained in `tmp/in-plane-audit-20260911` outside this public repository.

| Supplied source | Pages | Detection confidence | SHA-256 |
| --- | ---: | ---: | --- |
| CSA_S304-14_Design of masonry structures.pdf | 175 | 1.0 | 3B7981D7E65440D0DB21B2FE5842D798B5F4B0E1EED193D1DA8734D6D40CD324 |
| CSA S304_24_Design of masonry structures.pdf | 180 | 0.875 | EE59DD15C90916119E3562F9694D9FF93501617EAE8F0A711B2CF01994B82268 |
| IP design.pdf | 102 | 1.0 | 7E9CDB0F5B031F267D4DC08C8034559513F127FBD7D34146A3B6138AF8B54A17 |

## Findings corrected

| Finding | Correction and evidence |
| --- | --- |
| Sample interpolation could give a small positive squat-wall moment resistance where direct equilibrium gives zero near the tensile endpoint. | The entered demand now uses a direct neutral-axis equilibrium solution; samples are for drawing/export only. A reproduced 140 mm, 30 MPa-unit, 3 m-high wall at Pf = -272 kN returned 0.6715 kN-m previously and now returns 0.0. |
| Tied compression steel was credited even when tie checks failed. | Both the axial cap and P-M solver require compliant tie size/spacing and confirmation of hooks, placement and lateral restraint; S304-14 10.2.7/12.2 and S304:24 11.2.7/13.2. |
| Tied curves could reach an artificial cap when sampling stopped before compression steel developed its stress. | Sampling includes the steel strain-transition depths and extends through compression yielding. |
| Bar designations were used as diameters. | Corrected to 11.3, 16.0, 19.5 and 25.2 mm for 10M through 25M; areas remain 100, 200, 300 and 500 mm2. [Canadian rebar dimensions](https://www.cbsa-asfc.gc.ca/sima-lmsi/i-e/rb52024/rb52024-in-eng.html), paragraph 21. |
| Horizontal Av was treated as a transverse bond-splitting index without identifying the splitting plane or number of developed bars. | Ktr is conservatively zero. The development calculation includes geometry bounds and epoxy clear-spacing effects; 12.4.2.3/.5 and 13.4.2.3/.5. |
| A horizontal anchorage checkbox alone enabled shear-steel credit. | Credit additionally requires continuity, sufficient straight-bar development, bar area/fit, or valid wire diameter. Failed details remain visible in Section checks. |
| Class A lap selection had no eligibility check. | Requires explicit confirmation that As provided/required is at least 2 and no more than 50% is spliced within the lap length; 12.5.4.1 and 13.5.4.1/Table 11. |
| Horizontal reinforcing-bar strength was unbounded. | Bars are limited to 400 MPa. Wire remains a separate material route; S304:24 6.5 and S304-14 5.5. |
| 2024 general shear credited full longitudinal steel despite insufficient development. | Effective tensile area is reduced in proportion to available development; 11.10.2.6.3(c). |
| 2024 simplified shear omitted the qualifying close-vertical-spacing option. | Uses the spacing option only when the developed vertical area meets 0.002 t zs; 11.10.2.5(d). |
| A nominally partial layout filled every cell but still used partial-grout shear rules. | The actual grouted length controls gamma and the fully/partially grouted shear route. |
| Squat shear-cap enhancement was unconditional. | Enhancement requires confirmation that load input is distributed over the full wall length; 10.10.2.2 and 8.10.2.2. |
| Stack pattern used one horizontal layer as a cap on horizontal diagonal shear and lacked its special spacing limit. | Vertical joint shear is checked separately using the total developed area crossing the assessed joint and its corresponding vertical demand. Bond-beam/wire spacing limits are 800/400 mm. A crossing-area consistency check is included; 10.10.4/11.10.4 and Chapter 7 p. 402. |
| Sliding clamping remained high under net axial uplift. | Under net tension, steel yield clamping is reduced by the net uplift and cannot be negative. An open tension joint receives no uncracked bond credit. Underdeveloped vertical bars receive no yield credit for sliding; the basis is force equilibrium and 10.10.5/11.10.5. |
| Full wall self-weight was included at any assessed elevation. | Height above the checked section is explicit, including zero at the top joint; Chapter 7 Example 7.4 distinguishes base and top sliding. |
| Free-standing-wall tensile-strength limits and an already-cracked unreinforced state were unavailable. | Added the Table 5 note limit of 0.1 MPa and a cracked-only option; 7.2/8.2. Free-standing walls do not require a fictitious top connection confirmation. |
| An out-of-range axial load plus a linked OOP result could crash while formatting an undefined utilization. | Undefined resistance generates a blocked check. Zero demand at a zero-resistance endpoint is treated consistently. |
| A single OOP utilization was reused for both editions. | Each edition now has its own entered utilization. |
| P-M display clipped the tensile branch and negative demand. | The axial axis includes both tensile capacity and entered uplift. Partial-grout legend states that selection is at the entered Pf. The demand marker is visible on both light and dark themes. |

## Verification completed

The deployment requirements are pinned to the tested versions: Streamlit 1.61.1, pandas 2.3.3 and Altair 6.2.2.

- `python -m unittest discover -s tests -v`: 51 tests passed, including six Streamlit tests; no skips with the app dependencies installed.
- `python verify_in_plane.py`: all 40 prescribed-action and failure cases passed.
- `python verify_independent.py`: 1,056 equation comparisons passed. The separate implementation imports no production material table, section, shear, interpolation or development helper. It independently integrates compression regions, solves force equilibrium by bisection, and checks section area, axial cap, moment resistance, self-weight, Pd, diagonal shear and sliding resistance.
- The independent grid spans both editions; Type S/N mortar; 15/30 MPa units; 140/190/240/290 mm thicknesses; 3/5/7.5 m heights; all six section/method routes; zero, moderate and high compression; reinforced uplift; zero/nonzero moment and shear; and unreinforced/reinforced shear routes. Agreement tolerance is max(1e-7 in output units, 1e-10 relative).
- Reference example anchors remain covered: Example 7.1 full and squat flexure, Example 7.2 discrete/smeared partial grouting, Example 7.4 diagonal/sliding shear and self-weight, and unreinforced eccentricity branches. The literal 0.33 eccentricity limit is retained.
- UI checks verify the requested removals, retained source link/chart/checks/trace/exports, visible tensile domain, wall-type transitions, switching horizontal reinforcement back to None, distinct edition OOP inputs, and axial overload without an exception.

The earlier 40-case matrix named cantilever/fixed-fixed conditions but supplied their section forces directly. It did not validate a structural-analysis solver. That limitation remains explicit: calculate concurrent section actions and secondary effects using the applicable wall analysis before entering them. A user's connection or stability confirmation is not a calculation of that connection or the building's stability.

## Model limits retained

- Uniform symmetric vertical-bar layouts and rectangular concrete masonry only. The section model does not introduce new wall geometries.
- The OOP interface is a conservative interaction screen using designer-supplied results for the same section and actions. Compression-zone stability must be checked in the associated OOP analysis; this app does not generate that analysis.
- Reinforcement layout, development on both sides, laps, ties, connections and uncracked joint area require project-specific inputs. A nominal flexural curve remains visible when a detailing check fails; failed details must be resolved before using that resistance.
- Straight deformed-bar development is calculated. Joint-wire anchorage and Class B continuity are confirmed by the designer. Hooks, mechanical anchors, welded splices and detailed bond-splitting reinforcement are not automatically designed.
- Section actions are entered as magnitudes of moment/shear with compression-positive axial load. Boundary conditions and load distribution are not inferred from those inputs.
- No serviceability drift or building seismic design is claimed. The user-directed errata deferral remains open.

## Release interface

Removed the opening status/metric cards and introductory block, standalone shear-details table, edition-change commentary, and bottom yellow notice. The source-code button, P-M diagram, comparison table, individual section checks, calculation trace and CSV exports remain. Build identification is a small footer caption.
