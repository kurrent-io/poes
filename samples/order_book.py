"""Order Book — verified event-sourced aggregate with dict-based full state."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, replace

import hypothesis.strategies as st

from poes import Check


# ============ FULL STATE (with dict) ============

@dataclass(frozen=True)
class Order:
    item: str
    quantity: int
    unit_price: int


@dataclass
class State:
    orders: dict[str, Order]
    total_value: int
    total_items: int
    capacity: int


# ============ SCALAR STATE FOR VERIFICATION ============

@dataclass(frozen=True)
class VerifyState:
    total_value: int
    total_items: int
    capacity: int


# ============ INITIAL STATE ============

def make_initial() -> State:
    return State(orders={}, total_value=0, total_items=0, capacity=100)


VERIFY_INITIAL = VerifyState(total_value=0, total_items=0, capacity=100)


# ============ APPLY FUNCTION ============

def apply_event(state: State, event: dict) -> State:
    t = event["type"]
    if t == "OrderPlaced":
        order = Order(item=event["item"], quantity=event["quantity"], unit_price=event["unitPrice"])
        state.orders[event["orderId"]] = order
        state.total_value += order.quantity * order.unit_price
        state.total_items += order.quantity
    elif t == "OrderFulfilled":
        order = state.orders.pop(event["orderId"], None)
        if order:
            state.total_items -= order.quantity
    elif t == "OrderCancelled":
        order = state.orders.pop(event["orderId"], None)
        if order:
            state.total_value -= order.quantity * order.unit_price
            state.total_items -= order.quantity
    elif t == "CapacitySet":
        state.capacity = event["capacity"]
    return state


# ============ VERIFICATION ============

def verify():
    return (
        Check.define("OrderBook", VerifyState)
        .with_initial(VERIFY_INITIAL)
        .with_field("total_value", st.integers(0, 1000))
        .with_field("total_items", st.integers(0, 100))
        .with_field("capacity", st.integers(0, 100))
        .with_invariant("TotalValueNonNegative", lambda s: s.total_value >= 0)
        .with_invariant("TotalItemsNonNegative", lambda s: s.total_items >= 0)
        .with_invariant("ItemsWithinCapacity", lambda s: s.total_items <= s.capacity)
        .with_transition(
            "PlaceOrder",
            guard=lambda s: s.total_items + 5 <= s.capacity,
            apply=lambda s: replace(s, total_value=s.total_value + 50, total_items=s.total_items + 5),
            ensures=lambda before, after: (
                after.total_value == before.total_value + 50
                and after.total_items == before.total_items + 5
            ),
        )
        .with_transition(
            "FulfillOrder",
            guard=lambda s: s.total_items >= 5,
            apply=lambda s: replace(s, total_items=s.total_items - 5),
            ensures=lambda before, after: (
                after.total_items == before.total_items - 5
                and after.total_value == before.total_value
            ),
        )
        .with_transition(
            "CancelOrder",
            guard=lambda s: s.total_items >= 5 and s.total_value >= 50,
            apply=lambda s: replace(s, total_value=s.total_value - 50, total_items=s.total_items - 5),
            ensures=lambda before, after: (
                after.total_value == before.total_value - 50
                and after.total_items == before.total_items - 5
            ),
        )
        .with_transition(
            "SetCapacity",
            guard=lambda s: True,
            apply=lambda s: replace(s, capacity=max(s.total_items, 100)),
            ensures=lambda before, after: after.capacity == max(before.total_items, 100),
        )
        .with_eventually("CanPlaceOrders", lambda s: s.total_items + 5 <= s.capacity)
        .with_always_eventually("CanClear", lambda s: s.total_items == 0)
        .run()
    )


# ============ MAIN ============

def main():
    if "--verify" in sys.argv:
        result = verify()
        if result.all_passed:
            print("All properties verified! The order book is proven safe.")
            return 0
        else:
            print("Verification failed! See above for details.")
            return 1
    else:
        state = make_initial()
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                state = apply_event(state, event)
            except (json.JSONDecodeError, KeyError):
                pass
        output = {
            "orders": {k: {"item": v.item, "quantity": v.quantity, "unitPrice": v.unit_price}
                       for k, v in state.orders.items()},
            "totalValue": state.total_value,
            "totalItems": state.total_items,
            "capacity": state.capacity,
        }
        print(json.dumps(output))
        return 0


if __name__ == "__main__":
    sys.exit(main())
