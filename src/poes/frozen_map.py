"""FrozenMap — a hashable, immutable dict wrapper for POES verification."""

from __future__ import annotations

from typing import Any, Iterator, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class FrozenMap:
    """Hashable, immutable mapping for use in POES aggregate state.

    All values must themselves be hashable (ints, strings, bools, tuples,
    nested ``FrozenMap`` instances, etc.).

    Usage::

        m = FrozenMap({"a": 1, "b": 2})
        m2 = m.set("c", 3).delete("a")
        assert "c" in m2 and "a" not in m2
    """

    __slots__ = ("_data", "_hash")

    def __init__(self, data: dict | None = None, **kwargs: Any) -> None:
        if data is None:
            data = {}
        elif not isinstance(data, dict):
            data = dict(data)
        else:
            data = dict(data)
        data.update(kwargs)
        object.__setattr__(self, "_data", data)
        object.__setattr__(self, "_hash", None)

    # ── Read API ──────────────────────────────────────────────────────────

    def __getitem__(self, key: Any) -> Any:
        return self._data[key]

    def __contains__(self, key: Any) -> bool:
        return key in self._data

    def __iter__(self) -> Iterator:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: Any, default: Any = None) -> Any:
        return self._data.get(key, default)

    def items(self):
        return self._data.items()

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    # ── Immutable update API ──────────────────────────────────────────────

    def set(self, key: Any, value: Any) -> FrozenMap:
        new = dict(self._data)
        new[key] = value
        return FrozenMap(new)

    def delete(self, key: Any) -> FrozenMap:
        new = dict(self._data)
        del new[key]
        return FrozenMap(new)

    # ── Hashable / equality ───────────────────────────────────────────────

    def __hash__(self) -> int:
        h = self._hash
        if h is None:
            h = hash(frozenset(self._data.items()))
            object.__setattr__(self, "_hash", h)
        return h

    def __eq__(self, other: object) -> bool:
        if isinstance(other, FrozenMap):
            return self._data == other._data
        return NotImplemented

    def __repr__(self) -> str:
        return f"FrozenMap({self._data!r})"

    # ── Hypothesis strategy ───────────────────────────────────────────────

    @classmethod
    def strategy(
        cls,
        keys,
        values,
        *,
        min_size: int = 0,
        max_size: int | None = None,
    ):
        """Return a Hypothesis strategy that generates ``FrozenMap`` instances.

        Parameters are forwarded to ``st.dictionaries()``.
        """
        import hypothesis.strategies as st

        kwargs: dict[str, Any] = {"min_size": min_size}
        if max_size is not None:
            kwargs["max_size"] = max_size
        return st.dictionaries(keys, values, **kwargs).map(cls)
