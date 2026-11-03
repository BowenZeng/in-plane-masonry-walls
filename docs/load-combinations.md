# Multiple-case in-plane resistance checks

The batch layer evaluates the same wall section under multiple concurrent action
sets using the existing CSA S304-14 and S304:24 resistance engine. It does not
perform building analysis or calculate second-order actions. CSA S304:24 Clause
5.2.2 refers ultimate combinations to the NBC. Two load sets come from
`nbc_load_sets.py`:

- **NBC 2025 Table 4.1.3.2.-A** (`NBC2025_COMBINATIONS(conditions)`): the 21
  formulas / 33 cases of one L and one S, with the companion-zero rows of
  4.1.3.2.(3), both 1.25D and 0.9D, 1.5H in cases 2 to 4, the storage increase of
  4.1.3.2.(7), Note (6) / 4.1.5.5 for a separate roof live load (22 / 35) or a
  parking deck (deck snow 0.2 in case 2, no deck L in the S-principal rows), and
  the declared dead-load parts. The rows need the declared `LoadConditions`
  (importance category, occupancy, roof live load, deck, parts). Crane loads are
  excluded; there is no 0.9D + E row.
- **Legacy 14-row reference set** (`REFERENCE_COMBINATIONS`): the earlier rows,
  labelled by content (ten equal S304-14 4.2.2.2 rows; R06 / R07 its case 3 with
  the Note (4) companion 1.0L; R02 / R03 match no supplied table). It is not an
  NBC 2025 set.

## Inputs and signs

One batch represents one wall definition, checked section, and sliding plane.
Geometry, reinforcement, development, connections, and the assessed stack-joint
crossing area are shared. Identify the section and analysis/load basis above the
input table. Use another batch/session for another section or wall.

P and V are whole-wall kN; M is whole-wall kNm. Compression P is positive, tension
negative. M and V are signed in a consistent analysis coordinate system. They
must be concurrent at the same section; do not combine unrelated envelope maxima.
The existing rectangular wall and reinforcement layouts are symmetric. The batch
layer takes |M| and |V| only after signed summation, while retaining signed actions
in the report. Unsupported asymmetric walls remain outside the engine scope.

Case IDs identify supplemental inputs and remain stable when rows are reordered.
Give every enabled direct case/custom formula a nonempty, unique ID. Renaming an
ID creates a different association. Name is a descriptive label; numeric factors
in the table determine the formula. Blank required cells are invalid, not zero.
Disabled rows are excluded from evaluation; they do not suppress valid rows.

Changes inside an input table take effect when its **Apply** button is pressed.
Every enabled case then runs automatically for both editions. Switching the
inspection selector does not filter the evaluated batch or its exports.

## Generated actions: worked example

Select **Generate from unfactored actions**. With the default fully grouted wall,
the calculated wall weight above the base is 102.980475 kN. Choose **Add calculated
self-weight** and enter:

| Load | P (kN) | M (kNm) | V (kN) |
| --- | ---: | ---: | ---: |
| D, excluding wall weight | 150 | 0 | 0 |
| L | 60 | 0 | 0 |
| S | 0 | 0 | 0 |
| W | -30 | 400 | 80 |
| E | 0 | 0 | 0 |
| Other | 0 | 0 | 0 |

For legacy formula R10, `1.25D + 0.5L + 1.4W`, the two cases are (Pd_case =
0.9 x 252.980475 + min(0, the case's signed factored W axial load)):

| Case | Pf (kN) | signed Mf (kNm) | signed Vf (kN) | N_lat (kN) | Pd (kN) |
| --- | ---: | ---: | ---: | ---: | ---: |
| R10[W+] | 304.225594 | 560 | 112 | -42 | 185.682428 |
| R10[W-] | 388.225594 | -560 | -112 | 42 | 227.682428 |

Each reversal applies to the entire wind vector, including P. The D and L
contributions retain their signs. The same rule applies to E, including its axial
component. A custom formula containing W and E produces all four sign pairings.
The 14 reference formulas produce 23 cases, including separate IDs when zero
contributions make some totals identical. No resistance is reused from a selected
combination: flexure, demand-dependent shear, and sliding are checked per case.

**Pd per case.** Pd_case = 0.9 D_cnt + min(0, N_lat - N_cb) + N_cb (`case_pd`):
D_cnt is the counteracting dead load (D plus the added self-weight, plus soil only
where it resists, never the partition allowance), N_lat the case's signed
factored W and E axial loads including their non-reversible parts, and N_cb the
part declared coupling-beam axial load (`coupling_beam_axial`), the only part that
may add compression. The factor is 0.9 in every combination, including NBC load
case 5 (register I-1). Live and snow loads never enter Pd. The sliding clamping
force is max(0, Pd_case + phi_s As,dev fy kdev), never Pf.

If D already contains the wall weight, enter total D = 252.980475 kN and choose
**Already included in D**; this produces the same actions and Pd. Automatically
added weight is concentric, using the sidebar height above the checked section.
Enter any eccentric dead-load moment in D's M component. In generated mode, the
sidebar's direct-case D inputs do not supply a second dead-load contribution.

Only use linear superposition where it is appropriate. If final actions require
combination-specific second-order/nonlinear analysis, use **Copy generated cases
to direct mode**, then replace the concurrent P/M/V with that analysis's totals.
Copy replaces the direct table, retains generation provenance, D_cnt and the
signed lateral axial loads (so each copied case keeps its Pd), and does not
reapply factors or self-weight. Existing supplemental values copied
with the cases require review against the direct-case inputs.

## Direct actions: worked example

The initial direct row retains the previous defaults: P = 200 kN, M = 1000 kNm,
V = 200 kN. Add rows from the factored analysis; for example, `WIND-REVERSE` with
P = -100 kN, M = -250 kNm, and V = -60 kN. These are final actions; the program
does not automatically reverse, refactor, or add weight to them. The solver uses
P = -100, |M| = 250, |V| = 60, retaining the original signs in exports.

Each direct case declares its **signed lateral axial load** (the factored W/E
axial force of that case, uplift negative). Without it the case's sliding check and
the Pd-dependent shear routes are INCOMPLETE: the engine has no Pd fallback
(register I-2). **Counteracting dead load D_cnt** may be entered per row; blank uses
the sidebar's counteracting dead load and its self-weight setting. An optional
**coupling-beam axial** part may add compression to Pd. A Pd from an external
analysis can be entered instead in the selected case's supplemental form as a
signed value with its source. A negative D_cnt is refused.

## Case-specific inputs and OOP

Use **Case-specific inputs and linked OOP results** for the selected case. It stores
a signed, sourced Pd override, uncracked-area fraction, optional vertical joint
demand override, and separate OOP utilization/stability/reference for each edition.

Every case, including the initial direct case, starts with zero uncracked-bond
credit (F-045). Credit an uncracked fraction only when established for that case.
Base/support sliding does not use this fraction.

For stack-pattern walls, supply the shared assessed head-joint crossing area and
the concurrent **Joint V**. Generated mode combines signed joint contributions
with the same factors/directions as P/M/V. Enter zero explicitly where a load has
no joint contribution; blank means unknown. A case-specific override can supply
an externally calculated final joint demand. The engine checks its magnitude.

**Save and review case inputs** associates the supplemental values with a
fingerprint of the wall, section, load basis, actions, factors and source inputs.
Changes to these inputs mark existing supplemental results stale. Reordering or
changing the inspected case does not. A stale resistance override/active bond
credit makes that case incomplete until reviewed. Stale OOP values are excluded
from the combined screen while valid IP checks remain available. OOP results
must correspond to the same section and concurrent actions, including the same
axial force. Missing OOP results never receive assumed passing values.

## Results and exports

- **PASS**: the implemented IP resistance checks pass for that case and edition.
- **FAIL**: a known demand exceeds resistance, including axial overload or positive
  demand with zero resistance. Undefined P–M utilization remains blank, not zero.
- **INCOMPLETE**: a required demand/resistance input or declaration is missing or
  stale (for example the case's signed lateral axial load).
- **INVALID**: malformed, unsupported, nonfinite, or conflicting inputs prevented
  evaluation. Other valid cases still run.

Additional checks report outstanding detailing, connections, OOP, the seismic
gate (`ip.seismic_provisions_not_evaluated`, every SFRS wall and earthquake case)
and other engine checks separately; their statuses come from
`batch_reporting.check_status` (INFO, PASS, INCOMPLETE, UNSUPPORTED, BLOCKED). “All entered IP resistance cases pass” requires every enabled
case and edition to pass the implemented resistance checks; it is not a wall-level
CSA compliance certificate. The existing additive IP/OOP utilization remains a
separately labelled rational interaction screen.

The governing table identifies maximum utilization and all tied case IDs for each
check and edition. Undefined or unevaluated cases appear in the unranked column
instead of disappearing. The plot includes all finite entered demand points,
including overload and uplift. For partially grouted walls, Discrete and Smeared
curves have explicit names; the lower-resistance method is evaluated individually
at each Pf. A curve selected at one Pf is not presented as a global lower envelope.

Five CSV exports cover all enabled cases: results, governors, checks, calculation
traces, and P–M points. Case reports include edition, section, basis, signed
actions, factors, direction, self-weight treatment, source provenance and input
fingerprint. Traces include each exact WallInput used by the engine. P–M CSV
points describe sampled curves, while design resistance uses direct equilibrium.
Data are retained within the Streamlit session, not saved as a persistent project;
download the reports before ending the session.

## Python API

```python
from in_plane_engine import WallInput, wall_self_weight
from load_combinations import Action, NBC2025_COMBINATIONS, generate_load_cases, check_load_cases
from nbc_load_sets import LoadConditions

wall = WallInput(least_clear_cell_dimension=100.0)
conditions = LoadConditions(importance_category="Normal", uls_specified=True, occupancy="ordinary",
                            roof_live_load="included_in_L", parking_deck="none")
cases = generate_load_cases(
    {"D": Action(150, 0, 0), "L": Action(60, 0, 0), "S": Action(), "W": Action(-30, 400, 80),
     "E": Action(), "H": Action()},
    NBC2025_COMBINATIONS(conditions),
    self_weight=wall_self_weight(wall),
    conditions=conditions,
    section="Base",
    load_basis="Project analysis; NBC 2025 Table 4.1.3.2.-A",
)
batch = check_load_cases(wall, cases)
for row in batch.governing:
    print(row.edition, row.check, row.case_ids, row.utilization)
```

Direct callers construct `LoadCase` objects. Optional `CaseSupplement` values are
keyed by case ID and reviewed with `review_supplement(wall, case, supplement)`;
`LinkedOOP` values are keyed by edition. The batch API replaces case-dependent
fields on the shared WallInput: P/M/V, the case Pd and its basis, the earthquake
flag, uncracked fraction, joint demand, and OOP fields belong to the
case/supplement. `calculate_wall(WallInput)`
remains available with unchanged numerical behavior. Added engine metadata
identifies check categories and selected/alternate curve methods.

`generate_load_cases` refuses a non-zero contribution (including a NaN or a
`stack_v`-only action) that no enabled row of the supplied set applies: every
row is then returned as an invalid `LoadCase` whose error names the omitted
contribution (for example "The Other contribution is non-zero but no enabled
row of the custom set applies it."), so `check_load_cases` reports INVALID and
`all_ip_pass` is False. The optional `set_name` names the set in that message;
by default it is inferred from the row tags ("NBC 2025", "legacy 14-row
reference" or "custom"), the wording of the native UI.
