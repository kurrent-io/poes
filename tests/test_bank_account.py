"""Test bank account verification produces correct results."""

from dataclasses import dataclass, replace

import hypothesis.strategies as st

from poes import Check


def test_bank_account_verification_passes():
    @dataclass(frozen=True)
    class BankAccount:
        balance: int
        is_open: bool

    result = (
        Check.define("BankAccount", BankAccount)
        .with_initial(BankAccount(balance=0, is_open=True))
        .with_field("balance", st.integers(0, 1000))
        .with_field("is_open", st.booleans())
        .with_invariant("BalanceNonNegative", lambda s: s.balance >= 0)
        .with_invariant("BalanceBounded", lambda s: s.balance <= 1000)
        .with_transition(
            "Deposit",
            guard=lambda s: s.is_open,
            apply=lambda s: replace(s, balance=min(1000, s.balance + 100)),
            ensures=lambda before, after: after.balance == min(1000, before.balance + 100),
        )
        .with_transition(
            "Withdraw",
            guard=lambda s: s.is_open and s.balance >= 50,
            apply=lambda s: replace(s, balance=s.balance - 50),
            ensures=lambda before, after: after.balance == before.balance - 50,
        )
        .with_transition(
            "Close",
            guard=lambda s: s.balance == 0,
            apply=lambda s: replace(s, is_open=False),
            ensures=lambda before, after: not after.is_open,
        )
        .with_transition(
            "Reopen",
            guard=lambda s: not s.is_open,
            apply=lambda s: replace(s, is_open=True),
            ensures=lambda before, after: after.is_open,
        )
        .with_eventually("CanClose", lambda s: not s.is_open)
        .with_leads_to(
            "BalanceCanDrain",
            trigger=lambda s: s.is_open and s.balance > 0,
            response=lambda s: s.balance == 0,
        )
        .with_always_eventually("CanDeposit", lambda s: s.is_open)
        .run()
    )

    assert result.all_passed
    assert result.property_passed
    assert result.exploration_passed
    assert result.temporal_passed
    assert result.states_explored > 0
    assert result.property_proofs > 0
