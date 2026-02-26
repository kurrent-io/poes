"""Warehouse Inventory — verified event-sourced aggregate."""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace

import hypothesis.strategies as st

from poes import Check


@dataclass(frozen=True)
class Inventory:
    quantity: int
    reserved: int
    reordered: int


def available(s: Inventory) -> int:
    return s.quantity - s.reserved


def main():
    result = (
        Check.define("Inventory", Inventory)
        .with_initial(Inventory(quantity=50, reserved=0, reordered=0))
        .with_field("quantity", st.integers(0, 50))
        .with_field("reserved", st.integers(0, 10))
        .with_field("reordered", st.integers(0, 50))
        .with_invariant("QuantityNonNegative", lambda s: s.quantity >= 0)
        .with_invariant("ReservedNonNegative", lambda s: s.reserved >= 0)
        .with_invariant("ReorderedNonNegative", lambda s: s.reordered >= 0)
        .with_invariant("ReservedNotExceedQuantity", lambda s: s.reserved <= s.quantity)
        # ReceiveItems
        .with_transition(
            "ReceiveItems",
            guard=lambda s: True,
            apply=lambda s: replace(s, quantity=s.quantity + 10),
            ensures=lambda before, after: after.quantity == before.quantity + 10,
        )
        # ShipItems
        .with_transition(
            "ShipItems",
            guard=lambda s: available(s) >= 10,
            apply=lambda s: replace(s, quantity=s.quantity - 10),
            ensures=lambda before, after: after.quantity == before.quantity - 10,
        )
        # ReserveItems
        .with_transition(
            "ReserveItems",
            guard=lambda s: available(s) >= 5,
            apply=lambda s: replace(s, reserved=s.reserved + 5),
            ensures=lambda before, after: after.reserved == before.reserved + 5,
        )
        # ReleaseReservation
        .with_transition(
            "ReleaseReservation",
            guard=lambda s: s.reserved >= 5,
            apply=lambda s: replace(s, reserved=s.reserved - 5),
            ensures=lambda before, after: after.reserved == before.reserved - 5,
        )
        # FulfillReservation
        .with_transition(
            "FulfillReservation",
            guard=lambda s: s.reserved >= 5 and s.quantity >= 5,
            apply=lambda s: replace(s, quantity=s.quantity - 5, reserved=s.reserved - 5),
            ensures=lambda before, after: (
                after.quantity == before.quantity - 5
                and after.reserved == before.reserved - 5
            ),
        )
        # PlaceReorder
        .with_transition(
            "PlaceReorder",
            guard=lambda s: s.quantity < 20,
            apply=lambda s: replace(s, reordered=s.reordered + 50),
            ensures=lambda before, after: after.reordered == before.reordered + 50,
        )
        # ReceiveReorder
        .with_transition(
            "ReceiveReorder",
            guard=lambda s: s.reordered >= 50,
            apply=lambda s: replace(s, quantity=s.quantity + 50, reordered=s.reordered - 50),
            ensures=lambda before, after: (
                after.quantity == before.quantity + 50
                and after.reordered == before.reordered - 50
            ),
        )
        # Temporal properties
        .with_eventually("ShippingPossible", lambda s: available(s) >= 10)
        .with_always_eventually("CanClear", lambda s: s.reserved == 0)
        .run()
    )

    if result.all_passed:
        print("All properties verified! The inventory system is proven safe.")
        return 0
    else:
        print("Verification failed! See above for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
