# poes-enforce — a pi package

Enforces **Proof-Oriented Event Sourcing** in a [pi](https://pi.dev) coding
session. It steers the model to write every event-sourced aggregate with a POES
proof, validates each aggregate the moment it's written, and drives a **repair
loop** — feeding the counterexample back into the model's own agent loop — until
the proof passes.

Ships as a self-contained **pi package** bundling four resources:

| Resource | File | Role |
|----------|------|------|
| Extension | `index.ts` | validate-on-write, repair loop, backstop, commands |
| Skill | `skills/poes/SKILL.md` | the full POES API reference (`/skill:poes`) |
| Prompt template | `prompts/poes.md` | the `/poes` workflow command (generate → verify → repair) |
| Validator | `scripts/poes_validate.py` | deterministic proof runner |

## Install

```bash
pi install -l ./poes-enforce      # project-local (.pi); drop -l for global (~/.pi)
pip install poes                  # the library the validator imports
```

`pi install` records the package in `settings.json` (`packages: [...]`) and pi
auto-loads its extension, skill, and prompt — no `-e` flags, no manual wiring.
(Verified against pi 0.74: the packaged `poes_verify` tool loads headlessly with
no explicit path.) You can also `pi install git:...` / `npm:...` once published.

**Requirement:** the interpreter the validator calls (`python`, or `$POES_PYTHON`)
must be able to `import poes`. If not, enforcement surfaces `pip install poes`
rather than failing silently.

## The workflow (`/poes`)

`/poes <describe the aggregate>` expands the prompt template in `prompts/poes.md`,
which pins the model to the exact workflow: design → write with a
`verify() -> CheckResult` → call `poes_verify` → repair from the counterexample →
done only when all proofs pass. This is pi's native way to make the harness follow
a fixed workflow (pi has no separate "flows" feature; a prompt template + the
enforcement extension is the equivalent).

## How enforcement works

**Generation steering.** The `poes_verify` tool's `promptGuidelines` inject the
POES contract into the system prompt (frozen state, a `verify()` entrypoint, an
invariant per rule, a transition per event type, `expect_transitions`,
proof-of-work), and point the model at the bundled skill.

**Validate-on-write + repair loop.** When the model writes/edits a `.py` file
that looks like an aggregate (`@dataclass(frozen=True)` + `apply`/`decide`/event
markers), the `tool_result` hook runs `poes_validate.py` right there and, if the
proof is missing or failing, **appends the counterexample and repair instructions
to the write result the model receives**. The model then repairs within its own
run; each repair write re-enters the check, forming the loop. It is capped per
file at `POES_MAX_REPAIRS` attempts (default 4), after which it tells the model to
stop and report the blocker.

> This design was chosen after testing against pi 0.74: `agent_settled` never
> fires in headless/RPC and `sendMessage(triggerTurn)` doesn't reliably start a
> turn there, but a `tool_result` handler's returned content **is** awaited and
> shown to the model — so riding the tool loop is the robust way to enforce.

**Backstop.** `agent_end` fires in every mode; it records the final verdict
(audit + notification) so an unproven aggregate is never silently accepted.

Modes (`POES_ENFORCE` env or `/poes-enforce`): **block** (default — inject repair
instructions), **warn** (note only), **off**.

## Commands

- `/poes <spec>` — run the full generate-verify-repair workflow.
- `/poes-verify [file]` — verify a file (or all tracked aggregates).
- `/poes-status` — show mode and each tracked aggregate's status.
- `/poes-enforce [off|warn|block]` — get/set enforcement mode.
- `/poes-skill` — open the bundled POES API reference.

## Configuration

| Setting | Where | Default | Meaning |
|---------|-------|---------|---------|
| enforcement mode | `POES_ENFORCE` / `/poes-enforce` | `block` | `off` \| `warn` \| `block` |
| repair attempts/file | `POES_MAX_REPAIRS` | `4` | cap on automatic repair rounds |
| Python interpreter | `POES_PYTHON` | `python` | interpreter for the validator |
| audit log | `POES_ENFORCE_LOG` | (off) | path to append gate/repair events for CI evidence |

## The contract the model must satisfy

Full policy: [`references/ENFORCEMENT.md`](references/ENFORCEMENT.md). Each
aggregate file must expose:

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

A module-level `POES_CHECK` builder (or a single module-level `CheckBuilder`) also
works. `verify()` must **return** the result and must not `sys.exit()`.

## Validator CLI (usable standalone)

```bash
python scripts/poes_validate.py <target.py> [--out result.json] [--entrypoint NAME]
```

## API-version notes

Built against pi 0.74 docs + live testing. `Type` is imported from
`@sinclair/typebox` (bundled with pi); if your build re-exports it elsewhere,
adjust the import. Event/context objects are typed loosely to survive version
drift.
