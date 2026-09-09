# ThermoFlow architecture

## Boundary map

```text
Client creates/selects project and uploads STL; no solve is triggered
  v
Project + Workpiece API
  | content hash, source coordinates, encoding, quality diagnostics
  v
STL geometry inspector
  | disconnected shells, stable component/region IDs, labelled preview triangles
  v
User scale confirmation
  | um/mm/m -> normalized millimetre solver geometry
  v
Structured draft (OpenAI Structured Outputs, Codex CLI, or explicit offline rules)
  | purpose, materials, source, BCs, mesh, criteria, missing/unsupported items
  v
User review and confirmation
  | component materials and edited fields become the authoritative snapshot
  v
Versioned SimulationSpec
  | project/geometry/material/physics/conditions/mesh/solver/criteria + units
  v
PlanPolicy
  | IDs, units, ranges, capabilities, well-posedness, resource budget
  v
Persistent task queue
  | study reservation, bounded subprocesses, cancellation/timeout/recovery
  v
Deterministic mesh generation and review
  | actual active cells, quality metrics, mesh VTK, warning acknowledgement
  v
Registered deterministic solver
  | no model-provided code or commands
  v
Result contract + real VTK fields + convergence/energy evidence
  | verified complete mesh -> exterior/section geometry and cell fields
  v
Three.js engineering viewport
  | local assets, real-cell picking, field scale/time and full-grid differences
  v
Criterion evaluation
  | criteria + material property-range validity
  | meets_criteria | violates_criteria | indeterminate
  v
Immutable study copy and comparison
  | copied input -> user reconfirmation -> independent mesh/result
  | common temperature scale + metrics; matching grids -> pointwise difference
  v
Optional bounded optimization Agent
  | authorized change -> independent study -> solve -> compare -> stop
```

## Trust boundaries

- Uploaded files are size-limited, extension-gated, content-hashed, and stored
  under generated internal IDs.
- Projects are aggregate roots for geometry versions and research history.
  Existing workpieces without project metadata are migrated into independent
  projects without changing their geometry or study identifiers.
- STL has no physical unit. Source coordinates are retained unchanged until the
  user confirms `um`, `mm`, or `m`; only then is `source_mm.stl` generated.
- Component IDs are derived from shell geometry. Region IDs are derived from the
  uploaded content fingerprint plus the immutable geometric selector. Preview
  triangles carry component IDs, and `SimulationSpec` conditions target region
  IDs instead of user-editable labels. Unit confirmation preserves both ID sets.
- CadFlow owns CAD construction and STL export. ThermoFlow does not import
  CadFlow native objects to solve an STL.
- GPT receives compact geometry and structured state, not unrestricted file,
  mesh, result-field, shell, or solver access.
- GPT output is parsed directly into a strict model. It cannot select executable
  paths, arbitrary plugins, code, or commands.
- The optional Codex harness calls the deployment's existing CLI/configuration/login,
  with schema-constrained final output and the same application-side policy checks.
  Prompts are passed through stdin in an ephemeral isolated working directory;
  CLI shell/MCP/plugins/hooks/browser/multi-agent tools are disabled per invocation.
  Raw stdout/stderr never enter API responses or research history. Output size,
  wall-clock deadline and a shared one-call admission limit bound this local runner.
  Its process group is reaped on timeout or exit. It cannot authorize a study or
  produce solver fields, and failure does not switch to an offline planner.
  Codex itself owns credential access and external provider retention. CLI startup
  may still need write access to its own home even with temporary logs/state and
  ephemeral sessions. The full live workflow has not passed acceptance; see
  `CODEX_HARNESS_SETUP.md` for the current deployment status and deferred checks.
- Material data records source, version, and valid temperature range. Suggested
  values remain visibly unconfirmed. Component assignments keep a catalog ID only
  when the full material record exactly matches that version; edited values become
  user-sourced records and cannot retain database identity. Boundary temperatures
  are checked during policy validation, and solved extrema are checked during
  evaluation. Catalog range violations and user materials without a complete
  validity range prevent `meets_criteria`; explicit criterion violations remain
  `violates_criteria`.
- A normal study remains `needs_input` until confirmation stores a plan snapshot
  fingerprint. The fingerprint covers the complete versioned `SimulationSpec`,
  while `SimulationPlan` remains the current thermal solver projection.
  Their hashes must agree before meshing or solving. STL studies must then persist a mesh bound to the same plan and
  geometry fingerprints. Non-blocking warnings require attributed acceptance;
  blocking failures cannot run. Component count must match the generated mesh's
  connected-region count, and exposed faces must establish one-to-one ownership
  by original STL component IDs before materials can be mapped. The solver accepts only a policy-approved study
with a verified `ready` mesh.
- Planner model metadata may be stored for audit, but hidden reasoning, API
  payloads, tool calls, internal IDs, and solver stdout are not normal UI data.
- An optimization run may change only explicitly authorized variables and has a
  hard round budget. Every candidate is an independently stored study.
- Copying a study carries forward its structured inputs but never its mesh,
  result, confirmation, or input fingerprint. The copy returns to `needs_input`.
- Comparison accepts two to four completed studies from one project. Scalar
  metrics may be compared across geometry versions. A spatial temperature
  difference is candidate minus baseline and is emitted only when geometry,
  solved grid, pitch, and every sampled voxel coordinate match.

## Continuous modeling drafts

`modeling.py` provides revision-checked form saves, conversation proposals and
apply/dismiss/undo transitions. A draft's `draft_revision` covers its input and
pending conversation decisions. Saves preserve incomplete but schema-valid
inputs and run policy checks; they never create a confirmed input fingerprint.
The unit-bearing `SimulationSpec` is rebuilt whenever the draft plan changes.
Repeated equal form saves are no-ops, so they do not invalidate pending proposals.

A per-study modeling lock rejects simultaneous model calls. The execution lock is
held only while reading the baseline and publishing a response, not during the
external model request. Publication rechecks the revision and geometry, component
and region fingerprint; concurrent edits make the late response inapplicable.
This is concurrency control, not a deployment-wide rate limiter. Applying a
proposal repeats schema and policy validation and keeps it unconfirmed, including
when policy errors require further editing. Confirmation refuses pending proposals
and existing confirmed records; source/copy and solve remain separate operations.

The response plan is independently schema-validated. Material records must match
current user-provided data or a catalog entry; changing the claimed source type
cannot fabricate trusted material evidence. Human-readable changes are produced
locally using stable ID-to-name mappings and physical units. Section-level
suggestion provenance and one-step undo survive reloads. Later edits invalidate
the undo snapshot and remove suggestion markers only from changed sections.
Full scalar-level provenance and an arbitrary-depth edit history remain pending.

The existing strict Responses parsing interface now receives `current_draft` and
a bounded conversation window. Hidden reasoning, API request/response envelopes,
model IDs and raw field/geometry arrays are not rendered in the conversation.
The model cannot call solve from this endpoint. Optional history retention keeps
at most 40 messages; the model receives at most 20. With retention disabled, only
the current page session supplies previous raw messages. Structured purpose,
assumptions, suggestions and confirmed inputs persist independently of that
setting; old studies and backups are not retroactively purged.

The workbench serializes auto-saves, blocks navigation on unsaved invalid values
or revision conflicts, and displays recoverable failure state. Late responses
cannot regress the local revision. Material editors survive normal rerenders;
source controls synchronize with the scenario form. Tests cover backend state
transitions, a stubbed structured model client and Node form-state behavior.
The Playwright workflow includes desktop/mobile conversation screenshots, but
local socket creation is currently denied before browser execution, so this is
not verified browser acceptance or live GPT integration.

The socket denial above describes the original restricted-session browser attempt.
Current SSH diagnostics permit local sockets, but do not constitute browser acceptance.
As of 2026-09-07, live Codex verification is paused at the deployment owner's request.
OpenAI planner and optimization calls share a 45-second SDK request timeout and
two retries; these are transport settings, not an end-to-end API deadline.
Provider exceptions are converted to fixed public messages before reaching the
service. Modeling preserves actionable timeout, busy, authentication and rate-limit
messages while preserving saved drafts. Optimization failures retain a failed run
record; unknown tool exceptions are not copied verbatim into that record or logs.

## CadFlow integration

The handoff contract is:

1. CadFlow calls `Shape.export_stl(path, binary=True)` after modelling.
2. The client imports the file with `POST /v1/workpieces/files`.
3. ThermoFlow checks encoding, topology, disconnected shells, bounds, quality,
   and creates a compact preview.
4. The user confirms the STL coordinate unit.
5. The planner drafts point/line/surface source, two bounding-plane temperature
   layers, exposed-face convection, materials, mesh, and criteria.
6. The user can replace the default layers with saved surface-region temperatures,
   then edits and confirms the draft.
7. ThermoFlow generates the actual voxel mesh, quality metrics, surface preview,
   and `mesh.vtk`; the user explicitly accepts any non-blocking quality warning.
8. `voxel_stl_v1` runs only when that mesh still matches the confirmed input and
   geometry fingerprints.

STL does not retain original CAD face, feature, material, or assembly semantics.
This milestone uses detected disconnected shells as candidate components, six
global bounding-plane selectors, component surfaces, and immutable user-selected
STL triangle unions. These regions do not recover original CAD face semantics.
STEP and topology-preserving CAD edits remain outside the current geometry contract.

`regions.py` saves named `surface_patch` definitions under a geometry lock, using
the original STL fingerprint and sorted triangle indices for identity. Repeated
selections are idempotent. Duplicate, invalid, and empty selections are rejected.
Unit and component edits share the geometry lock, including legacy capability
migration, preventing lost selections during concurrent writes. Existing study
snapshots are not rewritten by that migration.

`SimulationSpec` 1.2 snapshots contain the selected triangles and component references.
Before meshing/solving, each referenced region must match its confirmed definition;
new unrelated regions do not invalidate earlier studies. Copy, source edits and
optimization retain the full fixed-boundary list rather than assuming two planes.

## Persistent layout

```text
data/
  projects/project-<internal-id>/
    project.json            # display metadata and geometry-version membership
  workpieces/wp-<internal-id>/
    workpiece.json           # includes owning project
    source.stl
    source_mm.stl           # created after unit confirmation
  studies/study-<internal-id>/
    study.json              # SimulationSpec, solver projection, policy, provenance, state
    mesh.json               # actual cells, quality, review, snapshot hashes
    result.json
    artifacts/
      mesh.vtk              # pre-solve active voxel grid and surface labels
      temperature.vtk       # temperature, heat flux, source power
  agent-runs/agent-<internal-id>/
    agent-run.json          # goal, authorization, candidates, outcome
  tasks/
    manager.lock            # one scheduler per data directory
    task-<internal-id>/
      task.json             # operation, input fingerprint, progress, lifecycle
      diagnostic.json       # failure type and sanitized code locations, when present
```

The display layer converts internal IDs to names and dates. Advanced diagnostics
may resolve a short diagnostic reference to internal records, but normal project,
result, and Agent views do not show filesystem paths or UUIDs.
The browser stores only workspace navigation state (selected records, task tab,
and visualization mode); authoritative inputs, task state, and results remain in
the server-side project repository.

## Current solvers

`analytic_box_v1` is a homogeneous isotropic steady-conduction regression oracle.
It solves two opposite fixed-temperature faces with all other faces adiabatic and
matches the exact Fourier-law result.

`voxel_stl_v1` is the current arbitrary-geometry path:

1. verify the persisted mesh against the confirmed plan, geometry, and artifact hashes;
2. load a usable three-dimensional STL and reject an unconfirmed scale;
3. deterministically reproduce the reviewed mesh by filling a watertight surface
   or reconstructing an explicitly approximate domain from
   open surface voxels and directional slices;
4. label disconnected voxel regions and bind them to original STL surface component
   IDs; assign conductivity, density and specific heat using that same ownership,
   not a centroid ordering that quantization may change;
   map fixed-temperature regions using exact nearest triangles at exposed voxel
   face centers, resolving geometric edge ties by outward-normal alignment and
   then original triangle index; reject empty mappings and conflicting shared cells;
5. map a requested source anchor to a surface or depth-selected embedded voxel;
6. select source cells around a point, line segment, or rectangular patch;
7. assemble a six-neighbour finite-volume conduction system, using harmonic
   interface conductivity, prescribed-temperature cells, and exposed-face convection;
8. solve temperature and calculate source, fixed-boundary, and convection energy
   balance;
9. calculate internal cell flux `q = -k grad(T)` using central or one-sided finite
   differences, then average internal and contact-normal face fluxes at contact cells;
10. write a VTK unstructured voxel grid with temperature, heat-flux vector,
   heat-flux magnitude, and heat-source power;
11. reject the result if its grid differs from the reviewed preview;
12. evaluate explicit engineering criteria and material property-range validity;
    without sufficient criteria or a valid material range, return `indeterminate`
    rather than a fitness claim.

The same regional mapping routine is used for mesh review and numerical assembly.
Mesh and result records retain boundary identities, temperatures and mapped cell
counts. Energy balance sums conduction between all prescribed and free cells, not
only two axis-end layers. The workbench highlights actual original triangles,
shows region names and mapping counts, and does not draw a fictitious axis-plane
marker for custom regions. Prescribed values still act at voxel cell centers;
this approximation and mesh-convergence requirements are explicit review warnings.

`RegionFaceMap` resolves stable regions onto exposed voxel faces, while
`ThermalSurfaceTerms` constructs face-resolved heat flux, film conductance, radiation
coefficient and surrounding-temperature terms. The mesher and solver use the same
selection and overlap checks. Regional convection replaces the global term only on
its selected faces; convection and radiation can coexist. Explicit exchange on a
prescribed cell is rejected because this mesh cannot resolve separate values there.
Every connected steady-state solid must have a fixed or exchange-temperature anchor.
Transient capacity makes an insulated system solvable without an artificial sink.

Solver revision 1.5 uses `G = k_harmonic A / d` for neighboring voxel cells, with
`A = pitch^2` and `d = pitch`. Global and regional convection use the same actual
exposed face area. No whole-assembly volume factor rescales conduction or exchange.
Only transient thermal capacity is corrected: each watertight component gets total
`rho * cp * abs(STL volume)` in the confirmed units, distributed over its own cells.
An open component retains its reconstructed voxel volume and the existing approximation
warnings. Thus changing an unconnected object's size cannot change another object's
material, thermal mass or conductance. Input-specification versions are unchanged;
new result records identify solver revision 1.5. Old stored results remain immutable
and need a copied, reconfirmed and recomputed study to incorporate numerical changes.

The unknown-cell equation is `C dT/dt + A T + r T^4 = b`, with surface terms
aggregated at their owning cells. `radiation.py` solves the nonlinear equation with
damped Newton iterations, a sparse Jacobian and a positive-temperature line search.
`transient.py` applies the same solve to backward-Euler storage terms. Failed
nonlinear residual/positivity checks prevent result publication. Signed boundary
powers and storage are saved at every solved time; nonlinear residual histories
remain diagnostic data, not the normal workbench interface.

The signed conservation residual remains in watts. Its relative normalization uses
`energy_balance_reference_power = max(sum(abs(b)) + sum(abs(A @ T)) + sum(r * T^4), 1e-30)`.
For transient steps, `A` and `b` here include backward-Euler storage terms. Saving
this reference per solved frame makes the metric reproducible without dividing
by a near-zero net power at equilibrium. It measures algebraic balance, not mesh
or time-discretization accuracy; the initial frame has no integration reference.

`SimulationSpec` 1.3 projects all surface quantities with explicit units, including
inward heat flux, radiation temperature, emissivity and its source. Disabled source
and convection settings are retained in the executable plan so editing/copying can
reenable existing values, but disabled terms are absent from the physical conditions
snapshot and numerical assembly. Older plan fingerprints omit new empty/default
fields. A missing temperature-rise/power metric is nullable through results and
study comparisons rather than being represented by a fabricated zero.

`SimulationSpec` 1.4 adds typed thermal contacts with stable source/target component and
region references, area-specific resistance, optional effective area and optional maximum
gap. `thermal_contacts.py` maps opposing exposed voxel faces using mutual nearest neighbors,
normal/separation alignment and the confirmed gap limit. Each accepted pair becomes one
symmetric finite-volume matrix edge; contact-connected solids share the steady-state anchor
check. Contact faces are excluded from environmental exchange, contact transfer is reported
separately, and empty contact fields are omitted from older fingerprints. Failure to obtain
a reliable pair blocks meshing. This contract does not claim conforming interface elements,
mechanical contact, or recovered assembly topology.

The STL solver supports steady conduction and transient backward-Euler integration.
Transient heat capacity is component-specific, the final step ends at the confirmed
duration, and every time step saves full numerical fields, extrema, convergence and
energy-storage balance. Criteria and material validity cover all computed times.
Time-varying loads, surface-to-surface radiation, nonlinear/mechanical contact, thermo-mechanical response, plasticity, creep, fatigue, CFD,
combustion, and conjugate heat transfer remain outside its capability contract.
A future solver can implement the same versioned study/result boundary without
granting GPT direct numerical authority.

## Computation lifecycle

Task submission verifies confirmation and its fingerprint, then reserves the study.
Confirmation, mesh review, and execution serialize on the same study lock. Ordinary
workbench actions submit `mesh`, `solve`, or `apply_and_solve`; each task is persisted
before scheduling and uses a fresh spawned process. Cancellation and timeout can stop
native code. A Linux parent-death signal prevents a server crash leaving an unowned
numerical child. The queue is bounded, but memory quotas require deployment controls.

Publishing serializes against cancellation using the task lock. Mesh/result manifests
are written before the study publication state. Readers require the completed study
state and downloads verify published artifact hashes. Recovery checks committed output
before choosing success, interruption or retryable failure. A worker cannot publish a
terminal task outcome until the supervisor has cleared the study reservation.

A combined task proceeds from mesh to solve only when mesh quality passes; warnings
and blocking failures yield `needs_review`. Cancellation before queued meshing starts
preserves an existing reviewed mesh. An incomplete submission without a matching study
reservation is interrupted, never executed. Server restarts retain queued work and
recover interrupted running studies without changing confirmed inputs. Optional
optimization and legacy synchronous APIs remain outside this task lifecycle.

## Engineering viewport

`visualization.py` is a read-only boundary between stored geometry/results and the
browser. It exposes complete STL faces with original stable component IDs, checked
VTK exterior faces, axis-aligned sections, full-grid differences and real-cell probes.
Mesh/result reads require published study state and a matching artifact fingerprint.
Shared faces are excluded by connectivity, not by screen-space sampling. Sections
preserve finite-volume cell values; the nodal reference solver uses trilinear
interpolation. Probe coordinates identify cell centers explicitly.
For multipart voxels, result/mesh surfaces and probes reconstruct the regular grid
from the verified VTK and reuse the solver's original-surface component ownership.
This avoids displaying a correct field under a different component after voxel
coordinates collapse a geometric centroid-order distinction.

`vtk_io.py` reuses the pinned meshio 5.3 legacy parser with a private, cloned function
namespace to translate VTK_VOXEL connectivity into hexahedron ordering. No parser
globals or persisted artifacts are changed. This keeps existing studies readable.

The workbench delegates its primary canvas to `viewport.mjs` and locally bundled
Three.js r185. BufferGeometry carries physical field colors and triangle-to-cell
maps for raycasting. OrbitControls implements camera navigation. Rendering waits
for each requested numerical frame; failed reads clear the old field and stop time
playback. The three-entry display cache is keyed by study/frame/section plus geometry
unit or mesh generation revision. Full-browser visual acceptance and large-mesh LOD
remain outstanding; pure geometry/raycast tests do not prove WebGL presentation.
