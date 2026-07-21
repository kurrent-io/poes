# poes-enforce — pi extension

Enforces **Proof-Oriented Event Sourcing** in a [pi](https://pi.dev) coding
session. It steers the model to write every event-sourced aggregate with a POES
proof, and it gates the turn: if a generated aggregate isn't proven, the
extension re-engages the model with the counterexample until it is.

## What it does

**Generation.** Registers a `poes_verify` tool whose `promptGuidelines` inject
the POES contract into the system prompt — frozen state, a `verify() ->
CheckResult` entrypoint, an invariant per rule, a transition per event type,
`expect_transitions`, and a proof-of-work document. The model is told to call
`poes_verify` after writing an aggregate.

**Validation.** On every `write`/`edit` of a `.py` file, the extension reads the
file from disk and classifies it. A file is an *aggregate* when it has a
`@dataclass(frozen=True)` state plus event-sourcing markers (`apply`/`replace`,
`decide`, events, KurrentDB). When the agent settles, each tracked aggregate is
run through `scripts/poes_validate.py`:

- **block** (default): a missing or failing proof re-engages the model with the
  reason/counterexample, up to 3 rounds per user task, then hands back to you.
- **warn**: failures are surfaced via notifications only.
- **off**: no validation runs.

The validator is the deterministic core: it imports the module, finds the
verification entrypoint, runs it, and reports `all_passed` plus proof counts,
states explored, and any counterexample. Exit 0 iff verified and all proofs
pass.

## Enable it

This extension lives in `.pi/extensions/poes-enforce/`, which pi discovers
automatically for a trusted project (`.pi/extensions/*/index.ts`). Nothing else
is required. To load it from elsewhere, add the path to pi `settings.json`:

```json
{
  "extensions": [".pi/extensions/poes-enforce"]
}
```

Requires the `poes` package importable by the interpreter the extension calls
(it falls back to `<repo>/src` if `poes` isn't installed):

```bash
pip install -e ".[dev]"
```

## Configuration

| Setting | Where | Default | Meaning |
|---------|-------|---------|---------|
| enforcement mode | `POES_ENFORCE` env, or `/poes-enforce` | `block` | `off` \| `warn` \| `block` |
| Python interpreter | `POES_PYTHON` env | `python` | interpreter used to run the validator |

## Commands

- `/poes-verify [file]` — verify a file, or all tracked aggregates if omitted.
- `/poes-status` — show the mode and each tracked aggregate's status.
- `/poes-enforce [off|warn|block]` — get or set enforcement mode for the session.

## The contract the model must satisfy

Full policy: [`references/ENFORCEMENT.md`](references/ENFORCEMENT.md). In short,
each aggregate file must expose:

```python
def verify() -> CheckResult:
    builder = (
        Check.define("Name", StateClass)
        .with_initial(...)
        .with_field(...)          # every field
        .with_invariant(...)      # every rule
        .with_transition(...)     # every event type, with guard/apply/ensures
        .expect_transitions(N)
    )
    builder.generate_proof_of_work(path="proof.md")
    return builder.run()          # MUST return the CheckResult
```

A module-level `POES_CHECK` builder, or a single module-level `CheckBuilder`,
also works. `verify()` must **return** the result and must not `sys.exit()`.

## Validator CLI (usable standalone)

```bash
python .pi/extensions/poes-enforce/scripts/poes_validate.py <target.py> [--out result.json] [--entrypoint NAME]
```

## API-version notes

Written against <https://pi.dev/docs/latest/extensions>. Two spots may need a
tweak if your pi build differs:

- `Type` is imported from `@sinclair/typebox` (bundled with pi). If your build
  re-exports it (e.g. from `@earendil-works/pi-ai`), change the import.
- `pi.sendMessage(text, { triggerTurn: true })` drives the block-mode re-engage
  loop. If its signature differs, the gate falls back to a notification with the
  same detail. Event/context objects are typed loosely so the rest keeps working.
