# POES - Proof-Oriented Event Sourcing

**Write your domain logic once. Verify it automatically.**

POES is a Python framework that enables you to write event-sourced aggregates with formal verification guarantees. It combines Hypothesis property-based testing with exhaustive state exploration and temporal property checking — no external SMT solvers or model checkers required.

## Built for Coding Agents

POES is designed so AI coding agents can verify the code they generate. An agent produces an event-sourced aggregate and runs POES verification in the same step — the result is deterministic and machine-readable, with no ambiguity to hallucinate around. When a future change breaks a previously proven property, the agent gets a minimal counterexample showing exactly what broke. Developers describe business rules and review verification results; the agent handles event sourcing machinery.

## What It Verifies

POES proves four categories of properties about your event-sourced aggregates:

1. **Property Testing** — Hypothesis-powered testing of transitions against invariants
2. **State Space Safety** — All reachable states satisfy all invariants (BFS exploration)
3. **Temporal Properties** — Liveness properties like "eventually" and "leads-to" (SCC analysis)
4. **Persistence Verification** — Replay production events from KurrentDB and check every intermediate state against all invariants

## Quick Example

```python
from dataclasses import dataclass, replace
import hypothesis.strategies as st
from poes import Check

@dataclass(frozen=True)
class BankAccount:
    balance: int = 0
    is_open: bool = True

result = (
    Check.define("BankAccount", BankAccount)
    .with_initial(BankAccount(balance=0, is_open=True))
    .with_field("balance", st.integers(0, 1000))
    .with_field("is_open", st.booleans())
    # Invariants
    .with_invariant("BalanceNonNegative", lambda s: s.balance >= 0)
    .with_invariant("BalanceBounded", lambda s: s.balance <= 1000)
    # Parametric transition — Hypothesis generates amount automatically
    .with_parametric_transition("Deposit",
        params={"amount": st.integers(1, 500)},
        guard=lambda s, amount: s.is_open and s.balance + amount <= 1000,
        apply=lambda s, amount: replace(s, balance=s.balance + amount),
        ensures=lambda before, after, amount: after.balance == before.balance + amount)
    # Fixed transition
    .with_transition("Withdraw",
        guard=lambda s: s.is_open and s.balance >= 50,
        apply=lambda s: replace(s, balance=s.balance - 50),
        ensures=lambda before, after: after.balance == before.balance - 50)
    # Temporal properties
    .with_eventually("CanClose", lambda s: not s.is_open)
    .with_leads_to("BalanceCanDrain",
        trigger=lambda s: s.is_open and s.balance > 0,
        response=lambda s: s.balance == 0)
    .with_always_eventually("CanDeposit", lambda s: s.is_open)
    .run()
)
```

Output:
```
╔══════════════════════════════════════════════════════════════════════════════╗
║  VERIFYING: BankAccount
╚══════════════════════════════════════════════════════════════════════════════╝

  Fields: 2 | Max examples: 500 | Invariants: 2 | Transitions: 2

  ┌─ Property Testing ──────────────────────────────────────────────────────────┐
  │  ✓ Deposit/BalanceNonNegative  (500 examples)
  │  ✓ Deposit/BalanceBounded  (500 examples)
  │  ✓ Withdraw/BalanceNonNegative  (500 examples)
  │  ✓ Withdraw/BalanceBounded  (500 examples)
  └─────────────────────────────────────────────────────────────────────────────┘

  ┌─ State Exploration ─────────────────────────────────────────────────────────┐
  │  ✓ All 22 reachable states safe
  └─────────────────────────────────────────────────────────────────────────────┘

  ┌─ Temporal Properties ──────────────────────────────────────────────────────┐
  │  ✓ Eventually(CanClose)
  │  ✓ LeadsTo(BalanceCanDrain)
  │  ✓ AlwaysEventually(CanDeposit)
  └─────────────────────────────────────────────────────────────────────────────┘

  ✓ VERIFIED: All 7 proofs passed, 22 states explored (150ms)
```

## API Reference

### Check.define

Start defining an aggregate for verification:

```python
Check.define("AggregateName", StateClass)
```

### .with_initial

Set the initial state for state exploration:

```python
.with_initial(BankAccount(balance=0, is_open=True))
```

### .with_field

Define a field with a Hypothesis strategy for property testing:

```python
.with_field("balance", st.integers(0, 1000))
.with_field("is_open", st.booleans())
.with_field("status", st.sampled_from(["pending", "active", "closed"]))
```

### .with_map_field

Define a `FrozenMap` field (hashable, immutable dict) with key/value strategies:

```python
from poes import FrozenMap

.with_map_field("balances", st.text(min_size=1, max_size=3), st.integers(0, 100),
                min_size=0, max_size=5)
```

This is shorthand for `.with_field(name, FrozenMap.strategy(keys, values, ...))`. Use `FrozenMap` in your state dataclass instead of `dict`:

```python
@dataclass(frozen=True)
class Ledger:
    balances: FrozenMap  # not dict — FrozenMap is hashable

# Immutable updates return new FrozenMap instances:
new_state = replace(s, balances=s.balances.set("alice", 100))
new_state = replace(s, balances=s.balances.delete("bob"))
```

`FrozenMap` supports `__getitem__`, `get`, `__contains__`, `__iter__`, `__len__`, `keys`, `values`, `items`. All values must be hashable (ints, strings, bools, tuples, nested FrozenMaps).

### .with_invariant

Add invariants that must always hold:

```python
.with_invariant("BalanceNonNegative", lambda s: s.balance >= 0)
.with_invariant("ReservedNotExceedStock", lambda s: s.reserved <= s.quantity)
```

### .with_transition

Add a transition with guard, apply function, and post-condition (fixed values):

```python
.with_transition("Withdraw",
    guard=lambda s: s.is_open and s.balance >= 50,
    apply=lambda s: replace(s, balance=s.balance - 50),
    ensures=lambda before, after: after.balance == before.balance - 50)
```

### .with_parametric_transition

Add a transition whose parameters are generated by Hypothesis. Instead of fixed values (e.g., always deposit 100), Hypothesis generates arbitrary parameter values and proves properties hold for all of them:

```python
.with_parametric_transition("Deposit",
    params={"amount": st.integers(1, 500)},
    guard=lambda s, amount: s.is_open and s.balance + amount <= 1000,
    apply=lambda s, amount: replace(s, balance=s.balance + amount),
    ensures=lambda before, after, amount: after.balance == before.balance + amount)
```

- `params`: dict of name → Hypothesis strategy (same pattern as `with_field`)
- `guard(state, **params)`: when can this transition fire for these params?
- `apply(state, **params)`: produce the new state
- `ensures(before, after, **params)`: post-condition that must hold

For state exploration (BFS) and temporal checking, each parametric transition is expanded into ~5 concrete transitions by sampling representative parameter values.

### Temporal Properties

```python
# Eventually: state predicate can be reached from any cycle
.with_eventually("CanClose", lambda s: not s.is_open)

# LeadsTo: whenever trigger holds, response eventually holds
.with_leads_to("BalanceCanDrain",
    trigger=lambda s: s.is_open and s.balance > 0,
    response=lambda s: s.balance == 0)

# AlwaysEventually: predicate holds infinitely often on every path
.with_always_eventually("CanDeposit", lambda s: s.is_open)
```

## Samples

| Sample | Description | Key Invariants |
|--------|-------------|----------------|
| **bank_account** | Simple bank account | Balance >= 0, Balance <= max |
| **gambling_wallet** | Casino betting wallet | Balance >= 0, ActiveBet >= 0 |
| **inventory** | Warehouse inventory | Reserved <= Quantity |
| **order_book** | Order book (map-based) | Scalar summaries for maps |
| **gift_card** | Gift card system | Full KurrentDB integration |
| **hotel_reservation** | Hotel room lifecycle | State diagram, temporal properties |

Run a sample:

```bash
python samples/bank_account.py
```

## How It Works

POES uses three complementary verification techniques:

### 1. Property Testing (Hypothesis)

Uses Hypothesis to generate random states from field strategies and test that every transition preserves every invariant. Runs hundreds of examples per (transition, invariant) pair.

### 2. State Exploration

Starting from the initial state, explores all reachable states by applying all enabled transitions (BFS). Verifies that every reachable state satisfies all invariants and every transition satisfies its post-condition.

### 3. Temporal Verification

Builds a state graph and uses Tarjan's SCC algorithm to identify cycles. Checks liveness properties:
- **Eventually**: No cycle exists where the predicate never holds
- **LeadsTo**: From any trigger state, response is eventually reachable
- **AlwaysEventually**: Every cycle passes through a satisfying state

### 4. Persistence Verification

Replays actual events from KurrentDB and checks that every intermediate state satisfies all invariants. Catches bugs that the model can't: serialization errors, manual edits, migration issues, or command-handler bugs that produce events the model wouldn't allow.

## Limitations

### State Space Size

- State exploration is limited to prevent memory exhaustion (default: 10,000 states for temporal checking)
- Large state spaces may hit limits before full exploration
- Use bounded strategies to control state space size
- Map fields (`FrozenMap`) can cause state space explosion — use small `max_size` bounds

### Finite Domains Only

- POES tests concrete values via Hypothesis, not symbolic reasoning
- Properties are verified probabilistically (property testing) and exhaustively within reachable states (exploration)
- Add more Hypothesis examples with `.with_max_examples()` for more thorough coverage

### Temporal Properties

- Graph builder limits to 10,000 states
- No partial order reduction or symmetry breaking yet
- Very large state spaces may need bounded checking

## Project Structure

```
src/poes/
├── __init__.py            # Package exports
├── check.py               # Check.define fluent API
├── frozen_map.py          # FrozenMap — hashable immutable dict
├── hypothesis_bridge.py   # Hypothesis property-based testing
├── explorer.py            # BFS state exploration
├── temporal.py            # SCC-based liveness checking
├── repository.py          # KurrentDB persistence (Repository pattern)
└── docgen.py              # Markdown proof document generation

samples/
├── bank_account.py        # Bank account with deposits/withdrawals
├── gambling_wallet.py     # Casino wallet with betting
├── inventory.py           # Warehouse inventory management
└── order_book.py          # Order book with map-based state

benchmark/                 # AI agent benchmark framework
├── runner.py              # Benchmark runner
├── judge.py               # Test evaluation
├── prompts/               # Control and treatment prompts
└── tasks/                 # Benchmark tasks
```

## Requirements

- Python 3.10+
- hypothesis

Install:

```bash
pip install hypothesis
```

## License

MIT
