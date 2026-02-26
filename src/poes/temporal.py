"""SCC-based temporal/liveness property checking."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable


# --- Temporal property types ---

@dataclass(frozen=True)
class Eventually:
    name: str
    predicate: Callable[[Any], bool]


@dataclass(frozen=True)
class LeadsTo:
    name: str
    trigger: Callable[[Any], bool]
    response: Callable[[Any], bool]


@dataclass(frozen=True)
class AlwaysEventually:
    name: str
    predicate: Callable[[Any], bool]


TemporalProperty = Eventually | LeadsTo | AlwaysEventually


# --- Results ---

@dataclass(frozen=True)
class AllPropertiesSatisfied:
    properties_checked: int


@dataclass(frozen=True)
class LivenessViolation:
    property_name: str
    description: str
    bad_cycle: list


TemporalResult = AllPropertiesSatisfied | LivenessViolation


# --- State graph ---

@dataclass
class StateGraph:
    states: set = field(default_factory=set)
    edges: dict = field(default_factory=dict)  # state -> list[state]
    initial_state: Any = None


def build_graph(initial, transitions, max_states: int = 10_000) -> StateGraph:
    """Build state graph by exploring all reachable states via BFS."""
    graph = StateGraph(initial_state=initial)
    graph.states.add(initial)
    graph.edges[initial] = []
    queue: deque = deque([initial])

    while queue and len(graph.states) < max_states:
        current = queue.popleft()
        for t in transitions:
            if t.guard(current):
                nxt = t.apply(current)
                graph.edges[current].append(nxt)
                if nxt not in graph.states:
                    graph.states.add(nxt)
                    graph.edges[nxt] = []
                    queue.append(nxt)

    return graph


def find_sccs(graph: StateGraph) -> list[list]:
    """Iterative Tarjan's algorithm for finding Strongly Connected Components."""
    index: dict = {}
    lowlink: dict = {}
    on_stack: set = set()
    scc_stack: list = []
    sccs: list[list] = []
    current_index = 0

    # Call stack: (node, phase, successor_index)
    call_stack: list[tuple] = []

    for start_node in graph.states:
        if start_node in index:
            continue

        call_stack.append((start_node, 0, 0))

        while call_stack:
            v, phase, suc_idx = call_stack.pop()

            if phase == 0:
                # Initial visit
                index[v] = current_index
                lowlink[v] = current_index
                current_index += 1
                scc_stack.append(v)
                on_stack.add(v)
                call_stack.append((v, 1, 0))

            elif phase == 1:
                # Process successors
                successors = graph.edges.get(v, [])
                if suc_idx < len(successors):
                    w = successors[suc_idx]
                    if w not in index:
                        call_stack.append((v, 1, suc_idx + 1))
                        call_stack.append((w, 0, 0))
                    else:
                        if w in on_stack:
                            lowlink[v] = min(lowlink[v], index[w])
                        call_stack.append((v, 1, suc_idx + 1))
                else:
                    # All successors processed
                    call_stack.append((v, 2, 0))

            elif phase == 2:
                # Post-process: update parent's lowlink
                if call_stack:
                    parent, parent_phase, _ = call_stack[-1]
                    if parent_phase == 1 and parent in index:
                        lowlink[parent] = min(lowlink[parent], lowlink[v])

                # If root, pop SCC
                if lowlink[v] == index[v]:
                    scc = []
                    while True:
                        w = scc_stack.pop()
                        on_stack.discard(w)
                        scc.append(w)
                        if w == v:
                            break
                    sccs.append(scc)

    return sccs


def _is_non_trivial_scc(graph: StateGraph, scc: list) -> bool:
    """Check if an SCC has a cycle (multi-node or single self-loop)."""
    if len(scc) == 1:
        v = scc[0]
        return v in graph.edges and v in graph.edges[v]
    return True


def check_eventually(graph: StateGraph, predicate: Callable) -> list | None:
    """Check Eventually(P): no cycle avoids P entirely. Returns bad cycle or None."""
    sccs = find_sccs(graph)
    for scc in sccs:
        if _is_non_trivial_scc(graph, scc) and all(not predicate(s) for s in scc):
            return scc
    return None


def check_always_eventually(graph: StateGraph, predicate: Callable) -> list | None:
    """Same as check_eventually in finite state graphs without fairness."""
    return check_eventually(graph, predicate)


def check_leads_to(
    graph: StateGraph,
    trigger: Callable,
    response: Callable,
) -> list | None:
    """Check LeadsTo(P, Q): after P, must eventually reach Q."""
    sccs = find_sccs(graph)
    bad_cycles = [
        scc for scc in sccs
        if _is_non_trivial_scc(graph, scc) and all(not response(s) for s in scc)
    ]

    if not bad_cycles:
        return None

    bad_cycle_states = set()
    for scc in bad_cycles:
        bad_cycle_states.update(scc)

    trigger_states = [s for s in graph.states if trigger(s)]

    for start in trigger_states:
        visited: set = {start}
        queue: deque = deque([start])
        found = False
        while queue and not found:
            current = queue.popleft()
            if current in bad_cycle_states:
                found = True
            else:
                for nxt in graph.edges.get(current, []):
                    if nxt not in visited:
                        visited.add(nxt)
                        queue.append(nxt)
        if found:
            return bad_cycles[0]

    return None


def check_all(
    graph: StateGraph,
    properties: list[TemporalProperty],
) -> TemporalResult:
    """Check all temporal properties, returning first violation or success."""
    for i, prop in enumerate(properties):
        if isinstance(prop, Eventually):
            bad = check_eventually(graph, prop.predicate)
            if bad is not None:
                return LivenessViolation(prop.name, "Found cycle where property never holds", bad)
        elif isinstance(prop, AlwaysEventually):
            bad = check_always_eventually(graph, prop.predicate)
            if bad is not None:
                return LivenessViolation(prop.name, "Found cycle that never satisfies property", bad)
        elif isinstance(prop, LeadsTo):
            bad = check_leads_to(graph, prop.trigger, prop.response)
            if bad is not None:
                return LivenessViolation(prop.name, "Trigger can lead to cycle that never satisfies response", bad)

    return AllPropertiesSatisfied(len(properties))
