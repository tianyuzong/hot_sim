# Agentic thermal simulation research

Research date: 2026-09-04.

## Methods reviewed

| Method | Useful idea | Limitation for CAE | ThermoFlow decision |
| --- | --- | --- | --- |
| [Plan-and-Solve](https://arxiv.org/abs/2305.04091) | Split a complex objective into an explicit plan and ordered subtasks | A plan alone does not react to solver output | Keep explicit phases, but evaluate after every solve |
| [Toolformer](https://arxiv.org/abs/2302.04761) | Let the model decide when an external API is useful | General tool freedom is unnecessary and unsafe for a solver service | Expose only typed, registered simulation tools |
| [ReAct](https://arxiv.org/abs/2210.03629) | Interleave environment observations with actions | Unbounded trajectories can loop and become expensive | Use Observe -> Evaluate -> Decide -> Act with a hard five-round ceiling |
| [Reflexion](https://arxiv.org/abs/2303.11366) | Feed outcome signals into the next attempt without model retraining | Free-form self-critique is not a physical verifier | Feed scalar solver metrics and deterministic violations into the next decision |
| [AgentBench](https://arxiv.org/abs/2308.03688) | Evaluate agents as multi-turn systems, not one-shot text generators | General benchmarks do not measure physical fidelity | Build domain evals around goal satisfaction, conservation, mesh sensitivity, cost, and action count |
| [ChatCFD](https://advanced.onlinelibrary.wiley.com/doi/10.1002/aidi.202500174) | Structured domain knowledge, error localization, iterative reflection, and physical-fidelity evaluation | Its multi-agent CFD stack is heavier than this steady thermal scope | Adopt physics verification and bounded reflection first; defer role specialization |
| [OpenAI Responses API](https://developers.openai.com/api/reference/cli/resources/responses/methods/create) | Strongly typed custom tools and structured outputs | The application still owns tool execution, validation, and state | Use a strict model action schema and execute only server-computed adjustments |

ChatCFD is especially relevant because its reported execution success is higher
than its physical-fidelity score. That gap is the key product lesson: a completed
solver process is not enough. ThermoFlow therefore treats deterministic physics
checks, not model confidence or fluent narration, as the terminal condition.

## Implemented architecture

```text
Engineer goal + explicit limits
        |
        v
inspect_geometry
        |
        v
inspect_result --> evaluate_constraints --> finish
                         |
                         v
                 adjust_parameters
                         |
                         v
                    run_solver
                         |
                         +------ feedback to inspect_result
```

The Agent starts from an already completed study. It can inspect geometry and
solver results, evaluate peak-temperature bounds, energy balance, and embedded
source depth, then create a new immutable study. The current allowlisted actions
are heat-source power adjustment, attainable-depth reconciliation, and mesh or
linear-tolerance refinement. Every candidate passes the existing `PlanPolicy`
before the registered solver runs.

The OpenAI policy chooses only `finish` or `apply_adjustment` through Structured
Outputs. Numerical changes are calculated by deterministic application code.
The offline policy runs the same state machine without an API key. Neither mode
can execute shell commands, arbitrary Python, uploaded plugins, or user-selected
paths. Concise decision summaries are stored; hidden chain-of-thought is not.

## Evaluation contract

A run is successful only when all requested, machine-checkable criteria pass:

- peak temperature is within the explicit upper and lower limits, including tolerance;
- source mapping reaches the requested embedded depth, or the request is reconciled to the reachable domain;
- relative energy-balance error is below the user limit;
- every candidate study passes schema and deterministic policy validation;
- the run stops within the user-selected one-to-five solver-call budget.

The complete tool trajectory, candidate study IDs, provider/model provenance,
and selected study are persisted under `data/agent-runs/`.

## Recommended next increments

1. Add surface probes and hotspot coordinates to `SimulationResult`. This is a
   prerequisite for defensible source-position optimization.
2. Add a bounded design-of-experiments tool for source position, power, and area.
   Let a numerical optimizer rank candidates; let the model interpret the goal.
3. Add a two- or three-level mesh-convergence tool. Energy balance alone does not
   establish discretization independence.
4. Add a versioned material and boundary-condition knowledge base with citations,
   temperature ranges, and approval status. Do not let free-form retrieval values
   bypass policy.
5. Build a regression suite of closed, open, thin, disconnected, and malformed
   STL cases. Track physical-goal pass rate, false completion rate, solver calls,
   wall time, and token cost.
6. Introduce specialist agents only when additional physics creates genuinely
   separate responsibilities such as meshing, conjugate heat transfer, radiation,
   and result review. A single orchestrator is easier to audit at the current scope.

