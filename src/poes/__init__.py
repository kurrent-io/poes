"""POES — Proof-Oriented Event Sourcing."""

__version__ = "0.3.1"

from .check import Check, CheckBuilder, CheckResult
from .explorer import Transition
from .frozen_map import FrozenMap
from .persistence_check import PersistenceCheckResult
from .repository import Repository

__all__ = [
    "Check",
    "CheckBuilder",
    "CheckResult",
    "FrozenMap",
    "PersistenceCheckResult",
    "Repository",
    "Transition",
]
