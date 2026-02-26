"""Bank Account — verified event-sourced aggregate."""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from pathlib import Path

import hypothesis.strategies as st

from poes import Check


@dataclass(frozen=True)
class BankAccount:
    balance: int
    is_open: bool


def main():
    builder = (
        Check.define("BankAccount", BankAccount)
        .with_initial(BankAccount(balance=0, is_open=True))
        .with_field("balance", st.integers(0, 1000))
        .with_field("is_open", st.booleans())
        .with_invariant("BalanceNonNegative", lambda s: s.balance >= 0)
        .with_invariant("BalanceBounded", lambda s: s.balance <= 1000)
        # Deposit (parametric — Hypothesis generates amount)
        .with_parametric_transition(
            "Deposit",
            params={"amount": st.integers(1, 500)},
            guard=lambda s, amount: s.is_open and s.balance + amount <= 1000,
            apply=lambda s, amount: replace(s, balance=s.balance + amount),
            ensures=lambda before, after, amount: after.balance == before.balance + amount,
        )
        # Withdraw
        .with_transition(
            "Withdraw",
            guard=lambda s: s.is_open and s.balance >= 50,
            apply=lambda s: replace(s, balance=s.balance - 50),
            ensures=lambda before, after: after.balance == before.balance - 50,
        )
        # Close
        .with_transition(
            "Close",
            guard=lambda s: s.balance == 0,
            apply=lambda s: replace(s, is_open=False),
            ensures=lambda before, after: not after.is_open,
        )
        # Reopen
        .with_transition(
            "Reopen",
            guard=lambda s: not s.is_open,
            apply=lambda s: replace(s, is_open=True),
            ensures=lambda before, after: after.is_open,
        )
        # Temporal properties
        .with_eventually("CanClose", lambda s: not s.is_open)
        .with_leads_to(
            "BalanceCanDrain",
            trigger=lambda s: s.is_open and s.balance > 0,
            response=lambda s: s.balance == 0,
        )
        .with_always_eventually("CanDeposit", lambda s: s.is_open)
    )

    # Generate proof-of-work document — save to file alongside this script
    proof_path = Path(__file__).with_name("bank_account_proof.md")
    builder.generate_proof_of_work(path=proof_path)
    print(f"Proof of work saved to {proof_path}")

    result = builder.run()

    if result.all_passed:
        print("All properties verified! The bank account is proven safe.")
        return 0
    else:
        print("Verification failed! See above for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
