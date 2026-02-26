# POES — Proof-Oriented Event Sourcing

## Setup

Install the package and dev dependencies:

```bash
pip install -e ".[dev]"
```

For KurrentDB persistence support, install the KurrentDB coding agent skills from https://github.com/kurrent-io/coding-agent-skills/ — they include Docker setup, client API reference, and persistence patterns:

```bash
pip install -e ".[kurrentdb]"
docker compose up -d
```

## Skill

This repo includes a Claude Code skill at `.claude/skills/poes/SKILL.md`. It teaches you the full POES API — state definitions, invariants, transitions, temporal properties, and persistence verification.

Read and follow `.claude/skills/poes/SKILL.md` when working with POES aggregates.

## Running Tests

```bash
pytest tests/
```

## Running Samples

```bash
python samples/bank_account.py
python samples/inventory.py
python samples/gambling_wallet.py
python samples/order_book.py
python samples/hotel_reservation.py   # requires KurrentDB
python samples/gift_card.py           # requires KurrentDB
```
