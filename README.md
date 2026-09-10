# ThermoFlow

ThermoFlow is an engineering simulation workbench connected to CadFlow through
STL. A project owns one or more geometry versions and their complete study history.
Importing an STL only creates and inspects a workpiece inside a project. The user then confirms
the STL unit and real dimensions, describes the operating condition, reviews the
structured draft, assigns a material to every detected component, and explicitly
confirms the inputs before a normal study can run.

The planner converts a user description into a strict `SimulationPlan` and
identifies missing or unsupported information. It does not produce numerical
fields. User-confirmed structured data is the solver input, and deterministic
policy validates that input again before execution.

Before execution, the platform reconstructs and persists the actual voxel mesh,
its quality metrics, a surface-cell preview, and a standalone VTK mesh artifact.
Warnings require an explicit, attributed review; blocking quality failures cannot
be overridden. A mismatch between detected STL components and connected voxel
regions is blocking because component materials could otherwise be misassigned.
The finite-volume solver then uses the same deterministic mesh,
applies enabled local power sources, fixed-temperature layers or saved surface regions,
surface heat flux, convection, diffuse-gray environmental radiation, and explicitly confirmed
thermal contact resistance between component surfaces. It solves the sparse temperature system, checks energy
balance, reconstructs cell heat flux from internal conduction and contact faces, and writes real VTK
cell fields for temperature, heat flux, heat-flux magnitude, and source power.

## Trust model

- GPT returns a Pydantic-validated data structure. It cannot choose shell
  commands, executable paths, source code, output paths, or unregistered solvers.
- Material suggestions remain unconfirmed until the user selects catalog data or
  explicitly accepts entered properties.
- Component assignments retain a stable material-catalog ID only while all
  properties exactly match that versioned record. Editing conductivity, density,
  or heat capacity creates an attributed user override and clears the catalog ID.
- Component IDs, units, ranges, required fields, supported physics, and mesh
  resource limits are checked by server-side policy.
- Every study stores a versioned `SimulationSpec` shared by the Agent, structured
  UI, mesher, solver boundary, and reports. Physical values in this contract carry
  explicit units. Every confirmed study also stores an input fingerprint, planner
  provenance, a mesh fingerprint, and immutable mesh/result artifacts.
- A completed solve is not automatically a successful engineering assessment.
  Criteria are evaluated separately; missing criteria produce `indeterminate`.
  A result outside a catalog material's stated property range also remains
  `indeterminate`, even when its numerical acceptance criteria pass. User-sourced
  material properties need an explicit valid temperature range before the
  platform can return `meets_criteria`.

## Confirmed API workflow

```text
POST /v1/projects                create an engineering project
GET  /v1/projects                list projects
PATCH /v1/projects/{id}          rename project metadata
GET  /v1/projects/{id}/workspace geometry versions and study history
POST /v1/workpieces/files       multipart CAD file; imports and inspects only
POST /v1/workpieces/{id}/unit   { unit: um | mm | m }
PATCH /v1/workpieces/{id}/components/{component_id}  rename a stable component
POST /v1/workpieces/{id}/regions  { name, triangle_ids }, immutable original-STL selection
POST /v1/studies                { workpiece_id, purpose, require_confirmation }
PUT  /v1/studies/{id}/draft     { expected_revision, overrides, purpose }, saves only
POST /v1/studies/{id}/modeling/messages  { expected_revision, message }, proposes only
POST /v1/studies/{id}/modeling/decision  { expected_revision, action: apply | dismiss | undo }
POST /v1/studies/{id}/confirm   reviewed overrides and confirmer
GET  /v1/studies/{id}/spec      versioned, unit-bearing SimulationSpec
POST /v1/studies/{id}/tasks     { operation: mesh | solve | apply_and_solve }, returns 202
GET  /v1/tasks                 restore tasks by study_id or project_id
GET  /v1/tasks/{id}             stage, progress, elapsed timestamps, outcome
POST /v1/tasks/{id}/cancel      stop queued or running computation
POST /v1/studies/{id}/mesh/confirm  explicitly accept non-blocking warnings
GET  /v1/studies/{id}/mesh
GET  /v1/studies/{id}
GET  /v1/studies/{id}/result
GET  /v1/studies/{id}/frames/{index}  verified temperature/heat-flux frame at a solved time
GET  /v1/studies/{id}/artifacts/{name}
GET  /v1/workpieces/{id}/view  complete STL triangles, confirmed scale and stable components
POST /v1/studies/{id}/view    verified VTK surface, section or full-grid study difference
GET  /v1/studies/{id}/cells/{cell}/probe  true cell-center values, optional frame_index
POST /v1/studies/{id}/copy     copy confirmed inputs into an unconfirmed study
POST /v1/study-comparisons     compare 2-4 completed studies in one project

POST /v1/studies/{id}/agent-runs  optional bounded optimization loop
GET  /v1/agent-runs               list optimization records
POST /v1/assistant-sessions       unified knowledge Q&A and modeling Agent session
GET  /v1/assistant-sessions/{id}  restore a Q&A/modeling session
POST /v1/assistant-sessions/{id}/turns  submit a question, description or typed answers
POST /v1/assistant-sessions/{id}/launch confirm the summary and submit the solver task
```

The collapsible modeling conversation stays beside the engineering canvas and
reads the saved structured draft. Form edits are serialized and version-checked;
switching studies or sending a message first waits for pending saves. Incomplete
transient timing can be saved and questioned, but cannot pass solve confirmation.
Suggestions display human-readable before/after values with units and remain
separate from the draft until explicitly applied. Application is undoable until
a later edit or conversation; it never confirms input, generates mesh, or solves.
The final confirmation remains a separate action, blocked by pending suggestions.
Already confirmed studies, including legacy records, must be copied before editing.

GPT receives the current plan, compact geometry/region metadata and the latest
20 conversation messages, not STL triangles or result fields. New material values
in a suggestion must match the current draft or a local catalog record; new user
material numbers must first be entered through the material form. The starter
catalog remains demonstration data, not certified production material evidence.
The offline planner supports only its existing explicit-value rules, not general
language understanding. Real GPT network integration and browser acceptance of
this conversation workflow have not yet been verified in this sandbox.

`THERMOFLOW_RETAIN_MODELING_HISTORY=true` retains a rolling window of 40 messages.
With `false`, new conversation text is not stored in study files; the page may
supply its in-memory session history on the next request. Reloading then loses
that conversation, but keeps structured inputs, pending suggestions and input
confirmation. This option is not retroactive deletion of other studies or backups;
text deliberately promoted to structured purpose/assumptions is still input data.
Suggestion provenance currently tracks plan sections, not every nested scalar.
General model-call rate limiting, complete field-level review and production
material provenance remain further implementation work.

`POST /v1/simulations/stl` remains as a compatibility endpoint for automated
tests and trusted integrations. It confirms millimetres and solves synchronously,
so it is not the workbench's normal user flow. The legacy `/mesh` and `/run`
POST endpoints also remain synchronous and are outside the background queue's
concurrency, cancellation and timeout controls; do not expose them to untrusted
clients. Ordinary workbench mesh and solve actions use `/tasks`.

Requested heat-source coordinates are not silently clipped to the STL bounding
box. The solver maps them to the reconstructed domain and returns requested and
resolved coordinates in `result.heat_source_mapping`. For open or thin domains,
the result also states whether the requested embedded depth was achieved.

## Workbench

The main workspace combines a project/geometry/component tree, rotatable STL canvas,
structured property panels, research history, and an optional Agent area. Normal
screens show user-facing state and engineering values, not UUIDs, raw JSON,
solver output, stack traces, API payloads, or model tool calls. Operational data
is available only through the separate advanced diagnostics entry.

The material panel exposes conductivity, density, and heat capacity for every
detected component. Catalog version/citation and valid temperature range remain
visible; user edits are carried into the confirmation snapshot with user-source
provenance instead of being presented as database values.

Material validity is checked both before and after the solve. Boundary
temperatures outside a material record's stated range are raised during plan
review. If the solved temperature field exceeds that range, the engineering
assessment is `indeterminate`; an explicit criterion violation still takes
precedence and is reported as `violates_criteria`.

The left workspace groups studies by project and identifies which geometry
version produced each study. Import can create a new project or add another
geometry version to the selected project. Historical `SimulationSpec` snapshots
retain the project and geometry names that were confirmed at that time. Browser
refresh restores the selected project, geometry, study, task panel, and field
view without copying engineering inputs or result data into browser storage.

Disconnected STL shells receive content-derived stable component IDs. Preview
triangles carry those IDs so component-tree selection, hide, isolate, and canvas
picking refer to the same shell. Geometry faces and component surfaces also have
stable region IDs. Confirmed `SimulationSpec` boundary conditions reference the
region IDs while retaining the solver selector as region metadata, preventing a
display-name change from rebinding a condition.

The canvas can select individual original STL triangles and save their union as an
immutable named region. The scenario panel binds 1-24 fixed temperatures to bounding
planes, component surfaces, or saved selections. Saving a selection does not confirm
or run a study and does not reset unconfirmed form edits. A region ID depends on the
source STL fingerprint and sorted face indices, not display order or the chosen unit.
Selected surfaces use `SimulationSpec` 1.2 snapshots; adding an unrelated region does
not invalidate older studies, but changing a referenced definition blocks computation.

Regional boundary mapping uses exact nearest-triangle queries at exposed voxel face
centers and imposes the temperature at their owning cell centers. Equal-distance
edges use outward-normal alignment and then stable triangle order. This remains a
voxel approximation, not a conforming finite-element surface condition. The mesh
panel reports mapped cell counts and requires review; empty mappings, conflicting
temperatures in shared cells, and a fully prescribed domain block the solve. Small
patches and sharp edges need mesh-convergence checks.

Surface conditions bind to stable region IDs. Positive heat flux is into the solid;
negative flux extracts heat. Regional convection replaces global convection on the
selected exposed faces. Flux, convection and radiation may coexist, but overlapping
conditions of the same kind or exchange on prescribed-temperature cells are blocked.
Radiation requires an explicitly sourced emissivity and a surrounding temperature.
Its model is diffuse-gray exchange with large isothermal surroundings; view factors,
self-occlusion, spectral effects and participating media are not calculated.

Thermal contacts explicitly bind two different detected components and one saved surface
region on each side. The user confirms an area-specific resistance and may constrain the
effective area and maximum gap. The voxel mapper accepts only mutual nearest exposed faces
whose normals oppose and whose separation follows those normals; missing or remote matches
block meshing instead of creating an implicit long-distance connection. Accepted contact
faces are removed from environmental exchange, symmetric matrix links preserve energy, and
the mapping records geometric area, effective area, conductance and maximum face gap. This
is a face-resolved voxel approximation for disconnected STL shells, not conforming contact
elements or recovered CAD assembly semantics.

The scenario panel can disable local power and global convection and remove all
fixed temperatures. A steady study needs a temperature anchor (fixed temperature,
convection or radiation) for every disconnected solid. Insulated transient studies
are supported with confirmed initial temperature and duration. No input power means
the temperature-rise/power metric is unavailable, not zero.

The primary canvas now uses bundled Three.js r185 and OrbitControls with no CDN.
It reads complete STL geometry and actual exterior faces of the verified VTK mesh,
not the compact planner preview. Rotation, pan, wheel/touch zoom, standard camera
views, opacity, component hiding/isolation, boundary selection and source dragging
share that scene. Completed studies provide true temperature/heat-flux colors,
sampled numerical vector arrows, temperature bands and axis-aligned sections.
Legends identify quantity, unit, range and solved time; steady results are static.

Sections intersect the real solver cells; finite-volume values remain cell-constant
and nodal reference results use trilinear interpolation. Picking resolves an actual
VTK cell and reads its temperature, flux, component and time through a checksummed
probe endpoint. Hotspot positioning uses extrema of the complete field. Spatial
comparison requires matching complete mesh connectivity, coordinates and time,
and computes candidate minus baseline. Display JSON is not sent to the Agent.

`temperature_field_preview` and `heat_flux_field_preview` remain compact compatibility
summaries, but are not the numerical source for the new canvas. The VTK adapter
uses meshio 5.3 with a scoped connectivity translation for its unsupported VTK_VOXEL
type; it neither changes stored files nor modifies the parser globally. WebGL
startup failures are explicit instead of falling back to synthetic field images.
Real-browser screenshot/pixel acceptance remains unpassed in the current sandbox.
Large-model LOD, fixed probes, general clipping/isosurfaces, exploded views and full
side-by-side result layouts remain implementation work.

A completed study can be copied without copying its mesh or result. The copy
returns to `needs_input`, so changed structured inputs must be reviewed and
confirmed before a new mesh and solve. The result inspector compares two to four
completed studies from the same project with a shared temperature scale and
metric deltas. It produces a spatial temperature-difference field only when both
studies use the same geometry version, solver grid, pitch, and sampled voxel
coordinates; otherwise it clearly limits the comparison to scalar metrics.

After a study completes, editing a heat source creates a new study and preserves
the earlier result. The optional optimization Agent may only vary explicitly
authorized parameters, creates one reproducible study per round, and stops at the
user's round budget. The workbench summarizes changes, metric movement, selected
study, and stop reason without exposing the internal call trace.

## Run locally

Mesh and solve tasks run in isolated child processes with persisted queue records.
The workbench shows stages, progress, elapsed time and cancellation, and reconnects
to running tasks after refresh. Cancel and timeout terminate the numerical process,
including native solver calls. Confirmed inputs remain available for retry; complete
results are never overwritten. Partially published meshes/results cannot be opened
as usable output or downloaded through their artifact endpoints.

`apply_and_solve` generates a new mesh and proceeds only if its quality passes. A
warning or blocking defect ends the task as `needs_review`; the user must inspect
the mesh and explicitly accept non-blocking warnings before submitting `solve`.
Previous studies' warning acceptance is not carried over. A stopped server interrupts
running tasks and retains queued tasks; on restart the queue resumes and interrupted
studies can be retried. This does not resume a numerical iteration mid-solve.

Each data directory permits one API service process. Child-process concurrency is
controlled with `THERMOFLOW_TASK_WORKERS` (default 1, range 1-4), waiting capacity
with `THERMOFLOW_TASK_QUEUE_LIMIT` (default 8, range 0-100), and per-task runtime with
`THERMOFLOW_TASK_TIMEOUT_SECONDS` (default 900, range 1-7200). Queue waiting is excluded;
the combined mesh/solve task shares one deadline. The API can request a shorter,
but never longer, deadline. These are admission/time limits, not a hard memory quota;
production deployments still need operating-system resource isolation. Optional
optimization runs and the legacy synchronous endpoints are not yet managed by this queue.

Use Python 3.10 or newer on Linux. The A800 deployment can use CuPy for the sparse
linear solve while geometry preprocessing and VTK output remain on CPU.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
# Set OPENAI_API_KEY in .env or the process environment when using OpenAI mode.
thermoflow
```

Open `http://127.0.0.1:8000/docs` for the workbench. Developer OpenAPI remains at
`http://127.0.0.1:8000/api/docs`. For an offline smoke test:

```bash
THERMOFLOW_PLANNER=deterministic thermoflow
```

### Codex harness

Set `THERMOFLOW_PLANNER=codex` to use an installed Codex CLI for both structured
modeling and bounded optimization decisions. Python 3.10 uses the conditional
`tomli` dependency for configuration parsing; newer Python uses `tomllib`. No separate OpenAI API key or
OpenAI SDK call is needed in this mode. The CLI reads its deployment-owned
`config.toml` and login directly; ThermoFlow never opens or copies `auth.json`.
The model and provider remain those selected in Codex, not `OPENAI_MODEL`.

```bash
THERMOFLOW_PLANNER=codex \
THERMOFLOW_CODEX_HOME=/data/yihongzhu/_zty/.codex \
thermoflow
```

`THERMOFLOW_CODEX_EXECUTABLE` selects the CLI executable (default `codex`, not a
shell command); `THERMOFLOW_CODEX_HOME` defaults to `CODEX_HOME`, then the service
user's `.codex` directory. `THERMOFLOW_CODEX_TIMEOUT_SECONDS` is 5-600 seconds,
default 120. Run the service as the user who owns that installation. The local
ignored `.env` selects the supplied installation; it contains paths, not credentials.

The harness uses `codex exec --output-schema`, a private temporary working
directory, read-only sandboxing, no approvals, and ephemeral sessions. Shell,
MCP, plugins, hooks, web/browser tools and multi-agent execution are disabled for
these invocations without editing the shared configuration. Geometry summaries,
current draft and bounded conversation go through stdin; only the final JSON
file is parsed and validated. Raw CLI output is discarded. Temporary response,
schema, log and state directories are removed, and timeout kills and reaps the
process group. One request per API process is admitted at a time, including
optimization decisions; this is not a distributed rate limiter or token budget.
CLI flags target the installed Codex 0.153.4; retest when changing versions.

Codex still needs its own working login, configuration access and outbound
connection. Ephemeral mode is not a guarantee of a wholly read-only Codex home:
CLI initialization or credential refresh may require owner-writable files.
Earlier restricted-session probes failed on a read-only filesystem. On 2026-09-07,
SSH checks could create local sockets and a minimal CLI request completed, but a
full STL draft timed out; the complete live workflow remains **unverified**.
The deployment owner confirmed Codex is unavailable and requested a pause on live
integration. See [the current runbook](docs/CODEX_HARNESS_SETUP.md) for offline
checks and the acceptance sequence after recovery. This maintenance batch did not
change authentication files, shared Codex configuration, or planner selection.
External model data retention follows
the selected Codex provider, independently of ThermoFlow's conversation setting.
All suggestions still require application and separate user confirmation before
the deterministic mesh and solver workflow. There is no silent offline fallback.

Ubuntu systemd, CUDA, SSH tunnel, and Docker instructions are in
[docs/deployment-a800.md](docs/deployment-a800.md).

## CadFlow integration

CadFlow hands off geometry with `Shape.export_stl(path, binary=True)`. ThermoFlow
does not need the CadFlow runtime to solve the exported STL. Because STL carries
no unit metadata, the user must choose micrometres, millimetres, or metres before
planning; ThermoFlow then creates normalized millimetre solver geometry.

Watertight meshes use enclosed-volume filling. Open, inconsistent, or
disconnected meshes use an explicitly reported approximate voxel reconstruction.
Degenerate isolated fragments are counted and excluded from component assignment.
For reconstructed meshes, "embedded" means inside that reconstructed domain, not
inside a mathematically watertight solid.

## Current scope

`voxel_stl_v1` is a simplified steady-state and transient conduction backend. It supports
component-specific isotropic conductivity, harmonic interface conductivity, an optional
local point/line/rectangular source, 0-24 fixed-temperature plane or surface regions,
optional global convection and up to 48 region-specific flux/convection/radiation conditions.
It also supports up to 100 explicit thermal-contact definitions between reliably mapped
surface regions of different disconnected components.
`analytic_box_v1` remains a Fourier-law regression oracle.

Transient studies require a confirmed initial temperature, duration and time step.
The implicit backward-Euler integration includes component density and heat capacity,
preserves a complete field at every computed step (including the initial state), and
accounts for thermal storage in energy balance. Time steps are limited to 200 and
the estimated cell-count/time-step product to 5 million. The last step is shortened
to end exactly at the requested duration. Sources and boundaries remain constant
throughout the simulated interval. Time-step refinement is still required to assess
temporal discretization error; unconditional stability does not imply accuracy.

Each frame provides full VTK/NPZ fields, a checksummed display representation,
extrema coordinates, and numerical diagnostics. The workbench can select and play
solved time steps and lock the temperature scale across the interval. Engineering
criteria and material validity are evaluated over all computed times. The transient
temperature-rise/power ratio is not presented as a steady thermal resistance.
Transient input snapshots use `SimulationSpec` 1.1, or 1.2 with saved region definitions.
Regional exchange and disabled source/convection modes use 1.3. Existing 1.0/1.1/1.2
snapshots retain their fingerprints when the new optional fields are absent. Confirmed
component contact conditions use 1.4; empty contact lists are omitted from legacy plan and
specification fingerprints.

Nonlinear radiation uses damped Newton iterations with sparse linear solves and a
checked residual at every accepted time step. Iteration evidence is retained for
diagnostics. Results and individual time frames record signed flux, convection,
radiation, prescribed-boundary conduction, source and storage powers. Regional
exchange and global convection both use actual exposed voxel area. Solver revision
1.5 uses `G = k_harmonic A / d` without a whole-assembly volume correction. Closed
components retain their own physical STL volume in transient thermal capacity;
adding an unrelated body cannot rescale another body's conductance or heat capacity.
Materials, result surfaces and probes share original-STL component ownership instead
of centroid sorting. Contact-face flux is averaged with internal face flux rather
than counted twice. Previously saved fields are not rewritten: copy, reconfirm and
recompute a study to obtain revision 1.5 results. Numerical
surface area and cell-center boundary temperatures remain approximations, so mesh
convergence is required. Aligned nested grids are used in the conduction benchmark;
arbitrary coarse grid sizes can shift voxel extents and need not converge monotonically.

The voxel solver retains the signed conservation residual in watts and its explicit
`energy_balance_reference_power`. The reported relative error divides the absolute
residual by the sum of absolute equation terms (including implicit storage terms
for transient steps), not by net boundary power, which can vanish at equilibrium.
This normalized algebraic balance is not a discretization-error estimate; inspect
the raw power residual and mesh/time-step convergence as well. The initial frame
has no integration residual or reference power.

The current solver does not implement time-varying loads, surface-to-surface radiation,
conforming or nonlinear mechanical contact, thermo-mechanical stress/deformation, plasticity,
creep, fatigue, fluid flow, combustion, or conjugate heat transfer. Requests for
those effects are recorded as unsupported and must not be interpreted as a
complete fitness, life, or stability assessment.

Implementation and verification gaps against the supplied requirements are tracked in
[the implementation status](docs/IMPLEMENTATION_STATUS.md). The entire requirements
document has not yet been implemented or accepted.
The [2026-09-07 thermal verification report](docs/THERMAL_VERIFICATION_20260907.md)
records numerical corrections, regression evidence and remaining limitations.

See [the architecture](docs/architecture.md),
[the platform/license research](docs/research/platform-landscape.md), and
[the agentic simulation research](docs/research/agentic-simulation.md).

The project is MIT licensed. Dependency license notes are research guidance, not
legal advice; product distribution still requires a formal dependency audit.
