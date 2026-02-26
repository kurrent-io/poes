"""Persistence verification — replay production events and check invariants."""

from __future__ import annotations

import io
import re
import sys
import time
from dataclasses import dataclass


def _ensure_utf8_stdout():
    """Ensure stdout can handle Unicode box-drawing characters on Windows."""
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StreamVerified:
    stream_name: str
    events_replayed: int


@dataclass(frozen=True)
class StreamInvariantViolation:
    stream_name: str
    invariant_name: str
    event_position: int
    event_type: str
    violating_state: object


@dataclass(frozen=True)
class StreamError:
    stream_name: str
    message: str


@dataclass(frozen=True)
class PersistenceCheckResult:
    streams_checked: int
    streams_passed: int
    streams_failed: int
    events_replayed: int
    all_passed: bool
    failures: list  # list[StreamInvariantViolation]
    errors: list    # list[StreamError]
    duration_ms: int


# ---------------------------------------------------------------------------
# Core replay
# ---------------------------------------------------------------------------

def _verify_single_stream(
    stream_name: str,
    records: list,
    initial,
    apply,
    event_from_json,
    invariants: list[tuple[str, callable]],
) -> StreamVerified | StreamInvariantViolation | StreamError:
    """Replay events for one stream, checking invariants after each event."""
    state = initial() if callable(initial) else initial
    count = 0
    try:
        for i, record in enumerate(records):
            event = event_from_json(record.type, record.data)
            state = apply(state, event)
            count += 1
            for inv_name, inv_check in invariants:
                if not inv_check(state):
                    return StreamInvariantViolation(
                        stream_name=stream_name,
                        invariant_name=inv_name,
                        event_position=i,
                        event_type=record.type,
                        violating_state=state,
                    )
    except Exception as e:
        return StreamError(stream_name=stream_name, message=str(e))

    return StreamVerified(stream_name=stream_name, events_replayed=count)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def verify_persistence(
    *,
    client,
    stream_prefix: str,
    initial,
    apply,
    event_from_json,
    invariants: list[tuple[str, callable]],
    stream_ids: list[str] | None = None,
) -> PersistenceCheckResult:
    """Replay production events from KurrentDB and verify invariants.

    Parameters
    ----------
    client : KurrentDB client instance
    stream_prefix : naming prefix (e.g. "BankAccount")
    initial : initial state value or callable factory
    apply : (state, event) -> state
    event_from_json : (type_str, data_bytes) -> event
    invariants : list of (name, predicate) pairs
    stream_ids : specific IDs to check, or None for all streams matching prefix
    """
    _ensure_utf8_stdout()
    start = time.monotonic()

    # Collect streams to verify
    streams: dict[str, list] = {}  # stream_name -> records

    if stream_ids is not None:
        for sid in stream_ids:
            stream_name = f"{stream_prefix}-{sid}"
            try:
                records = list(client.get_stream(stream_name))
                streams[stream_name] = records
            except Exception as e:
                if "not found" in str(e).lower():
                    streams[stream_name] = []
                else:
                    streams[stream_name] = e  # store exception for later
    else:
        # Read all streams matching prefix
        pattern = rf"{re.escape(stream_prefix)}-.*"
        all_records = client.read_all(
            filter_include=[pattern], filter_by_stream_name=True
        )
        for record in all_records:
            name = record.stream_name
            streams.setdefault(name, []).append(record)

    # Verify each stream
    results: list[StreamVerified | StreamInvariantViolation | StreamError] = []
    for stream_name in sorted(streams.keys()):
        records_or_err = streams[stream_name]
        if isinstance(records_or_err, Exception):
            results.append(StreamError(
                stream_name=stream_name, message=str(records_or_err),
            ))
        else:
            results.append(_verify_single_stream(
                stream_name, records_or_err, initial, apply,
                event_from_json, invariants,
            ))

    # Tally
    failures = [r for r in results if isinstance(r, StreamInvariantViolation)]
    errors = [r for r in results if isinstance(r, StreamError)]
    passed = [r for r in results if isinstance(r, StreamVerified)]
    total_events = sum(r.events_replayed for r in passed)
    all_passed = len(failures) == 0 and len(errors) == 0

    duration_ms = int((time.monotonic() - start) * 1000)

    # Console output
    print()
    print("  ┌─ Persistence Verification ────────────────────────────────────────────────┐")
    for r in results:
        if isinstance(r, StreamVerified):
            print(f"  │  ✓ {r.stream_name}  ({r.events_replayed} events)")
        elif isinstance(r, StreamInvariantViolation):
            print(f"  │  ✗ {r.stream_name}  event #{r.event_position} ({r.event_type}) violates {r.invariant_name}")
        elif isinstance(r, StreamError):
            print(f"  │  ! {r.stream_name}  error: {r.message}")
    print("  └─────────────────────────────────────────────────────────────────────────────┘")

    if all_passed:
        print(f"  ✓ Persistence verified: {len(passed)} streams, {total_events} events ({duration_ms}ms)")
    else:
        print(f"  ✗ Persistence check failed: {len(failures)} violations, {len(errors)} errors")
    print()

    return PersistenceCheckResult(
        streams_checked=len(results),
        streams_passed=len(passed),
        streams_failed=len(failures) + len(errors),
        events_replayed=total_events,
        all_passed=all_passed,
        failures=failures,
        errors=errors,
        duration_ms=duration_ms,
    )
