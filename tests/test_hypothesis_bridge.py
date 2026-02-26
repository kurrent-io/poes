"""Test Hypothesis integration."""

from dataclasses import dataclass, replace

import hypothesis.strategies as st

from poes import Check


@dataclass(frozen=True)
class BankAccount:
    balance: int
    is_open: bool


def make_builder():
    return (
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
    )


def test_state_machine_generation():
    from poes.hypothesis_bridge import check_to_state_machine

    builder = make_builder()
    machine_cls = check_to_state_machine(builder)
    assert machine_cls is not None
    assert issubclass(machine_cls, object)


def test_hypothesis_finds_no_counterexample():
    from poes.hypothesis_bridge import run_hypothesis_test
    from hypothesis import settings

    builder = make_builder()
    run_hypothesis_test(builder, settings=settings(max_examples=50, stateful_step_count=20))


def test_verify_properties():
    from poes.hypothesis_bridge import verify_properties, PropertyProved

    builder = make_builder()
    results = verify_properties(
        BankAccount,
        builder._fields,
        builder._transitions,
        builder._invariants,
        max_examples=100,
    )
    assert len(results) > 0
    assert all(isinstance(r, PropertyProved) for r in results)
