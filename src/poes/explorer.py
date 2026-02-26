"""BFS state space exploration."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Transition:
    """A transition for state exploration."""
    name: str
    guard: Callable[[Any], bool]
    apply: Callable[[Any], Any]
    ensures: Callable[[Any, Any], bool] | None = None
    ensures_name: str | None = None


@dataclass(frozen=True)
class ExplorerConfig:
    """Configuration for state exploration."""
    max_states: int = 1_000_000
    max_depth: int = 100


@dataclass(frozen=True)
class AllSafe:
    states_explored: int
    max_depth_reached: int
    duration_ms: int


@dataclass(frozen=True)
class StateViolation:
    invariant_name: str
    violating_state: Any
    path: list[tuple[str, Any]]  # (transition_name, resulting_state)
    duration_ms: int


@dataclass(frozen=True)
class PostConditionViolation:
    transition_name: str
    ensures_name: str
    pre_state: Any
    post_state: Any
    path: list[tuple[str, Any]]
    duration_ms: int


@dataclass(frozen=True)
class LimitReached:
    states_explored: int
    limit_type: str
    duration_ms: int


@dataclass(frozen=True)
class ExplorerError:
    message: str


ExplorerResult = AllSafe | StateViolation | PostConditionViolation | LimitReached | ExplorerError


def explore(
    config: ExplorerConfig,
    initial_state: Any,
    transitions: list[Transition],
    invariants: list[tuple[str, Callable[[Any], bool]]],
) -> ExplorerResult:
    """Explore all reachable states from initial state via BFS."""
    start = time.monotonic()

    # Check initial state
    for inv_name, inv_check in invariants:
        if not inv_check(initial_state):
            ms = int((time.monotonic() - start) * 1000)
            return StateViolation(inv_name, initial_state, [], ms)

    visited: set = {initial_state}
    queue: deque = deque()
    queue.append((initial_state, [], 0))

    max_depth = 0

    while queue:
        if len(visited) >= config.max_states:
            ms = int((time.monotonic() - start) * 1000)
            return LimitReached(len(visited), "max states", ms)

        current_state, path, depth = queue.popleft()
        max_depth = max(max_depth, depth)

        if depth >= config.max_depth:
            ms = int((time.monotonic() - start) * 1000)
            return LimitReached(len(visited), "max depth", ms)

        for t in transitions:
            if t.guard(current_state):
                next_state = t.apply(current_state)
                new_path = path + [(t.name, next_state)]

                # Check invariants
                for inv_name, inv_check in invariants:
                    if not inv_check(next_state):
                        ms = int((time.monotonic() - start) * 1000)
                        return StateViolation(inv_name, next_state, new_path, ms)

                # Check post-condition
                if t.ensures is not None and not t.ensures(current_state, next_state):
                    ms = int((time.monotonic() - start) * 1000)
                    return PostConditionViolation(
                        t.name, t.ensures_name or t.name, current_state, next_state, new_path, ms,
                    )

                if next_state not in visited:
                    visited.add(next_state)
                    queue.append((next_state, new_path, depth + 1))

    ms = int((time.monotonic() - start) * 1000)
    return AllSafe(len(visited), max_depth, ms)
