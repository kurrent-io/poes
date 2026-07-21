---
description: Build and prove an event-sourced aggregate with POES (generate → verify → repair until proven)
argument-hint: <aggregate description>
---
Build a fully POES-verified event-sourced aggregate for: $@

Follow this exact workflow and do not stop until every step is complete:

1. **Design** — a frozen `@dataclass(frozen=True)` state, one event type per state change, and an `apply(state, event)` fold. Use `poes.FrozenMap` for any map/dict field.
2. **Write** the aggregate to a `.py` file, including a `def verify() -> CheckResult` that builds `Check.define(...)` with:
   - a field per state field (`with_field` / `with_map_field`),
   - an invariant per business rule (`with_invariant`),
   - a transition per event type with a `guard`, an `apply`, and a real `ensures`,
   - `.expect_transitions(N)` matching the number of event types,
   - `.generate_proof_of_work(path=...)`, then `return builder.run()`.

   Consult the POES skill (`/skill:poes`) for the exact API — do not guess.
3. **Verify** — call the `poes_verify` tool on the file.
4. **Repair** — if it does not pass, read the counterexample and fix the guard, the `apply`, or the invariant (do not weaken an invariant unless it is genuinely the wrong rule). Then go back to step 3.
5. **Done** only when `poes_verify` reports all proofs passed. The poes-enforce gate validates every aggregate you write and will re-surface any that is not proven, so the task is not finished until the proof is green.
