"""Generic event-sourced Repository for KurrentDB persistence."""

from __future__ import annotations


class Repository:
    """Event-sourced repository with optimistic concurrency over KurrentDB."""

    def __init__(self, client, stream_prefix: str, initial, apply, decide,
                 event_to_json, event_from_json):
        self._client = client
        self._prefix = stream_prefix
        self._initial = initial
        self._apply = apply
        self._decide = decide
        self._to_json = event_to_json
        self._from_json = event_from_json

    def _make_initial(self):
        return self._initial() if callable(self._initial) else self._initial

    def _stream_name(self, aggregate_id: str) -> str:
        return f"{self._prefix}-{aggregate_id}"

    def load(self, aggregate_id: str) -> tuple:
        """Load aggregate state by replaying events. Returns (state, version).

        version is -1 when the stream doesn't exist yet.
        """
        stream = self._stream_name(aggregate_id)
        state = self._make_initial()
        version = -1
        try:
            events = self._client.get_stream(stream)
            for record in events:
                event = self._from_json(record.type, record.data)
                state = self._apply(state, event)
                version = record.stream_position
        except Exception as e:
            if "not found" in str(e).lower():
                return state, version
            raise
        return state, version

    def execute(self, aggregate_id: str, command) -> tuple:
        """Load, decide, append, and return (new_state, new_version)."""
        from kurrentdbclient import NewEvent, StreamState

        state, version = self.load(aggregate_id)
        events = self._decide(state, command)
        if not events:
            return state, version

        new_events = [
            NewEvent(
                type=e.event_type,
                data=self._to_json(e).encode("utf-8"),
            )
            for e in events
        ]

        stream = self._stream_name(aggregate_id)
        if version == -1:
            self._client.append_to_stream(
                stream, current_version=StreamState.NO_STREAM, events=new_events,
            )
        else:
            self._client.append_to_stream(
                stream, current_version=version, events=new_events,
            )

        new_version = version + len(events)
        for e in events:
            state = self._apply(state, e)
        return state, new_version
