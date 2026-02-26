"""Proof-of-work document generation from CheckBuilder configuration."""

from __future__ import annotations

import dataclasses
import inspect
import re
import textwrap
from typing import TYPE_CHECKING, Callable

from .temporal import AlwaysEventually, Eventually, LeadsTo

if TYPE_CHECKING:
    from .check import CheckBuilder


def _extract_lambda_source(fn: Callable) -> str:
    """Extract the body of a lambda from source. Falls back to repr for non-lambdas."""
    try:
        src = inspect.getsource(fn).strip()
        src = textwrap.dedent(src)
        # Collapse multi-line continuations into a single line
        src = re.sub(r'\s*\n\s*', ' ', src)
        # Find 'lambda ...:' and extract body
        match = re.search(r'lambda\s+[^:]+:\s*(.+)', src)
        if not match:
            return repr(fn)
        body = match.group(1).strip()
        # Strip trailing comma
        body = re.sub(r',\s*$', '', body).strip()
        # Strip only unbalanced trailing closing parens
        while body and body[-1] == ')':
            if body.count('(') < body.count(')'):
                body = body[:-1].rstrip()
            else:
                break
        return body
    except (OSError, TypeError):
        return repr(fn)


def _type_name(tp: type) -> str:
    """Get a clean type name from a dataclass field type."""
    if isinstance(tp, type):
        return tp.__name__
    return str(tp)


def _math_ify(source: str) -> str:
    """Convert Python predicate source into lightweight math notation."""
    s = source
    s = s.replace(' and ', ' ∧ ')
    s = s.replace(' or ', ' ∨ ')
    s = s.replace('not ', '¬')
    s = s.replace(' not ', ' ¬')
    s = s.replace('==', '=')
    s = s.replace('!=', '≠')
    s = s.replace('>=', '≥')
    s = s.replace('<=', '≤')
    s = s.replace('True', '⊤')
    s = s.replace('False', '⊥')
    # s.field -> field (strip state variable prefix for readability)
    s = re.sub(r'\bs\.(\w+)', r'\1', s)
    s = re.sub(r'\bafter\.(\w+)', r"\1'", s)
    s = re.sub(r'\bbefore\.(\w+)', r'\1', s)
    # replace(s, field=val, ...) -> field' = val, ...
    m = re.match(r'^replace\(\s*s\s*,\s*(.+)\)$', s)
    if m:
        assignments = m.group(1)
        # Turn keyword assignments into primed equalities
        s = re.sub(r'(\w+)\s*=\s*', r"\1' = ", assignments)
    return s


def generate_proof_of_work(builder: CheckBuilder) -> str:
    """Generate a Markdown proof-of-work document from builder configuration."""
    sections: list[str] = []

    # Title
    sections.append(f"# {builder._name} — Proof of Work\n")

    # State Definition
    sections.append("## State Definition\n")
    fields = dataclasses.fields(builder._state_type)
    sections.append("| Field | Type |")
    sections.append("|-------|------|")
    for f in fields:
        sections.append(f"| {f.name} | {_type_name(f.type)} |")
    sections.append("")
    if builder._initial is not None:
        sections.append(f"**Initial state:** `{repr(builder._initial)}`\n")

    # Invariants
    if builder._invariants:
        sections.append("## Invariants\n")
        for inv_name, inv_fn in builder._invariants:
            src = _extract_lambda_source(inv_fn)
            sections.append(f"- **{inv_name}**: `{_math_ify(src)}`")
        sections.append("")

    # State Transitions — Mermaid diagram
    all_transitions = []
    for name, guard, apply_fn, ensures_name, ensures in builder._transitions:
        all_transitions.append({
            "name": name,
            "params": None,
            "guard": guard,
            "apply": apply_fn,
            "ensures_name": ensures_name,
            "ensures": ensures,
        })
    for name, param_strats, guard, apply_fn, ensures_name, ensures in builder._parametric_transitions:
        all_transitions.append({
            "name": name,
            "params": param_strats,
            "guard": guard,
            "apply": apply_fn,
            "ensures_name": ensures_name,
            "ensures": ensures,
        })

    if all_transitions:
        sections.append("## State Transitions\n")
        sections.append("```mermaid")
        sections.append("stateDiagram-v2")

        if builder._state_diagram:
            # Rich multi-state diagram from explicit edges
            for from_st, to_st, label in builder._state_diagram:
                sections.append(f"    {from_st} --> {to_st} : {label}")
        else:
            # Flat fallback — all transitions loop on Active
            sections.append("    [*] --> Active")
            for t in all_transitions:
                guard_src = _math_ify(_extract_lambda_source(t["guard"]))
                label = t["name"]
                if t["params"]:
                    param_names = ", ".join(t["params"].keys())
                    label = f"{t['name']}({param_names})"
                sections.append(f"    Active --> Active : {label} [{guard_src}]")

        sections.append("```\n")

        # Transition details as tables with math notation
        for t in all_transitions:
            header = t["name"]
            if t["params"]:
                param_names = ", ".join(t["params"].keys())
                header += f" *(parametric: {param_names})*"

            sections.append(f"### {header}\n")

            guard_src = _math_ify(_extract_lambda_source(t["guard"]))
            apply_src = _math_ify(_extract_lambda_source(t["apply"]))
            ensures_src = _math_ify(_extract_lambda_source(t["ensures"]))

            sections.append("| | |")
            sections.append("|---|---|")
            sections.append(f"| **Guard** | `{guard_src}` |")
            sections.append(f"| **Effect** | `{apply_src}` |")
            sections.append(f"| **Postcondition** | `{ensures_src}` |")
            sections.append("")

    # Temporal Properties
    if builder._temporal_properties:
        sections.append("## Temporal Properties\n")
        for prop in builder._temporal_properties:
            if isinstance(prop, Eventually):
                src = _math_ify(_extract_lambda_source(prop.predicate))
                sections.append(f"- **Eventually**({prop.name}): `{src}`")
            elif isinstance(prop, LeadsTo):
                trigger_src = _math_ify(_extract_lambda_source(prop.trigger))
                response_src = _math_ify(_extract_lambda_source(prop.response))
                sections.append(f"- **LeadsTo**({prop.name}): `{trigger_src}` ⟹ `{response_src}`")
            elif isinstance(prop, AlwaysEventually):
                src = _math_ify(_extract_lambda_source(prop.predicate))
                sections.append(f"- **AlwaysEventually**({prop.name}): `{src}`")
        sections.append("")

    return "\n".join(sections)
