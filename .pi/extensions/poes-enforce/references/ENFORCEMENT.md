# POES Enforcement Policy

This policy is enforced by the `poes-enforce` pi extension. It governs how
event-sourced aggregates are **generated** and how they are **validated**.

## When it applies

An artifact is treated as an **event-sourced aggregate** when a `.py` file
contains a frozen dataclass state together with event-sourcing markers — an
`apply(state, event)` fold and/or a `decide(state, command)` handler, event
types, or KurrentDB usage (`append_to_stream`, `Repository`, …).

## Generation rules (what you MUST produce)

When you write or modify an aggregate, in the same change you MUST also provide
its POES proof:

1. **State is frozen and hashable** — `@dataclass(frozen=True)`. Use
   `poes.FrozenMap` for map/dict fields (a plain `dict` is unhashable and breaks
   BFS/temporal). Produce new state with `dataclasses.replace(...)`.

2. **Expose a verification entrypoint** — a module-level
   `def verify() -> CheckResult` (or a module-level `POES_CHECK` builder). It
   MUST build a `Check.define(name, StateClass)` and **`return builder.run()`**.
   Never `sys.exit()` from `verify()`; return the result.

3. **Cover the whole model**:
   - `.with_initial(...)` — the initial state.
   - `.with_field(...)` / `.with_map_field(...)` — every field of the state
     dataclass, with a Hypothesis strategy.
   - `.with_invariant(...)` — every business rule that must always hold.
   - one transition per event type — `.with_transition(...)` or
     `.with_parametric_transition(...)` — each with a `guard`, an `apply`, and a
     non-trivial `ensures` postcondition (assert the *intended* change, not just
     that invariants survived).
   - `.expect_transitions(N)` where `N` equals the number of event types, so a
     forgotten event fails fast.

4. **Emit a proof-of-work** — call `.generate_proof_of_work(path=...)` before
   `.run()` so the change ships with a human-auditable proof document.

5. **Read the API first** — `.claude/skills/poes/SKILL.md` (and the pi skill, if
   installed) is the authoritative reference: builder methods, six templates,
   and common mistakes. Consult it rather than guessing.

## Validation rules (the gate)

- After you finish, the extension runs `poes_validate.py` on every aggregate you
  touched. An aggregate **passes** only when it exposes an entrypoint, that
  entrypoint returns a `CheckResult`, and `result.all_passed` is `True`
  (property testing, state exploration, and any temporal properties all green).
- In **block** mode (the default) a failing or missing proof re-engages you with
  the counterexample; fix the aggregate or the proof and continue until it
  passes. In **warn** mode failures are surfaced but do not re-engage. In **off**
  mode nothing runs.
- A minimal counterexample means the model, not the proof harness, is wrong —
  tighten a guard, correct an `apply`, or fix the invariant. Do not weaken an
  invariant just to make the proof pass unless that is genuinely the intended
  domain rule.

## Persistence (optional, Layer 4)

If the aggregate is backed by KurrentDB, the same `apply` used at runtime should
be verifiable against production streams via `builder.verify_persistence(...)`.
Keep one `apply` as the single source of truth for replay and verification.
