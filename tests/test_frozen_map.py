"""Smoke tests for FrozenMap + full POES verification integration."""

from dataclasses import dataclass

import hypothesis.strategies as st

from poes import Check, FrozenMap


# ── Unit tests ────────────────────────────────────────────────────────────────


def test_frozen_map_basics():
    m = FrozenMap({"a": 1, "b": 2})
    assert m["a"] == 1
    assert "b" in m
    assert len(m) == 2
    assert set(m.keys()) == {"a", "b"}

    m2 = m.set("c", 3)
    assert "c" in m2
    assert "c" not in m  # original unchanged

    m3 = m2.delete("a")
    assert "a" not in m3
    assert len(m3) == 2


def test_frozen_map_hashable():
    m1 = FrozenMap({"x": 1})
    m2 = FrozenMap({"x": 1})
    assert hash(m1) == hash(m2)
    assert m1 == m2
    s = {m1, m2}
    assert len(s) == 1


def test_frozen_map_empty():
    m = FrozenMap()
    assert len(m) == 0
    assert list(m) == []


# ── POES integration: all 3 phases with FrozenMap state ──────────────────────


@dataclass(frozen=True)
class Counter:
    """Tracks per-key counts in a FrozenMap."""
    counts: FrozenMap


def test_poes_all_phases_with_frozen_map():
    """Define a tiny aggregate with a FrozenMap field and run all 3 POES phases."""
    initial = Counter(counts=FrozenMap())

    result = (
        Check.define("CounterMap", Counter)
        .with_initial(initial)
        .with_map_field("counts", st.text(min_size=1, max_size=2), st.integers(0, 5),
                        min_size=0, max_size=3)
        .with_invariant(
            "AllNonNegative",
            lambda s: all(v >= 0 for v in s.counts.values()),
        )
        .with_parametric_transition(
            "Increment",
            {"key": st.text(min_size=1, max_size=2)},
            guard=lambda s, key: True,
            apply=lambda s, key: Counter(counts=s.counts.set(key, s.counts.get(key, 0) + 1)),
            ensures=lambda before, after, key: after.counts.get(key, 0) == before.counts.get(key, 0) + 1,
        )
        .with_eventually(
            "SomeKeyExists",
            lambda s: len(s.counts) > 0,
        )
        .with_max_examples(50)
        .run()
    )

    assert result.all_passed
    assert result.property_proofs > 0
    assert result.states_explored > 0
    assert result.temporal_properties_checked == 1
