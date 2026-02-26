"""Hypothesis integration — property-based testing and RuleBasedStateMachine bridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from hypothesis import given, settings, HealthCheck
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
)
import hypothesis.strategies as st

if TYPE_CHECKING:
    from .check import CheckBuilder


@dataclass(frozen=True)
class PropertyProved:
    name: str
    examples_tested: int


@dataclass(frozen=True)
class PropertyFailed:
    name: str
    counterexample: str


@dataclass(frozen=True)
class PropertyError:
    name: str
    message: str


PropertyResult = PropertyProved | PropertyFailed | PropertyError


def _make_invariant_test(strategy, guard, apply_fn, inv_check, max_examples):
    """Create a Hypothesis test for invariant preservation (no default args)."""
    counter = [0]

    @given(state=strategy)
    @settings(
        max_examples=max_examples,
        suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow],
        deadline=None,
    )
    def test(state):
        if not inv_check(state) or not guard(state):
            return
        counter[0] += 1
        new_state = apply_fn(state)
        assert inv_check(new_state), (
            f"Invariant violated. Before: {state!r}, After: {new_state!r}"
        )

    return test, counter


def _make_postcond_test(strategy, guard, apply_fn, ensures, max_examples):
    """Create a Hypothesis test for post-condition verification (no default args)."""
    counter = [0]

    @given(state=strategy)
    @settings(
        max_examples=max_examples,
        suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow],
        deadline=None,
    )
    def test(state):
        if not guard(state):
            return
        counter[0] += 1
        new_state = apply_fn(state)
        assert ensures(state, new_state), (
            f"Post-condition violated. Before: {state!r}, After: {new_state!r}"
        )

    return test, counter


def _make_parametric_invariant_test(state_strategy, param_strats, guard, apply_fn, inv_check, max_examples):
    """Create a Hypothesis test for invariant preservation with parametric transitions."""
    counter = [0]

    @given(state=state_strategy, **param_strats)
    @settings(
        max_examples=max_examples,
        suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow],
        deadline=None,
    )
    def test(state, **params):
        if not inv_check(state) or not guard(state, **params):
            return
        counter[0] += 1
        new_state = apply_fn(state, **params)
        assert inv_check(new_state), (
            f"Invariant violated. Before: {state!r}, Params: {params!r}, After: {new_state!r}"
        )

    return test, counter


def _make_parametric_postcond_test(state_strategy, param_strats, guard, apply_fn, ensures, max_examples):
    """Create a Hypothesis test for post-condition verification with parametric transitions."""
    counter = [0]

    @given(state=state_strategy, **param_strats)
    @settings(
        max_examples=max_examples,
        suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow],
        deadline=None,
    )
    def test(state, **params):
        if not guard(state, **params):
            return
        counter[0] += 1
        new_state = apply_fn(state, **params)
        assert ensures(state, new_state, **params), (
            f"Post-condition violated. Before: {state!r}, Params: {params!r}, After: {new_state!r}"
        )

    return test, counter


def verify_parametric_properties(
    state_type: type,
    fields: dict[str, st.SearchStrategy],
    parametric_transitions: list[tuple[str, dict, Callable, Callable, str, Callable]],
    invariants: list[tuple[str, Callable]],
    max_examples: int = 500,
) -> list[PropertyResult]:
    """Verify invariant preservation and post-conditions for parametric transitions.

    parametric_transitions: list of (name, param_strats, guard, apply_fn, ensures_name, ensures)
    """
    strategy = st.builds(state_type, **fields)
    results: list[PropertyResult] = []

    for t_name, param_strats, guard, apply_fn, ensures_name, ensures in parametric_transitions:
        # Invariant preservation
        for inv_name, inv_check in invariants:
            name = f"{t_name}/{inv_name}"
            try:
                test, counter = _make_parametric_invariant_test(
                    strategy, param_strats, guard, apply_fn, inv_check, max_examples
                )
                test()
                results.append(PropertyProved(name, counter[0]))
            except AssertionError as e:
                results.append(PropertyFailed(name, str(e)))
            except Exception as e:
                results.append(PropertyError(name, str(e)))

        # Post-condition
        name = f"{t_name}/{ensures_name}"
        try:
            test, counter = _make_parametric_postcond_test(
                strategy, param_strats, guard, apply_fn, ensures, max_examples
            )
            test()
            results.append(PropertyProved(name, counter[0]))
        except AssertionError as e:
            results.append(PropertyFailed(name, str(e)))
        except Exception as e:
            results.append(PropertyError(name, str(e)))

    return results


def verify_properties(
    state_type: type,
    fields: dict[str, st.SearchStrategy],
    transitions: list[tuple[str, Callable, Callable, str, Callable]],
    invariants: list[tuple[str, Callable]],
    max_examples: int = 500,
) -> list[PropertyResult]:
    """Verify invariant preservation and post-conditions using Hypothesis.

    For each transition x invariant pair: generate random states, filter where
    invariant holds AND guard passes, apply transition, assert invariant preserved.
    Similarly for post-conditions (ensures clauses).
    """
    strategy = st.builds(state_type, **fields)
    results: list[PropertyResult] = []

    # Test invariant preservation: for each transition x invariant
    for t_name, guard, apply_fn, _, _ in transitions:
        for inv_name, inv_check in invariants:
            name = f"{t_name}/{inv_name}"
            try:
                test, counter = _make_invariant_test(
                    strategy, guard, apply_fn, inv_check, max_examples
                )
                test()
                results.append(PropertyProved(name, counter[0]))
            except AssertionError as e:
                results.append(PropertyFailed(name, str(e)))
            except Exception as e:
                results.append(PropertyError(name, str(e)))

    # Test post-conditions: for each transition
    for t_name, guard, apply_fn, ensures_name, ensures in transitions:
        name = f"{t_name}/{ensures_name}"
        try:
            test, counter = _make_postcond_test(
                strategy, guard, apply_fn, ensures, max_examples
            )
            test()
            results.append(PropertyProved(name, counter[0]))
        except AssertionError as e:
            results.append(PropertyFailed(name, str(e)))
        except Exception as e:
            results.append(PropertyError(name, str(e)))

    return results


def check_to_state_machine(builder: CheckBuilder) -> type:
    """Dynamically create a RuleBasedStateMachine subclass from a CheckBuilder.

    Each transition becomes a @rule method with @precondition(guard).
    Each invariant becomes an @invariant method.
    """
    attrs: dict = {}

    # __init__
    initial = builder._initial

    def init_method(self):
        super(machine_cls, self).__init__()
        self.state = initial

    attrs["__init__"] = init_method

    # Invariants
    for inv_name, inv_check in builder._invariants:
        def make_inv(name, check):
            @invariant()
            def inv_method(self):
                assert check(self.state), f"Invariant {name} violated"
            return inv_method
        attrs[f"check_{inv_name}"] = make_inv(inv_name, inv_check)

    # Transitions: @precondition(guard) + @rule()
    for t_name, guard, apply_fn, ensures_name, ensures in builder._transitions:
        def make_rule(name, g, a, ename, e):
            @precondition(lambda self, _g=g: _g(self.state))
            @rule()
            def rule_method(self):
                before = self.state
                self.state = a(self.state)
                assert e(before, self.state), f"Post-condition {ename} violated"
            return rule_method
        attrs[f"do_{t_name}"] = make_rule(t_name, guard, apply_fn, ensures_name, ensures)

    machine_cls = type(f"{builder._name}Machine", (RuleBasedStateMachine,), attrs)
    return machine_cls


def run_hypothesis_test(builder: CheckBuilder, settings=None):
    """Convenience: create and run the state machine test."""
    machine_cls = check_to_state_machine(builder)
    test = machine_cls.TestCase
    if settings is not None:
        test.settings = settings
    test().runTest()
