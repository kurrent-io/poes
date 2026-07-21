# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# POES — Proof-Oriented Event Sourcing

A Python library that verifies event-sourced aggregates using property-based testing, exhaustive state exploration, and temporal liveness checking — no external SMT solvers or model checkers. It is a **coding-agent-first** framework: an agent writes an aggregate and proves it in the same step, producing a deterministic, machine-readable proof-of-work.

## Setup

```bash
pip install -e ".[dev]"           # library + pytest
pip install -e ".[kurrentdb]"     # + KurrentDB client (Layer 4 persistence)
docker compose up -d              # start local KurrentDB (see docker-compose.yml)
```

For KurrentDB persistence work, also install the KurrentDB coding-agent skills from https://github.com/kurrent-io/coding-agent-skills/ (Docker setup, client API reference, persistence patterns).

## Commands

```bash
pytest tests/                                   # all tests (no KurrentDB needed — clients are mocked)
pytest tests/test_bank_account.py               # one file
pytest tests/test_persistence_check.py -k violation   # one test by name substring

python samples/bank_account.py                  # pure-verification samples run verify() directly
python samples/gift_card.py --verify            # KurrentDB samples: --verify runs the model checker only (no DB)
python samples/gift_card.py                      # ...without --verify they run the live KurrentDB demo (DB required)
```

Pure-verification samples: `bank_account.py`, `gambling_wallet.py`, `inventory.py`. KurrentDB samples (support `--verify`): `gift_card.py`, `hotel_reservation.py`, `order_book.py`. Running a KurrentDB sample without `--verify` and without a running server will fail at connect time.

Note: `pyproject.toml` declares an `integration` pytest marker, but no test currently uses it — persistence tests mock the client with `MagicMock`, so the full suite runs offline.

## The skill is the API contract

`.claude/skills/poes/SKILL.md` is the authoritative, user-facing API reference (builder methods, six templates, common mistakes). **Read it before writing or modifying any POES aggregate**, and keep it in sync when you change the public `CheckBuilder` surface. `README.md` mirrors a subset of it.

## Architecture

POES verifies four independent property categories through four engines. `check.py` is the only orchestrator; the four engines under `src/poes/` know nothing about each other.

**`check.py` — fluent builder + orchestrator.** `Check.define(name, StateClass)` returns a `CheckBuilder`. The `.with_*` methods only *accumulate* configuration into private fields (`_fields`, `_invariants`, `_transitions`, `_parametric_transitions`, `_temporal_properties`, `_state_diagram`). `.run()` then drives the phases in order and prints the box-drawing report:

1. **Property testing** → `hypothesis_bridge.py`. `st.builds(state_type, **fields)` generates random states; each is filtered by `guard` (and invariant), the transition is applied, and the invariant + `ensures` postcondition are asserted. `verify_parametric_properties` feeds param strategies straight into `@given(state=…, **param_strats)`.
2. **State exploration** → `explorer.py`. `explore()` does BFS over all reachable states from the initial state, checking every invariant and postcondition on each edge. Returns a tagged-union result (`AllSafe | StateViolation | PostConditionViolation | LimitReached | ExplorerError`) with a counterexample path.
3. **Temporal / liveness** → `temporal.py`. `build_graph()` builds the reachable-state graph, `find_sccs()` is an iterative Tarjan SCC, and `check_all()` decides `Eventually` / `LeadsTo` / `AlwaysEventually` by looking for cycles (non-trivial SCCs) that never satisfy the target predicate.
4. **Persistence** → `persistence_check.py`. `verify_persistence()` replays real KurrentDB events through `apply` and checks invariants after every event. This is **not** part of `.run()` — call `builder.verify_persistence(...)` separately (it needs only invariants, no fields/transitions).

### Load-bearing design constraints

- **State must be `@dataclass(frozen=True)` (hashable).** BFS keeps a `visited: set` and temporal keeps `graph.states: set`; unhashable state breaks both. This is the entire reason `frozen_map.py`'s `FrozenMap` exists — a `dict` field would be unhashable, so map-valued state uses `FrozenMap` (immutable, `.set()`/`.delete()` return new instances) via `with_map_field(...)`.
- **Parametric transitions behave differently per engine.** Property testing explores the *full* parameter space via Hypothesis. But BFS and temporal need *concrete* transitions, so `.run()` calls `_sample_params()` to draw ~5 representative param combos and expands each parametric transition into ~5 concrete `Transition` closures. Consequence: **BFS/temporal only see a sample of parameter values** — property testing is what gives broad parameter coverage. When a bug only reproduces at a specific parameter value, it may surface in Phase 1 but not Phase 2/3.
- **`docgen.py` is coupled to `CheckBuilder`'s private fields.** `generate_proof_of_work()` reads `builder._invariants` etc. directly and reconstructs predicate text by running `inspect.getsource()` on the lambdas (`_extract_lambda_source`), then rewriting to math notation (`_math_ify`). So: (a) predicates must be real lambdas defined in a source file (source must be retrievable), and (b) renaming a `CheckBuilder` private field silently breaks proof generation.
- **Windows stdout.** `_ensure_utf8_stdout()` (in `check.py` and `persistence_check.py`) rewraps stdout to UTF-8 so the box-drawing report renders. Primary dev environment is Windows.

### KurrentDB integration (the sample pattern)

`repository.py`'s `Repository` implements the event-sourced write path: `load()` folds events into state and tracks version; `execute()` runs `load → decide → append` with optimistic concurrency (`current_version` / `StreamState.NO_STREAM`, retry on `WrongCurrentVersionError`). It is generic over four user functions: `initial`, `apply(state, event) -> state`, `decide(state, command) -> [events]`, and the `event_to_json` / `event_from_json` codecs.

KurrentDB samples keep **two state types**: a frozen `VerifyState` for the model checker, and a (possibly mutable) runtime state whose `apply` the `Repository` and `verify_persistence` share. The intent is that the same `apply` used at runtime is the one verified against production data — that is the "verification gap" POES targets.

## Status

Experimental (`Development Status :: 3 - Alpha`), written by Claude Code, MIT-licensed. Not recommended for production; the project welcomes feedback and PRs. Philosophy: https://www.kurrent.io/blog/proof-oriented-event-sourcing/
