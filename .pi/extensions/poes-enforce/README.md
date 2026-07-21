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

## Install it anywhere (standalone)

This extension is self-contained — the whole `poes-enforce/` folder is the
deliverable. To add POES enforcement to any project:

```bash
# 1. Drop the folder into the project's pi extensions directory
mkdir -p .pi/extensions
cp -r /path/to/poes-enforce .pi/extensions/

# 2. Install the POES library into the interpreter the validator uses
pip install poes
```

That's it. pi discovers `.pi/extensions/*/index.ts` automatically for a trusted
project, and enforcement starts in `block` mode. Nothing in the extension
assumes it lives inside the poes source tree.

To load it from some other location instead, point pi `settings.json` at it:

```json
{
  "extensions": ["/abs/path/to/poes-enforce"]
}
```

**Requirement:** the interpreter the extension calls (`python` by default, or
`$POES_PYTHON`) must be able to `import poes`. If it can't, the validator reports
`pip install poes` and enforcement surfaces a warning rather than failing
silently.

## Configuration

| Setting | Where | Default | Meaning |
|---------|-------|---------|---------|
| enforcement mode | `POES_ENFORCE` env, or `/poes-enforce` | `block` | `off` \| `warn` \| `block` |
| Python interpreter | `POES_PYTHON` env | `python` | interpreter used to run the validator |

## Commands

- `/poes-verify [file]` — verify a file, or all tracked aggregates if omitted.
- `/poes-status` — show the mode and each tracked aggregate's status.
- `/poes-enforce [off|warn|block]` — get or set enforcement mode for the session.
- `/poes-skill` — open the POES API reference bundled with this extension.

## Bundled skill

The full POES API reference ships **inside** the extension at
`skills/poes/SKILL.md` — the extension does not depend on any repo-specific
`.claude/skills/...` path. pi discovers it as `/skill:poes` (declared in
`package.json`), the model is pointed at its absolute path in the `poes_verify`
guidelines, and `/poes-skill` opens it directly. It's a snapshot of the canonical
skill from the poes repo; refresh it if the POES API changes.

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
