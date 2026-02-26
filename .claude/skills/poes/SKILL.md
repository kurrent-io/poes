# POES Verification Skill

## Trigger

Activate when the user asks to:
- Verify an event-sourced aggregate
- Prove properties about state transitions
- Use the POES (Proof-Oriented Event Sourcing) library
- Add invariants, transitions, or temporal properties to a Check
- Verify production data against invariants (persistence verification)

## Prerequisites

```bash
pip install poes   # or: pip install -e . (from repo root)
```

The `poes` package provides `Check`, `CheckBuilder`, `CheckResult`, `PersistenceCheckResult`, and `FrozenMap`.

## Core Concept

POES verifies event-sourced aggregates by:
1. **Property testing** (Hypothesis) — random states × transitions must preserve invariants and postconditions
2. **State exploration** (BFS) — exhaustively visits all reachable states from the initial state
3. **Temporal properties** (SCC analysis) — liveness checks over the reachable state graph
4. **Persistence verification** (KurrentDB) — replays production events and checks invariants hold after every event

The `apply(state, event) -> State` function is the **single source of truth** — the same function used at runtime for event replay is verified here.

## API Reference

### Entry Point

```python
from poes import Check

builder = Check.define("AggregateName", StateClass)
```

- `name`: Display name for verification output
- `state_type`: The frozen dataclass type representing aggregate state

### CheckBuilder Methods

All methods return `self` for fluent chaining.

#### `with_initial(state) -> CheckBuilder`
Set the initial state. **Required** before `.run()`.

#### `with_field(name: str, strategy: SearchStrategy) -> CheckBuilder`
Register a state field with a Hypothesis strategy for property testing. Must match field names on the state dataclass.

Common strategies:
- `st.integers(min, max)` — bounded integers
- `st.booleans()` — True/False
- `st.sampled_from([...])` — enum-like values
- `st.text(min_size=0, max_size=10)` — strings

#### `with_map_field(name, keys_strategy, values_strategy, *, min_size=0, max_size=None) -> CheckBuilder`
Register a `FrozenMap` field. Shorthand for `with_field(name, FrozenMap.strategy(keys, values, ...))`. Use `FrozenMap` (not `dict`) in the state dataclass — it's hashable and immutable.

#### `with_invariant(name: str, check: Callable[[State], bool]) -> CheckBuilder`
Add a state invariant that must hold for ALL reachable states.

#### `with_transition(name, guard, apply, ensures) -> CheckBuilder`
Add a fixed transition (no parameters).
- `guard(state) -> bool` — when can this transition fire?
- `apply(state) -> State` — produce the next state
- `ensures(before, after) -> bool` — postcondition that must hold

#### `with_transition_named(name, guard, apply, ensures_name, ensures) -> CheckBuilder`
Same as `with_transition` but with a custom name for the postcondition.

#### `with_parametric_transition(name, params, guard, apply, ensures) -> CheckBuilder`
Add a transition with Hypothesis-generated parameters.
- `params`: `dict[str, SearchStrategy]` — parameter strategies
- `guard(state, **params) -> bool`
- `apply(state, **params) -> State`
- `ensures(before, after, **params) -> bool`

#### `expect_transitions(count: int) -> CheckBuilder`
Assert the total number of transitions registered. Raises `ValueError` if mismatch at `.run()` time. Useful to ensure all event types are covered.

#### `with_max_examples(n: int) -> CheckBuilder`
Set Hypothesis max examples (default: 500).

#### Temporal Properties

- `with_eventually(name, predicate)` — some reachable state satisfies predicate
- `with_leads_to(name, trigger, response)` — every state matching trigger can reach a state matching response
- `with_always_eventually(name, predicate)` — from every reachable state, predicate is reachable again

#### `generate_proof_of_work(path=None) -> str`
Generate a Markdown proof document. If `path` is given, also writes to file.

#### `verify_persistence(*, client, stream_prefix, initial, apply, event_from_json, stream_ids=None) -> PersistenceCheckResult`
Replay production events from KurrentDB and verify all invariants hold after every event. This is Layer 4 — it closes the gap between "model is correct" and "production data is correct."

- `client`: KurrentDB client instance
- `stream_prefix`: naming prefix (e.g. `"BankAccount"`)
- `initial`: initial state value or callable factory
- `apply`: `(state, event) -> state` — the same apply function used in verification
- `event_from_json`: `(type_str, data_bytes) -> event` — deserializer
- `stream_ids`: list of IDs to check, or `None` to discover all streams matching prefix

When `stream_ids` is provided, reads each stream via `client.get_stream(f"{prefix}-{id}")`.
When `stream_ids` is `None`, reads all streams via `client.read_all()` with a prefix filter and groups by stream name.

Returns:
```python
@dataclass(frozen=True)
class PersistenceCheckResult:
    streams_checked: int
    streams_passed: int
    streams_failed: int
    events_replayed: int
    all_passed: bool
    failures: list   # list[StreamInvariantViolation]
    errors: list     # list[StreamError]
    duration_ms: int
```

Failure details:
```python
@dataclass(frozen=True)
class StreamInvariantViolation:
    stream_name: str
    invariant_name: str
    event_position: int
    event_type: str
    violating_state: object
```

#### `run() -> CheckResult`
Execute all verification phases. Prints formatted output and returns:
```python
@dataclass(frozen=True)
class CheckResult:
    aggregate_name: str
    property_passed: bool
    property_proofs: int
    property_failed: int
    exploration_passed: bool
    states_explored: int
    temporal_passed: bool
    temporal_properties_checked: int
    all_passed: bool
    duration_ms: int
    counterexample: str | None
```

## Templates

### Template 1: Simple Aggregate

```python
from __future__ import annotations
from dataclasses import dataclass, replace
import hypothesis.strategies as st
from poes import Check

@dataclass(frozen=True)
class MyState:
    field_a: int
    field_b: bool

INITIAL = MyState(field_a=0, field_b=True)

def apply(state: MyState, event: dict) -> MyState:
    """Shared apply function — used by both verification and persistence."""
    match event["type"]:
        case "EventA":
            return replace(state, field_a=state.field_a + event["amount"])
        case "EventB":
            return replace(state, field_b=False)
    return state

def verify():
    result = (
        Check.define("MyAggregate", MyState)
        .with_initial(INITIAL)
        .with_field("field_a", st.integers(0, 100))
        .with_field("field_b", st.booleans())
        .with_invariant("FieldANonNegative", lambda s: s.field_a >= 0)
        .with_transition(
            "EventA",
            guard=lambda s: s.field_b and s.field_a + 10 <= 100,
            apply=lambda s: replace(s, field_a=s.field_a + 10),
            ensures=lambda before, after: after.field_a == before.field_a + 10,
        )
        .with_transition(
            "EventB",
            guard=lambda s: s.field_b,
            apply=lambda s: replace(s, field_b=False),
            ensures=lambda before, after: not after.field_b,
        )
        .run()
    )
    assert result.all_passed
```

### Template 2: Parametric Transitions

Use when transitions have variable parameters (amounts, quantities, etc.):

```python
result = (
    Check.define("Account", Account)
    .with_initial(Account(balance=0, is_open=True))
    .with_field("balance", st.integers(0, 1000))
    .with_field("is_open", st.booleans())
    .with_invariant("BalanceNonNegative", lambda s: s.balance >= 0)
    .with_invariant("BalanceBounded", lambda s: s.balance <= 1000)
    .with_parametric_transition(
        "Deposit",
        params={"amount": st.integers(1, 500)},
        guard=lambda s, amount: s.is_open and s.balance + amount <= 1000,
        apply=lambda s, amount: replace(s, balance=s.balance + amount),
        ensures=lambda before, after, amount: after.balance == before.balance + amount,
    )
    .run()
)
```

### Template 3: Map-Based State with FrozenMap

Use `FrozenMap` for map/dict fields in aggregate state. It's hashable and immutable, so BFS exploration and temporal checking work out of the box.

```python
from poes import Check, FrozenMap

@dataclass(frozen=True)
class Ledger:
    balances: FrozenMap  # not dict — FrozenMap is hashable

INITIAL = Ledger(balances=FrozenMap())

result = (
    Check.define("Ledger", Ledger)
    .with_initial(INITIAL)
    .with_map_field("balances", st.text(min_size=1, max_size=2), st.integers(0, 100),
                    min_size=0, max_size=3)
    .with_invariant("AllNonNegative", lambda s: all(v >= 0 for v in s.balances.values()))
    .with_parametric_transition(
        "Credit",
        params={"account": st.text(min_size=1, max_size=2), "amount": st.integers(1, 50)},
        guard=lambda s, account, amount: s.balances.get(account, 0) + amount <= 100,
        apply=lambda s, account, amount: Ledger(
            balances=s.balances.set(account, s.balances.get(account, 0) + amount)),
        ensures=lambda before, after, account, amount:
            after.balances[account] == before.balances.get(account, 0) + amount,
    )
    .run()
)
```

**FrozenMap API:**
- Constructor: `FrozenMap({"a": 1})` or `FrozenMap()` for empty
- Read: `m[key]`, `m.get(key, default)`, `key in m`, `len(m)`, `m.keys()`, `m.values()`, `m.items()`
- Immutable update: `m.set(key, value)` and `m.delete(key)` — both return a new `FrozenMap`
- Strategy: `FrozenMap.strategy(keys_strat, values_strat, min_size=0, max_size=None)`
- All values must be hashable (ints, strings, bools, tuples, nested FrozenMaps)

**Tip:** Map fields cause state space explosion in BFS. Keep `max_size` small (2–4) to stay within exploration limits. For very large maps, you can still fall back to scalar summaries.

### Template 4: Temporal Properties

```python
builder = (
    Check.define("Workflow", WorkflowState)
    # ... fields, invariants, transitions ...
    # "Some reachable state satisfies this"
    .with_eventually("CanComplete", lambda s: s.status == "completed")
    # "If trigger holds, response is eventually reachable"
    .with_leads_to(
        "PendingCompletes",
        trigger=lambda s: s.status == "pending",
        response=lambda s: s.status == "completed",
    )
    # "From every reachable state, this is always reachable again"
    .with_always_eventually("AlwaysRecoverable", lambda s: s.is_healthy)
)
```

### Template 5: Proof-of-Work Generation

```python
from pathlib import Path

builder = (
    Check.define("MyAggregate", MyState)
    # ... full configuration ...
)

# Generate proof document before running
builder.generate_proof_of_work(path=Path("proof.md"))

# Run verification
result = builder.run()
```

### Template 6: Persistence Verification

Verify that production data in KurrentDB satisfies all invariants. Requires a KurrentDB client and the same `apply` + `event_from_json` used by the Repository.

```python
from kurrentdbclient import KurrentDBClient
from poes import Check

client = KurrentDBClient("kurrentdb://localhost:2113?tls=false")

builder = (
    Check.define("BankAccount", BankAccount)
    .with_invariant("BalanceNonNegative", lambda s: s.balance >= 0)
    .with_invariant("BalanceBounded", lambda s: s.balance <= 1000)
)

# Check specific streams
result = builder.verify_persistence(
    client=client,
    stream_prefix="BankAccount",
    initial=INITIAL,
    apply=apply,
    event_from_json=event_from_json,
    stream_ids=["acc-1", "acc-2"],
)
assert result.all_passed

# Or check ALL streams matching the prefix
result = builder.verify_persistence(
    client=client,
    stream_prefix="BankAccount",
    initial=INITIAL,
    apply=apply,
    event_from_json=event_from_json,
    stream_ids=None,  # discovers all BankAccount-* streams
)
```

Console output:
```
  ┌─ Persistence Verification ────────────────────────────────────────────────┐
  │  ✓ BankAccount-acc-1  (3 events)
  │  ✓ BankAccount-acc-2  (7 events)
  │  ✗ BankAccount-acc-3  event #4 (Withdrawn) violates BalanceNonNegative
  └─────────────────────────────────────────────────────────────────────────────┘
```

**Tip:** Persistence verification only needs invariants — no fields, transitions, or temporal properties required on the builder. You can run it independently of `.run()`.

## Common Mistakes

1. **Mutable state dataclass** — The verification state MUST be `@dataclass(frozen=True)` so it's hashable for BFS exploration.

2. **Fields don't match dataclass** — `with_field("balance", ...)` must correspond to a field named `balance` on the state dataclass.

3. **Guard too permissive** — If the guard allows a transition that violates an invariant, exploration will find it. Make guards tight enough to prevent violations.

4. **Missing postcondition** — Every transition should have an `ensures` that checks the specific effect. Don't just check invariants — check that the *intended change* happened.

5. **Forgetting `replace()`** — Use `dataclasses.replace(state, field=new_value)` to produce new frozen state. Don't try to mutate.

6. **Parametric guard ignores params** — `guard=lambda s, amount: s.is_open` should probably be `guard=lambda s, amount: s.is_open and s.balance + amount <= MAX`.

7. **Temporal without sufficient transitions** — `with_always_eventually` requires that the predicate is reachable from *every* state in the graph. If a state has no outgoing transitions that lead back, it fails.
