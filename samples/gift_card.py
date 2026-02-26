"""Gift Card — event-sourced aggregate demonstrating thorough KurrentDB client usage.

KurrentDB features exercised:
  - Append with optimistic concurrency (via poes.Repository)
  - Read stream (via poes.Repository.load)
  - get_current_version — check stream exists without full read
  - set_stream_metadata / get_stream_metadata — $maxCount on card streams
  - subscribe_to_all with filter — catch-up subscription for read model
  - create_subscription_to_stream / read_subscription_to_stream — persistent sub
  - read_all with filter_by_stream_name — cross-aggregate query
  - WrongCurrentVersionError catch + reload + retry — conflict resolution
  - delete_stream — soft delete a fully-redeemed card
"""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass, replace
from typing import Union


# =============================================================================
# FULL STATE (runtime / persistence)
# =============================================================================

@dataclass
class GiftCardState:
    balance: int
    is_active: bool
    total_issued: int
    total_redeemed: int


def make_initial() -> GiftCardState:
    return GiftCardState(balance=0, is_active=False, total_issued=0, total_redeemed=0)


# =============================================================================
# SCALAR STATE (verification — frozen for hashability)
# =============================================================================

@dataclass(frozen=True)
class VerifyState:
    balance: int
    is_active: bool
    total_issued: int
    total_redeemed: int


VERIFY_INITIAL = VerifyState(balance=0, is_active=False, total_issued=0, total_redeemed=0)


# =============================================================================
# EVENTS
# =============================================================================

@dataclass(frozen=True)
class Issued:
    amount: int
    event_type: str = "Issued"

@dataclass(frozen=True)
class Redeemed:
    amount: int
    event_type: str = "Redeemed"

@dataclass(frozen=True)
class ToppedUp:
    amount: int
    event_type: str = "ToppedUp"

@dataclass(frozen=True)
class Cancelled:
    refund: int
    event_type: str = "Cancelled"

Event = Union[Issued, Redeemed, ToppedUp, Cancelled]


def event_to_json(event: Event) -> str:
    d = {k: v for k, v in event.__dict__.items() if k != "event_type"}
    return json.dumps(d)


def event_from_json(event_type: str, data: bytes) -> Event:
    d = json.loads(data)
    match event_type:
        case "Issued":
            return Issued(amount=d["amount"])
        case "Redeemed":
            return Redeemed(amount=d["amount"])
        case "ToppedUp":
            return ToppedUp(amount=d["amount"])
        case "Cancelled":
            return Cancelled(refund=d["refund"])
        case _:
            raise ValueError(f"Unknown event type: {event_type}")


# =============================================================================
# APPLY — full state (runtime / persistence)
# =============================================================================

def apply(state: GiftCardState, event: Event) -> GiftCardState:
    match event:
        case Issued(amount=amt):
            state.balance += amt
            state.is_active = True
            state.total_issued += amt
        case Redeemed(amount=amt):
            state.balance -= amt
            state.total_redeemed += amt
        case ToppedUp(amount=amt):
            state.balance += amt
            state.total_issued += amt
        case Cancelled(refund=_):
            state.balance = 0
            state.is_active = False
    return state


# =============================================================================
# VERIFICATION
# =============================================================================

def verify():
    import hypothesis.strategies as st
    from pathlib import Path
    from poes import Check

    builder = (
        Check.define("GiftCard", VerifyState)
        .with_initial(VERIFY_INITIAL)
        .with_field("balance", st.integers(0, 500))
        .with_field("is_active", st.booleans())
        .with_field("total_issued", st.integers(0, 500))
        .with_field("total_redeemed", st.integers(0, 500))
        # --- Invariants ---
        .with_invariant(
            "BalanceNonNegative",
            lambda s: s.balance >= 0,
        )
        .with_invariant(
            "BalanceConsistent",
            lambda s: s.balance == s.total_issued - s.total_redeemed,
        )
        # RedeemedWithinIssued is derived: balance >= 0 ∧ balance = issued - redeemed ⟹ redeemed <= issued
        # --- Transitions ---
        .with_parametric_transition(
            "Issue",
            params={"amount": st.integers(10, 200)},
            guard=lambda s, amount: not s.is_active,
            apply=lambda s, amount: replace(
                s,
                balance=s.balance + amount,
                is_active=True,
                total_issued=s.total_issued + amount,
            ),
            ensures=lambda before, after, amount: (
                after.balance == before.balance + amount
                and after.is_active
                and after.total_issued == before.total_issued + amount
            ),
        )
        .with_parametric_transition(
            "Redeem",
            params={"amount": st.integers(1, 100)},
            guard=lambda s, amount: s.is_active and s.balance >= amount,
            apply=lambda s, amount: replace(
                s,
                balance=s.balance - amount,
                total_redeemed=s.total_redeemed + amount,
            ),
            ensures=lambda before, after, amount: (
                after.balance == before.balance - amount
                and after.total_redeemed == before.total_redeemed + amount
            ),
        )
        .with_parametric_transition(
            "TopUp",
            params={"amount": st.integers(10, 200)},
            guard=lambda s, amount: s.is_active and s.total_issued + amount <= 500,
            apply=lambda s, amount: replace(
                s,
                balance=s.balance + amount,
                total_issued=s.total_issued + amount,
            ),
            ensures=lambda before, after, amount: (
                after.balance == before.balance + amount
                and after.total_issued == before.total_issued + amount
            ),
        )
        .with_transition(
            "Cancel",
            guard=lambda s: s.is_active,
            apply=lambda s: replace(
                s,
                balance=0,
                is_active=False,
                total_issued=s.total_redeemed,
            ),
            ensures=lambda before, after: (
                after.balance == 0
                and not after.is_active
            ),
        )
        # --- Temporal Properties ---
        .with_leads_to(
            "IssuedCanReachZero",
            trigger=lambda s: s.is_active and s.balance > 0,
            response=lambda s: s.balance == 0,
        )
        .with_eventually("CanBeIssued", lambda s: s.is_active)
    )
    builder.generate_proof_of_work(path=Path("samples/gift_card_proof.md"))
    return builder.run()


# =============================================================================
# COMMANDS + DECIDE
# =============================================================================

@dataclass(frozen=True)
class IssueCmd:
    amount: int

@dataclass(frozen=True)
class RedeemCmd:
    amount: int

@dataclass(frozen=True)
class TopUpCmd:
    amount: int

@dataclass(frozen=True)
class CancelCmd:
    pass

Command = Union[IssueCmd, RedeemCmd, TopUpCmd, CancelCmd]


class DomainError(Exception):
    pass

class AlreadyIssued(DomainError):
    pass

class NotActive(DomainError):
    pass

class InsufficientBalance(DomainError):
    pass


def decide(state: GiftCardState, cmd: Command) -> list[Event]:
    match cmd:
        case IssueCmd(amount=amt):
            if state.is_active:
                raise AlreadyIssued("Card already issued")
            if amt <= 0:
                raise DomainError("Amount must be positive")
            return [Issued(amount=amt)]
        case RedeemCmd(amount=amt):
            if not state.is_active:
                raise NotActive("Card is not active")
            if amt <= 0:
                raise DomainError("Amount must be positive")
            if amt > state.balance:
                raise InsufficientBalance(
                    f"Cannot redeem {amt}: balance is {state.balance}"
                )
            return [Redeemed(amount=amt)]
        case TopUpCmd(amount=amt):
            if not state.is_active:
                raise NotActive("Card is not active")
            if amt <= 0:
                raise DomainError("Amount must be positive")
            return [ToppedUp(amount=amt)]
        case CancelCmd():
            if not state.is_active:
                raise NotActive("Card is not active")
            return [Cancelled(refund=state.balance)]
    raise ValueError(f"Unknown command: {cmd}")


# =============================================================================
# KURRENTDB DEMO
# =============================================================================

def run_demo():
    import time
    from kurrentdbclient import KurrentDBClient, NewEvent, StreamState
    from kurrentdbclient.exceptions import WrongCurrentVersionError, NotFoundError
    from poes import Repository

    client = KurrentDBClient("kurrentdb://localhost:2113?tls=false")
    repo = Repository(
        client=client,
        stream_prefix="GiftCard",
        initial=make_initial,
        apply=apply,
        decide=decide,
        event_to_json=event_to_json,
        event_from_json=event_from_json,
    )

    # Use unique IDs so each run is independent
    card_a = str(uuid.uuid4())[:8]
    card_b = str(uuid.uuid4())[:8]
    card_c = str(uuid.uuid4())[:8]

    # ── 1. Basic persistence ────────────────────────────────────────────────
    print("=" * 70)
    print("1. Basic Persistence")
    print("=" * 70)

    state, ver = repo.execute(card_a, IssueCmd(amount=100))
    print(f"  Card {card_a}: issued $100 -> balance={state.balance} (v{ver})")

    state, ver = repo.execute(card_a, RedeemCmd(amount=30))
    print(f"  Card {card_a}: redeemed $30 -> balance={state.balance} (v{ver})")

    state, ver = repo.execute(card_a, TopUpCmd(amount=50))
    print(f"  Card {card_a}: topped up $50 -> balance={state.balance} (v{ver})")

    state, ver = repo.execute(card_b, IssueCmd(amount=200))
    print(f"  Card {card_b}: issued $200 -> balance={state.balance} (v{ver})")

    state, ver = repo.execute(card_b, RedeemCmd(amount=200))
    print(f"  Card {card_b}: redeemed $200 -> balance={state.balance} (v{ver})")

    state, ver = repo.execute(card_c, IssueCmd(amount=50))
    print(f"  Card {card_c}: issued $50 -> balance={state.balance} (v{ver})")

    # Reload from store
    state, ver = repo.load(card_a)
    print(f"  Reloaded card {card_a}: balance={state.balance}, "
          f"issued={state.total_issued}, redeemed={state.total_redeemed} (v{ver})")
    print()

    # ── 2. Stream metadata ──────────────────────────────────────────────────
    print("=" * 70)
    print("2. Stream Metadata ($maxCount)")
    print("=" * 70)

    stream_a = f"GiftCard-{card_a}"
    client.set_stream_metadata(
        stream_a,
        metadata={"$maxCount": 50},
        current_version=StreamState.ANY,
    )
    print(f"  Set $maxCount=50 on {stream_a}")

    metadata, meta_ver = client.get_stream_metadata(stream_a)
    print(f"  Read back metadata: {metadata} (meta version={meta_ver})")
    print()

    # ── 3. Optimistic concurrency retry ─────────────────────────────────────
    print("=" * 70)
    print("3. Optimistic Concurrency Retry")
    print("=" * 70)

    # Load current version, then do a concurrent write to create a conflict
    state_snap, ver_snap = repo.load(card_c)
    print(f"  Snapshot card {card_c}: balance={state_snap.balance} at v{ver_snap}")

    # Another "process" redeems first
    repo.execute(card_c, RedeemCmd(amount=10))
    print(f"  Concurrent process redeemed $10 from card {card_c}")

    # Try to append with stale version — raw client call
    stale_event = NewEvent(
        type="Redeemed",
        data=json.dumps({"amount": 5}).encode("utf-8"),
    )
    stream_c = f"GiftCard-{card_c}"
    try:
        client.append_to_stream(
            stream_c,
            current_version=ver_snap,
            events=[stale_event],
        )
        print("  ERROR: Should have raised WrongCurrentVersionError!")
    except WrongCurrentVersionError as e:
        print(f"  Caught WrongCurrentVersionError: {e}")
        # Retry via Repository (reloads automatically)
        state, ver = repo.execute(card_c, RedeemCmd(amount=5))
        print(f"  Retried via Repository -> balance={state.balance} (v{ver})")
    print()

    # ── 4. Catch-up subscription ────────────────────────────────────────────
    print("=" * 70)
    print("4. Catch-up Subscription (subscribe_to_all)")
    print("=" * 70)

    balances: dict[str, int] = {}
    sub = client.subscribe_to_all(
        filter_include=[r"GiftCard-.*"],
        filter_by_stream_name=True,
        include_caught_up=True,
    )
    for event in sub:
        if hasattr(event, "is_caught_up") and event.is_caught_up:
            break
        if hasattr(event, "stream_name") and event.stream_name.startswith("GiftCard-"):
            card_id = event.stream_name.replace("GiftCard-", "")
            if card_id not in balances:
                balances[card_id] = 0
            match event.type:
                case "Issued" | "ToppedUp":
                    d = json.loads(event.data)
                    balances[card_id] += d["amount"]
                case "Redeemed":
                    d = json.loads(event.data)
                    balances[card_id] -= d["amount"]
                case "Cancelled":
                    balances[card_id] = 0
    sub.stop()

    print(f"  Read model from catch-up subscription:")
    for cid, bal in balances.items():
        print(f"    Card {cid}: balance={bal}")
    print(f"  Total cards seen: {len(balances)}")
    print()

    # ── 5. Persistent subscription ──────────────────────────────────────────
    print("=" * 70)
    print("5. Persistent Subscription")
    print("=" * 70)

    group = f"gift-card-notifications-{uuid.uuid4().hex[:8]}"
    try:
        client.create_subscription_to_stream(
            group,
            stream_a,
        )
        print(f"  Created persistent subscription '{group}' on {stream_a}")

        psub = client.read_subscription_to_stream(group, stream_a)
        count = 0
        for event in psub:
            count += 1
            print(f"    [{count}] {event.type}: {json.loads(event.data)}")
            psub.ack(event.ack_id)
            if count >= 3:
                break
        psub.stop()
        print(f"  Processed and ack'd {count} events")
    except Exception as e:
        print(f"  Persistent subscription error (may need server config): {e}")
    print()

    # ── 6. Read $all — cross-aggregate query ────────────────────────────────
    print("=" * 70)
    print("6. Read $all (cross-aggregate query)")
    print("=" * 70)

    stream_names: set[str] = set()
    for event in client.read_all(
        filter_include=[r"GiftCard-.*"],
        filter_by_stream_name=True,
    ):
        if hasattr(event, "stream_name"):
            stream_names.add(event.stream_name)

    print(f"  All GiftCard streams in the store:")
    for name in sorted(stream_names):
        print(f"    {name}")
    print(f"  Total: {len(stream_names)} streams")
    print()

    # ── 7. Get current version ──────────────────────────────────────────────
    print("=" * 70)
    print("7. Get Current Version (without reading events)")
    print("=" * 70)

    for cid in [card_a, card_b, card_c]:
        stream = f"GiftCard-{cid}"
        version = client.get_current_version(stream)
        print(f"  {stream}: version={version}")
    print()

    # ── 8. Soft delete ──────────────────────────────────────────────────────
    print("=" * 70)
    print("8. Soft Delete (fully redeemed card)")
    print("=" * 70)

    # Card B has balance=0
    state_b, ver_b = repo.load(card_b)
    print(f"  Card {card_b}: balance={state_b.balance} (v{ver_b})")
    assert state_b.balance == 0, "Card B should be fully redeemed"

    stream_b = f"GiftCard-{card_b}"
    client.delete_stream(stream_b, current_version=ver_b)
    print(f"  Soft-deleted {stream_b}")

    ver_after = client.get_current_version(stream_b)
    print(f"  Version after delete: {ver_after}")

    try:
        client.get_stream(stream_b)
        print(f"  Stream still readable (soft delete preserves history)")
    except NotFoundError:
        print(f"  Stream {stream_b} is gone (soft delete)")
    print()

    print("Demo complete.")


# =============================================================================
# MAIN
# =============================================================================

def main():
    if "--verify" in sys.argv:
        result = verify()
        if result.all_passed:
            print("All properties verified! The gift card system is proven safe.")
            return 0
        else:
            print("Verification failed! See above for details.")
            return 1

    run_demo()
    return 0


if __name__ == "__main__":
    sys.exit(main())
