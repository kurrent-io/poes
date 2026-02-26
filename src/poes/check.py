"""Check — clean verification API with fluent builder."""

from __future__ import annotations

import io
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import hypothesis.strategies as st


def _ensure_utf8_stdout():
    """Ensure stdout can handle Unicode box-drawing characters on Windows."""
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from .hypothesis_bridge import (
    PropertyProved,
    PropertyFailed,
    PropertyError,
    verify_properties,
    verify_parametric_properties,
)
from .explorer import (
    AllSafe,
    ExplorerConfig,
    LimitReached,
    PostConditionViolation,
    StateViolation,
    Transition,
    explore,
)
from .temporal import (
    AlwaysEventually,
    Eventually,
    LeadsTo,
    TemporalProperty,
    AllPropertiesSatisfied,
    LivenessViolation,
    build_graph,
    check_all,
)


def _sample_params(params: dict[str, st.SearchStrategy], n: int = 5) -> list[dict]:
    """Draw n representative param combos from strategies for BFS expansion."""
    combo_strategy = st.fixed_dictionaries(params)
    seen: set[tuple] = set()
    samples: list[dict] = []
    # Draw until we have n unique combos or exhaust attempts
    for _ in range(n * 10):
        if len(samples) >= n:
            break
        example = combo_strategy.example()
        key = tuple(sorted(example.items()))
        if key not in seen:
            seen.add(key)
            samples.append(example)
    return samples


@dataclass(frozen=True)
class CheckResult:
    aggregate_name: str
    property_passed: bool
    property_proofs: int
    property_failed: int
    exploration_passed: bool
    states_explored: int
    temporal_passed: bool
    temporal_properties_checked: int
    all_passed: bool
    duration_ms: int
    counterexample: str | None


class CheckBuilder:
    """Fluent builder for aggregate verification."""

    def __init__(self, name: str, state_type: type):
        self._name = name
        self._state_type = state_type
        self._initial = None
        self._fields: dict[str, st.SearchStrategy] = {}
        self._invariants: list[tuple[str, Callable]] = []
        # (name, guard, apply, ensures_name, ensures)
        self._transitions: list[tuple[str, Callable, Callable, str, Callable]] = []
        # (name, param_strats, guard, apply, ensures_name, ensures)
        self._parametric_transitions: list[tuple[str, dict, Callable, Callable, str, Callable]] = []
        self._expected_transition_count: int | None = None
        self._temporal_properties: list[TemporalProperty] = []
        self._state_diagram: list[tuple[str, str, str]] = []
        self._max_examples: int = 500

    def with_initial(self, state) -> CheckBuilder:
        self._initial = state
        return self

    def with_field(self, name: str, strategy: st.SearchStrategy) -> CheckBuilder:
        self._fields[name] = strategy
        return self

    def with_map_field(
        self,
        name: str,
        keys_strategy: st.SearchStrategy,
        values_strategy: st.SearchStrategy,
        *,
        min_size: int = 0,
        max_size: int | None = None,
    ) -> CheckBuilder:
        """Register a FrozenMap field with key/value strategies."""
        from .frozen_map import FrozenMap
        self._fields[name] = FrozenMap.strategy(
            keys_strategy, values_strategy,
            min_size=min_size, max_size=max_size,
        )
        return self

    def with_invariant(self, name: str, check: Callable) -> CheckBuilder:
        self._invariants.append((name, check))
        return self

    def with_transition(
        self,
        name: str,
        guard: Callable,
        apply: Callable,
        ensures: Callable,
    ) -> CheckBuilder:
        self._transitions.append((name, guard, apply, name + "Correct", ensures))
        return self

    def with_transition_named(
        self,
        name: str,
        guard: Callable,
        apply: Callable,
        ensures_name: str,
        ensures: Callable,
    ) -> CheckBuilder:
        self._transitions.append((name, guard, apply, ensures_name, ensures))
        return self

    def with_parametric_transition(
        self,
        name: str,
        params: dict[str, st.SearchStrategy],
        guard: Callable,
        apply: Callable,
        ensures: Callable,
    ) -> CheckBuilder:
        self._parametric_transitions.append(
            (name, params, guard, apply, name + "Correct", ensures)
        )
        return self

    def expect_transitions(self, count: int) -> CheckBuilder:
        self._expected_transition_count = count
        return self

    def with_state_diagram(self, transitions: list[tuple[str, str, str]]) -> CheckBuilder:
        """Set explicit state diagram edges for rich Mermaid rendering.

        Each tuple is (from_state, to_state, transition_name).
        Use ``"[*]"`` for the initial pseudo-state.
        """
        self._state_diagram = list(transitions)
        return self

    def with_max_examples(self, n: int) -> CheckBuilder:
        self._max_examples = n
        return self

    def with_eventually(self, name: str, predicate: Callable) -> CheckBuilder:
        self._temporal_properties.append(Eventually(name, predicate))
        return self

    def with_leads_to(self, name: str, trigger: Callable, response: Callable) -> CheckBuilder:
        self._temporal_properties.append(LeadsTo(name, trigger, response))
        return self

    def with_always_eventually(self, name: str, predicate: Callable) -> CheckBuilder:
        self._temporal_properties.append(AlwaysEventually(name, predicate))
        return self

    def verify_persistence(
        self,
        *,
        client,
        stream_prefix: str,
        initial,
        apply,
        event_from_json,
        stream_ids: list[str] | None = None,
    ):
        """Replay production events from KurrentDB and verify all invariants.

        Returns a PersistenceCheckResult.
        """
        from .persistence_check import verify_persistence
        return verify_persistence(
            client=client,
            stream_prefix=stream_prefix,
            initial=initial,
            apply=apply,
            event_from_json=event_from_json,
            invariants=self._invariants,
            stream_ids=stream_ids,
        )

    def generate_proof_of_work(self, path: str | Path | None = None) -> str:
        """Generate a Markdown proof-of-work document from this builder's configuration.

        If *path* is given, the document is also written to that file (UTF-8).
        """
        from .docgen import generate_proof_of_work
        md = generate_proof_of_work(self)
        if path is not None:
            Path(path).write_text(md, encoding="utf-8")
        return md

    def run(self) -> CheckResult:
        """Run all verification phases and print results."""
        _ensure_utf8_stdout()
        if self._initial is None:
            raise ValueError("Initial state must be set before running verification")

        if self._expected_transition_count is not None:
            actual = len(self._transitions) + len(self._parametric_transitions)
            if actual != self._expected_transition_count:
                raise ValueError(
                    f"Expected {self._expected_transition_count} transitions but found {actual}. "
                    "Ensure all event types are verified."
                )

        start = time.monotonic()

        print()
        print("╔══════════════════════════════════════════════════════════════════════════════╗")
        print(f"║  VERIFYING: {self._name}")
        print("╚══════════════════════════════════════════════════════════════════════════════╝")

        print()
        total_transitions = len(self._transitions) + len(self._parametric_transitions)
        print(f"  Fields: {len(self._fields)} | Max examples: {self._max_examples} "
              f"| Invariants: {len(self._invariants)} | Transitions: {total_transitions}")

        # Phase 1: Property Testing (Hypothesis)
        print()
        print("  ┌─ Property Testing ──────────────────────────────────────────────────────────┐")

        prop_results = verify_properties(
            self._state_type, self._fields, self._transitions,
            self._invariants, self._max_examples,
        )
        if self._parametric_transitions:
            prop_results += verify_parametric_properties(
                self._state_type, self._fields, self._parametric_transitions,
                self._invariants, self._max_examples,
            )
        property_proofs = 0
        property_failed = 0
        counterexample: str | None = None

        for r in prop_results:
            if isinstance(r, PropertyProved):
                property_proofs += 1
                print(f"  │  ✓ {r.name}  ({r.examples_tested} examples)")
            elif isinstance(r, PropertyFailed):
                property_failed += 1
                if counterexample is None:
                    counterexample = f"{r.name} failed"
                print(f"  │  ✗ {r.name}")
            elif isinstance(r, PropertyError):
                property_failed += 1
                if counterexample is None:
                    counterexample = f"{r.name}: {r.message}"
                print(f"  │  ! {r.name}: {r.message}")

        print("  └─────────────────────────────────────────────────────────────────────────────┘")

        # Phase 2: State Exploration (BFS)
        print()
        print("  ┌─ State Exploration ─────────────────────────────────────────────────────────┐")

        explorer_transitions = [
            Transition(name=name, guard=guard, apply=apply_fn,
                       ensures=ensures, ensures_name=ensures_name)
            for name, guard, apply_fn, ensures_name, ensures in self._transitions
        ]

        # Expand parametric transitions into concrete Transition instances
        for t_name, param_strats, guard, apply_fn, ensures_name, ensures in self._parametric_transitions:
            for sample in _sample_params(param_strats):
                explorer_transitions.append(Transition(
                    name=t_name,
                    guard=lambda s, _g=guard, _p=sample: _g(s, **_p),
                    apply=lambda s, _a=apply_fn, _p=sample: _a(s, **_p),
                    ensures=lambda before, after, _e=ensures, _p=sample: _e(before, after, **_p),
                    ensures_name=ensures_name,
                ))

        exploration_result = explore(
            ExplorerConfig(),
            self._initial,
            explorer_transitions,
            self._invariants,
        )

        if isinstance(exploration_result, AllSafe):
            print(f"  │  ✓ All {exploration_result.states_explored} reachable states safe")
            exploration_passed = True
            states_explored = exploration_result.states_explored
        elif isinstance(exploration_result, StateViolation):
            print(f"  │  ✗ Invariant violation: {exploration_result.invariant_name}")
            if counterexample is None:
                counterexample = f"Reachable state violates {exploration_result.invariant_name}"
            exploration_passed = False
            states_explored = 0
        elif isinstance(exploration_result, PostConditionViolation):
            print(f"  │  ✗ Post-condition violation: {exploration_result.transition_name}/{exploration_result.ensures_name}")
            if counterexample is None:
                counterexample = f"Reachable transition violates {exploration_result.ensures_name}"
            exploration_passed = False
            states_explored = 0
        elif isinstance(exploration_result, LimitReached):
            print(f"  │  ⚠ Limit reached ({exploration_result.limit_type}) after {exploration_result.states_explored} states")
            exploration_passed = True
            states_explored = exploration_result.states_explored
        else:
            print(f"  │  ! Error: {exploration_result.message}")
            exploration_passed = False
            states_explored = 0

        print("  └─────────────────────────────────────────────────────────────────────────────┘")

        # Phase 3: Temporal Properties (SCC)
        temporal_passed = True
        temporal_checked = 0

        if self._temporal_properties:
            print()
            print("  ┌─ Temporal Properties ──────────────────────────────────────────────────────┐")

            graph = build_graph(self._initial, explorer_transitions, max_states=10_000)
            temporal_result = check_all(graph, self._temporal_properties)

            if isinstance(temporal_result, AllPropertiesSatisfied):
                temporal_checked = temporal_result.properties_checked
                for prop in self._temporal_properties:
                    if isinstance(prop, Eventually):
                        print(f"  │  ✓ Eventually({prop.name})")
                    elif isinstance(prop, LeadsTo):
                        print(f"  │  ✓ LeadsTo({prop.name})")
                    elif isinstance(prop, AlwaysEventually):
                        print(f"  │  ✓ AlwaysEventually({prop.name})")
            elif isinstance(temporal_result, LivenessViolation):
                temporal_passed = False
                temporal_checked = len(self._temporal_properties)
                if counterexample is None:
                    counterexample = f"Liveness violation: {temporal_result.property_name}"
                print(f"  │  ✗ {temporal_result.property_name}: {temporal_result.description}")

            print("  └─────────────────────────────────────────────────────────────────────────────┘")

        duration_ms = int((time.monotonic() - start) * 1000)

        all_passed = (
            property_failed == 0
            and exploration_passed
            and temporal_passed
        )

        total_proofs = property_proofs + temporal_checked
        print()
        if all_passed:
            print(f"  ✓ VERIFIED: All {total_proofs} proofs passed, {states_explored} states explored ({duration_ms}ms)")
        else:
            print(f"  ✗ FAILED: {counterexample or 'Unknown error'}")
        print()

        return CheckResult(
            aggregate_name=self._name,
            property_passed=property_failed == 0,
            property_proofs=property_proofs,
            property_failed=property_failed,
            exploration_passed=exploration_passed,
            states_explored=states_explored,
            temporal_passed=temporal_passed,
            temporal_properties_checked=temporal_checked,
            all_passed=all_passed,
            duration_ms=duration_ms,
            counterexample=counterexample,
        )


class Check:
    """Main verification entry point."""

    @staticmethod
    def define(name: str, state_type: type) -> CheckBuilder:
        return CheckBuilder(name, state_type)
