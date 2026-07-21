# POES — Proof-Oriented Event Sourcing

Shorten the verification gap in AI-generated code. POES is a Python library that verifies
event-sourced aggregates using property-based testing, exhaustive state exploration, and temporal
property checking — no external SMT solvers or model checkers required. It's a **coding-agent-first**
framework: an agent writes an aggregate and proves it in the same step, and the proof-of-work is for
the human.

> Experimental, written by Claude Code — not recommended for production. Feedback and pull requests
> (including from coding agents) welcome. Philosophy:
> <https://www.kurrent.io/blog/proof-oriented-event-sourcing/>

## See it: enforced POES in the pi harness

![pi + poes-enforce: /poes builds and proves a Counter aggregate](docs/poes-pi-demo.gif)

*A single `/poes` request in [pi](https://pi.dev): `deepseek-v4-pro` writes a Counter aggregate, the
`poes-enforce` package validates every write, and the task only completes once the proof is green —
6 proofs, 101 states explored.*

## Pi harness — `poes-enforce`

[`poes-enforce`](.pi/extensions/poes-enforce/) is a [pi](https://pi.dev) package that makes a coding
session **enforce** POES. It steers the model to write every event-sourced aggregate with a proof,
validates each aggregate the moment it's written, and drives a **repair loop** — feeding the
counterexample back into the model's own agent loop — until the proof passes. An aggregate never
silently ships unproven.

### Install

```bash
pi install git:github.com/kurrent-io/poes   # or https://github.com/kurrent-io/poes
pip install poes                            # the library the validator runs
```

`pi install` records the package in `settings.json` and auto-loads its three resources — the
enforcement **extension**, the bundled POES **skill** (`/skill:poes`), and the `/poes` **workflow
command**. Add `-l` to install into the project (`.pi/`) instead of globally, or `@<tag>` to pin a
released version.

### Use it

- **`/poes <describe an aggregate>`** — runs the full *generate → verify → repair-until-proven*
  workflow (this is what the demo above shows).
- Or just write event-sourced code: any aggregate the model writes is validated on the spot, and in
  `block` mode (the default) an unproven one is repaired in-loop before the turn can finish.
- Helpers: `/poes-status`, `/poes-verify [file]`, `/poes-enforce off|warn|block`, `/poes-skill`.

Configuration and internals: [`.pi/extensions/poes-enforce/README.md`](.pi/extensions/poes-enforce/README.md).

### Other coding agents

For Claude Code (or Cursor, Windsurf, …) that don't run pi, copy the skill so the agent learns the
POES API and can verify autonomously:

```bash
mkdir -p .claude/skills/poes
curl -o .claude/skills/poes/SKILL.md \
  https://raw.githubusercontent.com/kurrent-io/poes/main/.claude/skills/poes/SKILL.md
```

Then register it in `.claude/settings.json`:

```json
{ "skills": [".claude/skills/poes/SKILL.md"] }
```

---

## The library

**Write your domain logic once. Verify it automatically.**

### Install

```bash
pip install poes                 # or, from source: pip install -e .
pip install "poes[dev]"          # + pytest
pip install "poes[kurrentdb]"    # + KurrentDB persistence support
```

Requirements: Python 3.10+ and [hypothesis](https://hypothesis.readthedocs.io/) (installed
automatically).

### Quick start

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

```
  ✓ VERIFIED: All 4 proofs passed, 22 states explored (150ms)
```

### What it verifies

1. **Property testing** — Hypothesis generates random states and checks every transition preserves
   every invariant.
2. **State-space safety** — BFS explores all reachable states from the initial state and verifies
   all invariants hold.
3. **Temporal properties** — liveness checks (`eventually`, `leads-to`, `always-eventually`) via SCC
   analysis.
4. **Persistence verification** — replays production events from KurrentDB and checks every
   intermediate state against all invariants.

### API overview

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

See the [skill file](.claude/skills/poes/SKILL.md) for the full API reference, templates, and common
mistakes.

### Samples

| Sample | Description |
|--------|-------------|
| [bank_account.py](samples/bank_account.py) | Deposits, withdrawals, balance bounds |
| [gambling_wallet.py](samples/gambling_wallet.py) | Casino betting with active bet tracking |
| [inventory.py](samples/inventory.py) | Warehouse stock with reservations |
| [order_book.py](samples/order_book.py) | Map-based state using FrozenMap |
| [gift_card.py](samples/gift_card.py) | Full KurrentDB integration |
| [hotel_reservation.py](samples/hotel_reservation.py) | State diagram with temporal properties |

```bash
python samples/bank_account.py
```

### Project structure

```
src/poes/
├── check.py               # Check.define fluent API (orchestrator)
├── frozen_map.py          # FrozenMap — hashable immutable dict
├── hypothesis_bridge.py   # property-based testing
├── explorer.py            # BFS state exploration
├── temporal.py            # SCC-based liveness checking
├── persistence_check.py   # production data verification
├── repository.py          # KurrentDB persistence (Repository pattern)
└── docgen.py              # Markdown proof-of-work generation

.pi/extensions/poes-enforce/  # the pi package (extension + skill + /poes workflow + validator)
```

## License

MIT
