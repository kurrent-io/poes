"""Gambling Wallet — verified event-sourced aggregate."""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace

import hypothesis.strategies as st

from poes import Check


@dataclass(frozen=True)
class Wallet:
    balance: int
    active_bet: int
    total_won: int
    total_lost: int


def main():
    result = (
        Check.define("GamblingWallet", Wallet)
        .with_initial(Wallet(balance=100, active_bet=0, total_won=0, total_lost=0))
        .with_field("balance", st.integers(0, 100))
        .with_field("active_bet", st.integers(0, 10))
        .with_field("total_won", st.integers(0, 10))
        .with_field("total_lost", st.integers(0, 10))
        .with_invariant("BalanceNonNegative", lambda s: s.balance >= 0)
        .with_invariant("ActiveBetNonNegative", lambda s: s.active_bet >= 0)
        .with_invariant("TotalWonNonNegative", lambda s: s.total_won >= 0)
        .with_invariant("TotalLostNonNegative", lambda s: s.total_lost >= 0)
        # Deposit
        .with_transition(
            "Deposit",
            guard=lambda s: True,
            apply=lambda s: replace(s, balance=s.balance + 10),
            ensures=lambda before, after: after.balance == before.balance + 10,
        )
        # Withdraw
        .with_transition(
            "Withdraw",
            guard=lambda s: s.balance >= 10,
            apply=lambda s: replace(s, balance=s.balance - 10),
            ensures=lambda before, after: after.balance == before.balance - 10,
        )
        # PlaceBet
        .with_transition(
            "PlaceBet",
            guard=lambda s: s.balance >= 10 and s.active_bet == 0,
            apply=lambda s: replace(s, balance=s.balance - 10, active_bet=s.active_bet + 10),
            ensures=lambda before, after: (
                after.balance == before.balance - 10
                and after.active_bet == before.active_bet + 10
            ),
        )
        # WinBet
        .with_transition(
            "WinBet",
            guard=lambda s: s.active_bet >= 10,
            apply=lambda s: replace(
                s,
                active_bet=s.active_bet - 10,
                balance=s.balance + 20,
                total_won=s.total_won + 10,
            ),
            ensures=lambda before, after: (
                after.active_bet == before.active_bet - 10
                and after.balance == before.balance + 20
                and after.total_won == before.total_won + 10
            ),
        )
        # LoseBet
        .with_transition(
            "LoseBet",
            guard=lambda s: s.active_bet >= 10,
            apply=lambda s: replace(
                s,
                active_bet=s.active_bet - 10,
                total_lost=s.total_lost + 10,
            ),
            ensures=lambda before, after: (
                after.active_bet == before.active_bet - 10
                and after.total_lost == before.total_lost + 10
            ),
        )
        # CancelBet
        .with_transition(
            "CancelBet",
            guard=lambda s: s.active_bet >= 10,
            apply=lambda s: replace(
                s,
                active_bet=s.active_bet - 10,
                balance=s.balance + 10,
            ),
            ensures=lambda before, after: (
                after.active_bet == before.active_bet - 10
                and after.balance == before.balance + 10
            ),
        )
        # Temporal properties
        .with_eventually("BetsCanSettle", lambda s: s.active_bet == 0)
        .with_leads_to(
            "BetSettles",
            trigger=lambda s: s.active_bet > 0,
            response=lambda s: s.active_bet == 0,
        )
        .with_always_eventually("CanBet", lambda s: s.balance >= 10 and s.active_bet == 0)
        .run()
    )

    if result.all_passed:
        print("All properties verified! The gambling wallet is proven safe.")
        return 0
    else:
        print("Verification failed! See above for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
