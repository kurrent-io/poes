#!/usr/bin/env python
"""POES validation runner — invoked by the poes-enforce pi extension.

Contract
--------
    python poes_validate.py <target.py> [--out result.json] [--entrypoint NAME]

It imports the target module, finds a POES verification entrypoint, runs it,
and writes a single JSON object describing the outcome to ``--out`` (and, as a
backup, prints it to stdout prefixed with ``__POES_RESULT__``).

Entrypoint discovery (in order):
  1. ``--entrypoint NAME`` if given and callable / present
  2. a module-level ``verify`` callable
  3. a module-level ``POES_CHECK`` CheckBuilder (``.run()`` is called)
  4. exactly one module-level CheckBuilder instance (``.run()`` is called)

The entrypoint must yield a POES ``CheckResult`` (anything with an
``all_passed`` attribute). A ``verify()`` that returns ``None`` is treated as a
policy failure — POES aggregates must ``return builder.run()``.

Exit code is 0 when the aggregate is verified AND ``all_passed`` is true,
otherwise 1. The extension reads the JSON, not the exit code, but the code is
kept meaningful for direct CLI use.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
import traceback
from contextlib import redirect_stdout
from pathlib import Path

RESULT_PREFIX = "__POES_RESULT__"


def _find_repo_root(start: Path) -> Path | None:
    """Walk up from *start* looking for a pyproject.toml (the poes repo root)."""
    for parent in [start, *start.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return None


def _ensure_poes_importable(target: Path) -> bool:
    """Make the ``poes`` package importable, preferring an installed copy.

    Returns True if ``poes`` can be imported after best-effort path setup. A
    standalone install (extension dropped into an arbitrary project) relies on
    ``pip install poes``; the repo-``src`` fallback only helps when running from
    inside the poes source tree.
    """
    try:
        import poes  # noqa: F401
        return True
    except ImportError:
        pass
    root = _find_repo_root(target.resolve())
    if root is not None:
        src = root / "src"
        if src.exists() and str(src) not in sys.path:
            sys.path.insert(0, str(src))
    try:
        import poes  # noqa: F401
        return True
    except ImportError:
        return False


def _import_module(target: Path):
    """Import *target* as a standalone module (name != '__main__')."""
    module_name = f"_poes_target_{abs(hash(str(target.resolve())))}"
    spec = importlib.util.spec_from_file_location(module_name, target)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module spec for {target}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _is_check_result(obj) -> bool:
    return hasattr(obj, "all_passed")


def _is_check_builder(obj) -> bool:
    # Duck-type a CheckBuilder without importing the class (version-tolerant).
    return hasattr(obj, "run") and hasattr(obj, "with_invariant") and hasattr(obj, "with_transition")


def _pick_entrypoint(module, explicit: str | None):
    """Return (label, callable_returning_CheckResult) or (reason, None)."""
    if explicit:
        obj = getattr(module, explicit, None)
        if obj is None:
            return f"entrypoint '{explicit}' not found in module", None
        if callable(obj):
            return explicit, obj
        if _is_check_builder(obj):
            return explicit, obj.run
        return f"entrypoint '{explicit}' is neither callable nor a CheckBuilder", None

    verify = getattr(module, "verify", None)
    if callable(verify):
        return "verify", verify

    poes_check = getattr(module, "POES_CHECK", None)
    if poes_check is not None and _is_check_builder(poes_check):
        return "POES_CHECK", poes_check.run

    builders = [
        (name, val)
        for name, val in vars(module).items()
        if not name.startswith("_") and _is_check_builder(val)
    ]
    if len(builders) == 1:
        name, builder = builders[0]
        return name, builder.run

    if len(builders) > 1:
        names = ", ".join(n for n, _ in builders)
        return (
            f"multiple CheckBuilders found ({names}); expose a single verify() "
            "that returns builder.run()"
        ), None

    return (
        "no POES verification entrypoint found — expose `def verify() -> CheckResult` "
        "that builds a Check.define(...) and returns builder.run()"
    ), None


def _prepare_import_path(target: Path) -> None:
    """Put the target's directory (and its project root / src) on sys.path so
    aggregates that import sibling or package-relative modules validate."""
    for p in (
        str(target.resolve().parent),
        *([str(r), str(r / "src")] if (r := _find_repo_root(target.resolve())) else []),
    ):
        if p and Path(p).exists() and p not in sys.path:
            sys.path.insert(0, p)


def _blank_result(target: Path) -> dict:
    return {
        "file": str(target),
        # kind classifies the outcome so the caller need not parse `reason`:
        # passed | proof_failed | entrypoint_missing | import_error | exception
        # | bad_return | env_error | not_found | crash | unknown
        "kind": "unknown",
        "entrypoint": None,
        "ran": False,
        "verified": False,
        "all_passed": False,
        "property_proofs": 0,
        "property_failed": 0,
        "exploration_passed": False,
        "states_explored": 0,
        "temporal_passed": False,
        "temporal_properties_checked": 0,
        "duration_ms": 0,
        "counterexample": None,
        "reason": None,
        "error": None,
    }


def validate(target: Path, entrypoint: str | None) -> dict:
    result = _blank_result(target)

    if not target.exists():
        result["kind"] = "not_found"
        result["reason"] = f"file not found: {target}"
        return result

    if not _ensure_poes_importable(target):
        result["kind"] = "env_error"
        result["reason"] = "the 'poes' package is not installed — run: pip install poes"
        result["error"] = "ModuleNotFoundError: No module named 'poes'"
        return result

    _prepare_import_path(target)

    # Import — capture the module's own stdout so POES banners don't pollute ours.
    captured = io.StringIO()
    try:
        with redirect_stdout(captured):
            module = _import_module(target)
    except Exception as exc:  # noqa: BLE001
        result["kind"] = "import_error"
        result["error"] = f"import failed: {exc}"
        result["reason"] = "module could not be imported"
        return result

    label, fn = _pick_entrypoint(module, entrypoint)
    if fn is None:
        result["kind"] = "entrypoint_missing"
        result["reason"] = label
        return result

    result["entrypoint"] = label

    try:
        with redirect_stdout(captured):
            check_result = fn()
    except SystemExit as exc:
        result["kind"] = "exception"
        result["error"] = f"entrypoint called sys.exit({exc.code})"
        result["reason"] = "verify() must not exit the process; return a CheckResult"
        return result
    except Exception as exc:  # noqa: BLE001
        result["kind"] = "exception"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["reason"] = "verification entrypoint raised an exception"
        return result

    result["ran"] = True

    if not _is_check_result(check_result):
        result["kind"] = "bad_return"
        result["reason"] = (
            f"entrypoint '{label}' returned {type(check_result).__name__}, not a CheckResult; "
            "return builder.run()"
        )
        return result

    result["verified"] = True
    for attr in (
        "all_passed",
        "property_proofs",
        "property_failed",
        "exploration_passed",
        "states_explored",
        "temporal_passed",
        "temporal_properties_checked",
        "duration_ms",
        "counterexample",
    ):
        if hasattr(check_result, attr):
            result[attr] = getattr(check_result, attr)

    result["kind"] = "passed" if result["all_passed"] else "proof_failed"
    if not result["all_passed"]:
        result["reason"] = result["counterexample"] or "one or more POES proofs failed"

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run POES verification on a target module.")
    parser.add_argument("target", help="Path to the Python file defining the aggregate.")
    parser.add_argument("--out", help="Write the JSON result to this path.")
    parser.add_argument("--entrypoint", help="Name of the verification entrypoint to call.")
    args = parser.parse_args(argv)

    target = Path(args.target)
    try:
        result = validate(target, args.entrypoint)
    except Exception as exc:  # noqa: BLE001 — never crash the runner
        result = _blank_result(target)
        result["kind"] = "crash"
        result["error"] = f"unexpected: {exc}\n{traceback.format_exc()}"
        result["reason"] = "validator crashed"

    payload = json.dumps(result)

    if args.out:
        try:
            Path(args.out).write_text(payload, encoding="utf-8")
        except OSError as exc:
            print(f"warning: could not write --out file: {exc}", file=sys.stderr)

    # Backup channel: the extension parses the last line starting with the prefix.
    print(f"{RESULT_PREFIX} {payload}")

    return 0 if (result["verified"] and result["all_passed"]) else 1


if __name__ == "__main__":
    sys.exit(main())
