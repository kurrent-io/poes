"""Test poes.persistence_check with a mocked KurrentDB client."""

import json
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Union
from unittest.mock import MagicMock

import pytest

from poes.persistence_check import (
    PersistenceCheckResult,
    StreamError,
    StreamInvariantViolation,
    StreamVerified,
    verify_persistence,
)


# ---------------------------------------------------------------------------
# Minimal aggregate (same shape as test_repository.py)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AccountState:
    balance: int
    is_open: bool


INITIAL = AccountState(balance=0, is_open=True)


@dataclass(frozen=True)
class Deposited:
    amount: int
    event_type: str = "Deposited"


@dataclass(frozen=True)
class Withdrawn:
    amount: int
    event_type: str = "Withdrawn"


@dataclass(frozen=True)
class Closed:
    event_type: str = "Closed"


Event = Union[Deposited, Withdrawn, Closed]


def apply(state: AccountState, event: Event) -> AccountState:
    match event:
        case Deposited(amount=a):
            return replace(state, balance=state.balance + a)
        case Withdrawn(amount=a):
            return replace(state, balance=state.balance - a)
        case Closed():
            return replace(state, is_open=False)
    return state


def event_to_json(event: Event) -> str:
    d = {k: v for k, v in event.__dict__.items() if k != "event_type"}
    return json.dumps(d)


def event_from_json(event_type: str, data: bytes) -> Event:
    d = json.loads(data)
    match event_type:
        case "Deposited":
            return Deposited(amount=d["amount"])
        case "Withdrawn":
            return Withdrawn(amount=d["amount"])
        case "Closed":
            return Closed()
    raise ValueError(f"Unknown event type: {event_type}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

INVARIANTS = [
    ("BalanceNonNegative", lambda s: s.balance >= 0),
]


def make_record(event: Event, position: int) -> SimpleNamespace:
    """Simulate a KurrentDB read record."""
    return SimpleNamespace(
        type=event.event_type,
        data=event_to_json(event).encode("utf-8"),
        stream_position=position,
    )


def make_all_record(event: Event, stream_name: str) -> SimpleNamespace:
    """Simulate a KurrentDB read_all record (includes stream_name)."""
    return SimpleNamespace(
        type=event.event_type,
        data=event_to_json(event).encode("utf-8"),
        stream_name=stream_name,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSingleStreamPass:
    def test_all_invariants_pass(self):
        records = [
            make_record(Deposited(amount=100), 0),
            make_record(Deposited(amount=50), 1),
            make_record(Withdrawn(amount=30), 2),
        ]
        client = MagicMock()
        client.get_stream.return_value = records

        result = verify_persistence(
            client=client,
            stream_prefix="Account",
            initial=INITIAL,
            apply=apply,
            event_from_json=event_from_json,
            invariants=INVARIANTS,
            stream_ids=["acc-1"],
        )

        assert result.all_passed is True
        assert result.streams_checked == 1
        assert result.streams_passed == 1
        assert result.streams_failed == 0
        assert result.events_replayed == 3
        assert result.failures == []
        assert result.errors == []
        client.get_stream.assert_called_once_with("Account-acc-1")


class TestSingleStreamViolation:
    def test_invariant_violation_at_specific_position(self):
        # Withdraw more than balance -> BalanceNonNegative fails
        records = [
            make_record(Deposited(amount=50), 0),
            make_record(Withdrawn(amount=100), 1),  # balance = -50
        ]
        client = MagicMock()
        client.get_stream.return_value = records

        result = verify_persistence(
            client=client,
            stream_prefix="Account",
            initial=INITIAL,
            apply=apply,
            event_from_json=event_from_json,
            invariants=INVARIANTS,
            stream_ids=["acc-1"],
        )

        assert result.all_passed is False
        assert result.streams_failed == 1
        assert len(result.failures) == 1

        violation = result.failures[0]
        assert isinstance(violation, StreamInvariantViolation)
        assert violation.stream_name == "Account-acc-1"
        assert violation.invariant_name == "BalanceNonNegative"
        assert violation.event_position == 1
        assert violation.event_type == "Withdrawn"
        assert violation.violating_state.balance == -50


class TestMultipleStreams:
    def test_multiple_streams_via_stream_ids(self):
        records_1 = [
            make_record(Deposited(amount=100), 0),
        ]
        records_2 = [
            make_record(Deposited(amount=200), 0),
            make_record(Withdrawn(amount=50), 1),
        ]
        client = MagicMock()
        client.get_stream.side_effect = [records_1, records_2]

        result = verify_persistence(
            client=client,
            stream_prefix="Account",
            initial=INITIAL,
            apply=apply,
            event_from_json=event_from_json,
            invariants=INVARIANTS,
            stream_ids=["acc-1", "acc-2"],
        )

        assert result.all_passed is True
        assert result.streams_checked == 2
        assert result.streams_passed == 2
        assert result.events_replayed == 3

    def test_one_passes_one_fails(self):
        records_good = [make_record(Deposited(amount=100), 0)]
        records_bad = [
            make_record(Deposited(amount=10), 0),
            make_record(Withdrawn(amount=50), 1),  # -40
        ]
        client = MagicMock()
        client.get_stream.side_effect = [records_good, records_bad]

        result = verify_persistence(
            client=client,
            stream_prefix="Account",
            initial=INITIAL,
            apply=apply,
            event_from_json=event_from_json,
            invariants=INVARIANTS,
            stream_ids=["good", "bad"],
        )

        assert result.all_passed is False
        assert result.streams_passed == 1
        assert result.streams_failed == 1
        assert len(result.failures) == 1
        assert result.failures[0].stream_name == "Account-bad"


class TestAllStreamsMode:
    def test_read_all_groups_by_stream_name(self):
        all_records = [
            make_all_record(Deposited(amount=100), "Account-acc-1"),
            make_all_record(Deposited(amount=50), "Account-acc-1"),
            make_all_record(Deposited(amount=200), "Account-acc-2"),
        ]
        client = MagicMock()
        client.read_all.return_value = all_records

        result = verify_persistence(
            client=client,
            stream_prefix="Account",
            initial=INITIAL,
            apply=apply,
            event_from_json=event_from_json,
            invariants=INVARIANTS,
            stream_ids=None,
        )

        assert result.all_passed is True
        assert result.streams_checked == 2
        assert result.events_replayed == 3
        client.read_all.assert_called_once()
        call_kwargs = client.read_all.call_args[1]
        assert call_kwargs["filter_by_stream_name"] is True


class TestEmptyAndMissingStreams:
    def test_empty_stream(self):
        client = MagicMock()
        client.get_stream.return_value = []

        result = verify_persistence(
            client=client,
            stream_prefix="Account",
            initial=INITIAL,
            apply=apply,
            event_from_json=event_from_json,
            invariants=INVARIANTS,
            stream_ids=["empty"],
        )

        assert result.all_passed is True
        assert result.streams_checked == 1
        assert result.events_replayed == 0

    def test_missing_stream_treated_as_empty(self):
        client = MagicMock()
        client.get_stream.side_effect = Exception("Stream not found")

        result = verify_persistence(
            client=client,
            stream_prefix="Account",
            initial=INITIAL,
            apply=apply,
            event_from_json=event_from_json,
            invariants=INVARIANTS,
            stream_ids=["missing"],
        )

        assert result.all_passed is True
        assert result.streams_checked == 1
        assert result.events_replayed == 0

    def test_connection_error_becomes_stream_error(self):
        client = MagicMock()
        client.get_stream.side_effect = ConnectionError("connection refused")

        result = verify_persistence(
            client=client,
            stream_prefix="Account",
            initial=INITIAL,
            apply=apply,
            event_from_json=event_from_json,
            invariants=INVARIANTS,
            stream_ids=["broken"],
        )

        assert result.all_passed is False
        assert len(result.errors) == 1
        assert isinstance(result.errors[0], StreamError)
        assert "connection refused" in result.errors[0].message


class TestCallableInitial:
    def test_callable_initial_is_invoked_per_stream(self):
        records = [make_record(Deposited(amount=100), 0)]
        client = MagicMock()
        client.get_stream.return_value = records

        call_count = 0

        def make_initial():
            nonlocal call_count
            call_count += 1
            return AccountState(balance=0, is_open=True)

        result = verify_persistence(
            client=client,
            stream_prefix="Account",
            initial=make_initial,
            apply=apply,
            event_from_json=event_from_json,
            invariants=INVARIANTS,
            stream_ids=["acc-1"],
        )

        assert result.all_passed is True
        assert call_count == 1


class TestCheckBuilderIntegration:
    def test_verify_persistence_via_builder(self):
        """CheckBuilder.verify_persistence delegates correctly."""
        from poes import Check

        records = [
            make_record(Deposited(amount=100), 0),
            make_record(Withdrawn(amount=30), 1),
        ]
        client = MagicMock()
        client.get_stream.return_value = records

        builder = (
            Check.define("Account", AccountState)
            .with_invariant("BalanceNonNegative", lambda s: s.balance >= 0)
        )

        result = builder.verify_persistence(
            client=client,
            stream_prefix="Account",
            initial=INITIAL,
            apply=apply,
            event_from_json=event_from_json,
            stream_ids=["acc-1"],
        )

        assert result.all_passed is True
        assert result.events_replayed == 2
