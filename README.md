# POES — Introducing an experimental project to shorten the verification gap in AI generated code

This project is currently in an experimental stage, written by Claude Code. We do not recommend running this into production.
This is also a coding agent first framework and skill. It is intended to be run in Claude Code, or any other coding agent. The proof of work is for the human.

Philosophy: https://www.kurrent.io/blog/proof-oriented-event-sourcing/

We are looking for feedback on its usefulness and shortfalls. We also welcome pull requests, including those by coding agents.

**Write your domain logic once. Verify it automatically.**

POES is a Python library that verifies event-sourced aggregates using property-based testing, exhaustive state exploration, and temporal property checking — no external SMT solvers or model checkers required.

## Quick Usage

"Use https://github.com/kurrent-io/poes/ to build and verify {prompt or spec} and then provide me with a proof-of-work (this is deterministic)".

## Installation

### 1. Install the package

```bash
pip install poes
```

Or install from source:

```bash
git clone https://github.com/kurrent-io/poes.git
cd poes
pip install -e .
```

### 2. Install the coding agent skill

Copy the skill into your project so your coding agent (Claude Code, Cursor, Windsurf, etc.) learns the POES API:

```bash
mkdir -p .claude/skills/poes
curl -o .claude/skills/poes/SKILL.md https://raw.githubusercontent.com/kurrent-io/poes/main/.claude/skills/poes/SKILL.md
```

Then register it in your project's `.claude/settings.json`:

```json
{
  "skills": [
    ".claude/skills/poes/SKILL.md"
  ]
}
```

The skill file contains the complete API reference, 6 ready-to-use templates, and common mistakes. Any agent that reads it can write and verify POES aggregates autonomously.

### 3. Optional dependencies

```bash
pip install poes[dev]        # pytest for running tests
pip install poes[kurrentdb]  # KurrentDB persistence support
```

### Requirements

- Python 3.10+
- [hypothesis](https://hypothesis.readthedocs.io/) (installed automatically)

## Quick Start

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
    .with_invariant("BalanceNonNegative", lambda s: s.balance >= 0)
    .with_invariant("BalanceBounded", lambda s: s.balance <= 1000)
    .with_parametric_transition("Deposit",
        params={"amount": st.integers(1, 500)},
        guard=lambda s, amount: s.is_open and s.balance + amount <= 1000,
        apply=lambda s, amount: replace(s, balance=s.balance + amount),
        ensures=lambda before, after, amount: after.balance == before.balance + amount)
    .with_transition("Withdraw",
        guard=lambda s: s.is_open and s.balance >= 50,
        apply=lambda s: replace(s, balance=s.balance - 50),
        ensures=lambda before, after: after.balance == before.balance - 50)
    .run()
)

assert result.all_passed
```

Output:

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  VERIFYING: BankAccount
╚══════════════════════════════════════════════════════════════════════════════╝

  ┌─ Property Testing ──────────────────────────────────────────────────────────┐
  │  ✓ Deposit/BalanceNonNegative  (500 examples)
  │  ✓ Deposit/BalanceBounded  (500 examples)
  │  ✓ Withdraw/BalanceNonNegative  (500 examples)
  │  ✓ Withdraw/BalanceBounded  (500 examples)
  └─────────────────────────────────────────────────────────────────────────────┘

  ┌─ State Exploration ─────────────────────────────────────────────────────────┐
  │  ✓ All 22 reachable states safe
  └─────────────────────────────────────────────────────────────────────────────┘

  ✓ VERIFIED: All 4 proofs passed, 22 states explored (150ms)
```

## What It Verifies

POES proves four categories of properties about your event-sourced aggregates:

1. **Property Testing** — Hypothesis generates random states and checks that every transition preserves every invariant
2. **State Space Safety** — BFS explores all reachable states from the initial state and verifies all invariants hold
3. **Temporal Properties** — Liveness checks like "eventually", "leads-to", and "always-eventually" using SCC analysis
4. **Persistence Verification** — Replays production events from KurrentDB and checks every intermediate state against all invariants

## Built for Coding Agents

POES is designed so AI coding agents can verify the code they generate. An agent produces an event-sourced aggregate and runs POES verification in the same step — the result is deterministic and machine-readable. When a future change breaks a previously proven property, the agent gets a minimal counterexample showing exactly what broke.

## API Overview

```python
from poes import Check, FrozenMap

builder = (
    Check.define("Name", StateClass)
    .with_initial(initial_state)
    .with_field("field", strategy)
    .with_map_field("map_field", keys_strat, values_strat)
    .with_invariant("Name", predicate)
    .with_transition("Event", guard, apply, ensures)
    .with_parametric_transition("Event", params, guard, apply, ensures)
    .with_eventually("Name", predicate)
    .with_leads_to("Name", trigger, response)
    .with_always_eventually("Name", predicate)
    .expect_transitions(count)
    .with_max_examples(n)
)

result = builder.run()                          # Run verification
builder.generate_proof_of_work(path="proof.md") # Generate proof document
```

See the [skill file](https://github.com/kurrent-io/poes/blob/main/.claude/skills/poes/SKILL.md) for full API reference, templates, and common mistakes.

## Samples

| Sample | Description |
|--------|-------------|
| [bank_account.py](https://github.com/kurrent-io/poes/blob/main/samples/bank_account.py) | Deposits, withdrawals, balance bounds |
| [gambling_wallet.py](https://github.com/kurrent-io/poes/blob/main/samples/gambling_wallet.py) | Casino betting with active bet tracking |
| [inventory.py](https://github.com/kurrent-io/poes/blob/main/samples/inventory.py) | Warehouse stock with reservations |
| [order_book.py](https://github.com/kurrent-io/poes/blob/main/samples/order_book.py) | Map-based state using FrozenMap |
| [gift_card.py](https://github.com/kurrent-io/poes/blob/main/samples/gift_card.py) | Full KurrentDB integration |
| [hotel_reservation.py](https://github.com/kurrent-io/poes/blob/main/samples/hotel_reservation.py) | State diagram with temporal properties |

Run a sample:

```bash
python samples/bank_account.py
```

## Project Structure

```
src/poes/
├── __init__.py            # Package exports
├── check.py               # Check.define fluent API
├── frozen_map.py          # FrozenMap — hashable immutable dict
├── hypothesis_bridge.py   # Hypothesis property-based testing
├── explorer.py            # BFS state exploration
├── temporal.py            # SCC-based liveness checking
├── persistence_check.py   # Production data verification
├── repository.py          # KurrentDB persistence (Repository pattern)
└── docgen.py              # Markdown proof document generation
```

## License

MIT
