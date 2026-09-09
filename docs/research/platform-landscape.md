# Simulation platform and license research

Research date: 2026-09-04. This is an engineering dependency screen, not legal
advice. Before distributing a commercial product, regenerate an SBOM from the
resolved lockfile and have counsel review weak/strong-copyleft components.

## What existing platforms do well

| Project | License posture | Pattern worth adopting | ThermoFlow decision |
| --- | --- | --- | --- |
| [CadFlow](https://github.com/yhz5613813/CadFlow) | MIT | Opaque native geometry handles, structured diagnostics, replayable model state, validated Scene artifacts | Direct geometry dependency through the public Python frontend |
| [CadFlow-Harness](https://github.com/zion-zion-zion/CadFlow-Harness) | MIT | Per-project workspaces, immutable run records, bounded execution, model-provider independence, artifact-first viewer | Reuse the architectural pattern; keep thermal plans and results as inspectable artifacts |
| [FreeCAD FEM](https://github.com/FreeCAD/FreeCAD) | LGPL-2.1 | Workbench flow from geometry to materials, constraints, solver writer, run, and result inspection | Adopt the workflow and solver-adapter boundary; do not embed its UI code |
| [SALOME](https://github.com/SalomePlatform) | LGPL-2.1 platform, dependency-specific terms | Study tree, modular pre/post-processing, mesh quality checks, compute-code supervision | Adopt study hierarchy and explicit mesh-quality stage; optional external adapter only |
| [SfePy](https://github.com/sfepy/sfepy) | BSD-3-Clause | Declarative problem definitions separate PDEs, regions, materials, BCs, and mesh files | Candidate permissive unstructured thermal solver adapter |
| [scikit-fem](https://github.com/kinnala/scikit-fem) | BSD-3-Clause for package code; examples may differ | Small Python FEM assembly layer, custom meshes, VTK output, easy verification | Preferred first general FEM backend; do not copy examples without checking their per-file licenses |
| [MFEM](https://github.com/mfem/mfem) | BSD-3-Clause | High-order elements, AMR, MPI/GPU scaling, matrix-free operators | Future HPC backend behind the same solver protocol |
| [MOOSE](https://github.com/idaholab/moose) | LGPL-2.1 | Pluggable physics objects, coupled multiphysics, adaptivity, scalable execution | Learn from plugin boundaries; optional isolated backend after compliance review |
| [DOLFINx](https://github.com/FEniCS/dolfinx) | LGPL-3.0-or-later; repository also contains GPL material | Expressive weak-form PDE authoring and parallel solvers | Do not make a default packaged dependency; optional service adapter only |
| [OpenFOAM](https://github.com/OpenFOAM/OpenFOAM-dev) | GPL-3.0-or-later | Case directories, field dictionaries, residual monitoring, conjugate heat transfer | Concepts only in proprietary core; no source copying or linked dependency |
| [Elmer FEM](https://github.com/ElmerCSC/elmerfem) | Mixed policy by component; review required | Broad heat-transfer/multiphysics catalog and GUI/workflow separation | Reference behavior and optional external adapter only after license audit |
| [Gmsh](https://github.com/live-clones/gmsh) | GPL-2.0-or-later with linking exception; separate commercial licensing exists | Excellent STEP/OpenCascade import, physical groups, size fields, mesh-quality UI | Not bundled by default. Recreate adapter contract and use only under an approved deployment/license model |
| [fTetWild](https://github.com/wildmeshing/fTetWild) | MPL-2.0 | Robust tetrahedralization of imperfect triangle soups | Candidate isolated meshing worker; modifications to MPL files must remain compliant |
| [meshio](https://github.com/nschloe/meshio) | MIT | Neutral conversion across VTK, Gmsh, MED, Abaqus, XDMF, and other mesh formats | Adopt as a permissive interchange layer |
| [ParaView](https://github.com/Kitware/ParaView) | BSD-3-Clause | Scientific filters, scalar bars, clipping, slices, probes, time controls | Recreate the focused browser UX; use its output conventions and VTK ecosystem |
| [VTK.js](https://github.com/Kitware/vtk-js) | BSD-3-Clause | Browser-native scientific field rendering | Preferred future web renderer |
| [PyVista](https://github.com/pyvista/pyvista) | MIT | Python-native mesh analysis and headless image generation | Candidate server-side inspection and regression rendering tool |
| [FastAPI](https://github.com/fastapi/fastapi) | MIT | OpenAPI-first typed service contracts | Adopted for the public API |
| [OpenAI Python](https://github.com/openai/openai-python) | Apache-2.0 | Responses API and typed Structured Outputs | Adopted for GPT planning |

## License policy

Dependency classes are handled differently:

1. **Permissive (MIT/BSD/Apache):** may be packaged after preserving notices and
   verifying transitive dependencies. This is the default class for the core.
2. **Weak copyleft (LGPL/MPL):** commercially usable, but distribution and
   modification obligations remain. Keep these replaceable and isolated, and
   publish required modifications/notices when used.
3. **Strong copyleft (GPL/AGPL):** do not link, vendor, or copy into the
   proprietary core. UI/workflow ideas and published algorithms may inform a
   clean implementation; any external-process deployment requires a separate
   legal review.
4. **Proprietary/reference products:** observe user workflows only. Do not copy
   assets, text, or implementation.

"Commercial use allowed" is not equivalent to "safe to embed in a closed-source
distribution." Every release must preserve license texts and attribution and
must check actual resolved transitive dependencies.

## Consolidated product design

ThermoFlow combines the strongest recurring patterns without cloning another
platform's code:

- **CadFlow-style evidence:** geometry revision, topology facts, and previews are
  stored before meshing.
- **FreeCAD/SALOME-style stages:** Geometry -> Plan -> Mesh -> Solve -> Verify ->
  Results, with stage-specific diagnostics.
- **MOOSE-style plugins:** physics, mesher, and solver implementations are
  registered adapters selected from an allowlist.
- **OpenFOAM-style reproducibility:** every study is a self-contained case with
  inputs, plan, provenance, logs, and results.
- **ParaView-style post-processing:** scalar field, legend, clipping/slicing,
  probes, extrema, and time controls are first-class result concepts.
- **CadFlow-Harness-style agent boundary:** GPT produces inspectable structured
  intent; deterministic code performs geometry, validation, and execution.

## Why GPT is planner, not executor

An unconstrained model can invent units, select nonexistent faces, produce
singular boundary conditions, or request an impractical mesh. ThermoFlow gives
GPT authority over engineering choices while denying it arbitrary execution:

```text
workpiece -> GPT Structured Output -> schema validation -> physics policy
          -> registered mesher/solver -> result checks -> immutable artifacts
```

The model selects material properties, operating temperatures, boundary faces,
mesh target, and solver tolerances. The application enforces finite values,
supported physics, topology references, well-posedness, and resource ceilings.

