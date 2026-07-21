/**
 * poes-enforce — a pi extension that enforces Proof-Oriented Event Sourcing.
 *
 * It works on two fronts:
 *
 *   1. Generation — a `poes_verify` tool plus system-prompt guidelines steer the
 *      model to write every event-sourced aggregate as a frozen dataclass with a
 *      `verify() -> CheckResult` entrypoint (invariants, transitions, temporal
 *      properties, proof-of-work).
 *
 *   2. Validation — whenever the model writes/edits a `.py` file that looks like
 *      an aggregate, the file is tracked. When the agent settles, the extension
 *      runs `scripts/poes_validate.py` on each tracked file. In `block` mode a
 *      failing or missing proof re-engages the model with the counterexample
 *      until it passes (capped, so the user is never trapped).
 *
 * The full policy lives in `references/ENFORCEMENT.md`.
 *
 * API notes: written against the extension reference at
 * https://pi.dev/docs/latest/extensions. `Type` comes from TypeBox (bundled with
 * pi); if your pi build re-exports it elsewhere, adjust the import below. Event
 * and context objects are typed loosely on purpose so the extension keeps working
 * across pi versions.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "@sinclair/typebox";
import { existsSync, readFileSync, writeFileSync, unlinkSync, appendFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, isAbsolute } from "node:path";
import { randomBytes } from "node:crypto";

type Strictness = "off" | "warn" | "block";

type FileStatus = "pending" | "missing-proof" | "passed" | "failed" | "error";

interface Tracked {
  status: FileStatus;
  reason: string | null;
  lastResult: ValidationResult | null;
}

interface ValidationResult {
  file: string;
  entrypoint: string | null;
  ran: boolean;
  verified: boolean;
  all_passed: boolean;
  property_proofs?: number;
  property_failed?: number;
  exploration_passed?: boolean;
  states_explored?: number;
  temporal_passed?: boolean;
  temporal_properties_checked?: number;
  duration_ms?: number;
  counterexample?: string | null;
  reason?: string | null;
  error?: string | null;
}

const RESULT_PREFIX = "__POES_RESULT__";
// Max automatic repair rounds per user task before handing back. Configurable
// via POES_MAX_REPAIRS.
const MAX_AUTO_FIX_ROUNDS = Number(process.env.POES_MAX_REPAIRS) || 4;

// Tool names that write file content. Matched case-insensitively; also matches
// any name containing write/edit/patch/create so it survives pi renames.
const WRITE_TOOL_HINTS = ["write", "edit", "patch", "create", "str_replace", "apply_patch"];

const PROMPT_GUIDELINES = [
  "Any event-sourced aggregate you write MUST ship with its POES proof in the same change.",
  "State is @dataclass(frozen=True); use poes.FrozenMap for map fields; build new state with dataclasses.replace().",
  "Expose `def verify() -> CheckResult` that builds Check.define(...) and returns builder.run() — never sys.exit().",
  "Cover every field with with_field/with_map_field, every rule with with_invariant, and every event type with a transition that has a guard, an apply, and a non-trivial ensures.",
  "Add .expect_transitions(N) matching the event-type count, and call .generate_proof_of_work(path=...) before .run().",
];

export default function (pi: ExtensionAPI) {
  // ── Configuration & state ────────────────────────────────────────────────
  let strictness: Strictness = (process.env.POES_ENFORCE as Strictness) || "block";
  const pythonCmd = process.env.POES_PYTHON || "python";

  // Resolve this extension's own directory without assuming a module system,
  // so the bundled validator and skill are found wherever the folder is dropped.
  const resolveExtDir = (): string => {
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const dir = (globalThis as any).__dirname ?? (typeof __dirname !== "undefined" ? __dirname : "");
      if (dir) return dir;
    } catch {
      /* __dirname not defined in this runtime */
    }
    return resolve(process.cwd(), ".pi/extensions/poes-enforce");
  };
  const extDir = resolveExtDir();
  const scriptPath = process.env.POES_VALIDATOR || join(extDir, "scripts", "poes_validate.py");
  const skillPath = join(extDir, "skills", "poes", "SKILL.md");

  // The POES API reference ships inside this extension, so the guidance points
  // at the bundled copy (not a repo-specific .claude path).
  const guidelines = [
    ...PROMPT_GUIDELINES,
    `Consult the POES skill for the full API before writing an aggregate: read ${skillPath} ` +
      "(bundled with this extension) or run /skill:poes / /poes-skill. After writing an aggregate, " +
      "call poes_verify — the poes-enforce gate also runs it automatically.",
  ];

  const tracked = new Map<string, Tracked>(); // absolute path -> status
  const repairAttempts = new Map<string, number>(); // path -> failed validations (repair-loop cap)

  // ── Helpers ────────────────────────────────────────────────────────────────
  // Optional audit trail: set POES_ENFORCE_LOG to a file path to record every
  // detection and gate decision (useful for CI evidence and debugging).
  const auditLog = (msg: string): void => {
    const path = process.env.POES_ENFORCE_LOG;
    if (!path) return;
    try {
      appendFileSync(path, `[${new Date().toISOString()}] ${msg}\n`);
    } catch {
      /* never let logging break enforcement */
    }
  };

  const isWriteTool = (name: string): boolean => {
    const n = (name || "").toLowerCase();
    return WRITE_TOOL_HINTS.some((h) => n.includes(h));
  };

  /** Collect .py path-like strings from arbitrary tool input (depth-limited). */
  const pyPathsFrom = (input: unknown, cwd: string, depth = 0): string[] => {
    const out: string[] = [];
    if (depth > 3 || input == null) return out;
    if (typeof input === "string") {
      if (input.toLowerCase().endsWith(".py")) {
        out.push(isAbsolute(input) ? input : resolve(cwd, input));
      }
      return out;
    }
    if (Array.isArray(input)) {
      for (const v of input) out.push(...pyPathsFrom(v, cwd, depth + 1));
      return out;
    }
    if (typeof input === "object") {
      for (const v of Object.values(input as Record<string, unknown>)) {
        out.push(...pyPathsFrom(v, cwd, depth + 1));
      }
    }
    return out;
  };

  const looksLikeAggregate = (src: string): boolean => {
    const frozen = /@dataclass\(\s*frozen\s*=\s*True\s*\)/.test(src);
    const fold = /def\s+apply\s*\(/.test(src) || /\breplace\s*\(/.test(src);
    const esMarkers =
      /\b(decide|append_to_stream|Repository|kurrentdb|event_from_json|event_type)\b/.test(src) ||
      /\bevent\b/i.test(src);
    return frozen && fold && esMarkers;
  };

  const isAggregateFile = (absPath: string): boolean => {
    if (!existsSync(absPath)) return false;
    try {
      return looksLikeAggregate(readFileSync(absPath, "utf-8"));
    } catch {
      return false;
    }
  };

  /** Forceful repair instructions injected into a failing write's tool result. */
  const repairInstructions = (rel: string, r: ValidationResult, attempt: number): string => {
    const why = r.counterexample || r.reason || "verification failed";
    return (
      `⛔ POES enforcement (block): ${rel} is NOT proven — ${why}\n` +
      "This is not done. Fix it now by editing the file (do not just reply):\n" +
      (r.entrypoint
        ? "  • A proof failed. The reason above tells you what broke — correct the guard, the apply, or the invariant. Do not weaken an invariant unless it is genuinely the wrong rule.\n"
        : "  • Add `def verify() -> CheckResult`: Check.define(...) with a field per state field, an invariant per rule, a transition per event type (guard + apply + a real ensures), .expect_transitions(N), then `return builder.run()`.\n") +
      `  • Unsure of the API? Read the POES skill: ${skillPath}\n` +
      "  • Then write the file again; POES re-checks automatically. You may also call poes_verify to confirm.\n" +
      `(repair attempt ${attempt}/${MAX_AUTO_FIX_ROUNDS})`
    );
  };

  /** Run the Python validator on one file and return the parsed result. */
  const runValidator = async (absPath: string): Promise<ValidationResult> => {
    const outPath = join(tmpdir(), `poes-${randomBytes(8).toString("hex")}.json`);
    let execText = "";
    try {
      const res: any = await pi.exec(
        pythonCmd,
        [scriptPath, absPath, "--out", outPath],
        { timeout: 180_000 },
      );
      execText = `${res?.stdout ?? res?.output ?? ""}\n${res?.stderr ?? ""}`;
    } catch (err: any) {
      execText = String(err?.message ?? err);
    }

    // Prefer the --out file; fall back to the sentinel line on stdout.
    let raw: string | null = null;
    try {
      if (existsSync(outPath)) {
        raw = readFileSync(outPath, "utf-8");
        unlinkSync(outPath);
      }
    } catch {
      /* ignore */
    }
    if (!raw) {
      const line = execText
        .split(/\r?\n/)
        .reverse()
        .find((l) => l.trim().startsWith(RESULT_PREFIX));
      if (line) raw = line.trim().slice(RESULT_PREFIX.length).trim();
    }

    if (!raw) {
      return {
        file: absPath,
        entrypoint: null,
        ran: false,
        verified: false,
        all_passed: false,
        reason:
          `could not run the POES validator (python="${pythonCmd}"). Is Python + poes ` +
          "installed? Set POES_PYTHON to your interpreter.",
        error: execText.slice(0, 2000),
      };
    }

    try {
      return JSON.parse(raw) as ValidationResult;
    } catch (err: any) {
      return {
        file: absPath,
        entrypoint: null,
        ran: false,
        verified: false,
        all_passed: false,
        reason: "validator produced unparseable output",
        error: `${err?.message}: ${raw.slice(0, 500)}`,
      };
    }
  };

  const statusFromResult = (r: ValidationResult): FileStatus => {
    if (r.reason && !r.ran && r.error && /could not run|installed/.test(r.reason)) return "error";
    if (!r.verified) return r.entrypoint ? "failed" : "missing-proof";
    return r.all_passed ? "passed" : "failed";
  };

  const relOrAbs = (p: string, cwd: string): string =>
    p.startsWith(cwd) ? p.slice(cwd.length).replace(/^[\\/]/, "") : p;

  const renderWidget = (cwd: string): string[] => {
    if (tracked.size === 0) return [`POES: no aggregates tracked (${strictness})`];
    const lines = [`POES enforcement: ${strictness}`];
    const glyph: Record<FileStatus, string> = {
      passed: "✓",
      failed: "✗",
      "missing-proof": "!",
      pending: "…",
      error: "⚠",
    };
    for (const [path, t] of tracked) {
      lines.push(`  ${glyph[t.status]} ${relOrAbs(path, cwd)} — ${t.status}`);
    }
    return lines;
  };

  const summarize = (r: ValidationResult, cwd: string): string => {
    const name = relOrAbs(r.file, cwd);
    if (r.verified && r.all_passed) {
      return `✓ ${name} — ${r.property_proofs ?? 0} proofs, ${r.states_explored ?? 0} states`;
    }
    return `✗ ${name} — ${r.reason || r.counterexample || "verification failed"}`;
  };

  // ── Generation: register the agent-facing verify tool ────────────────────
  pi.registerTool({
    name: "poes_verify",
    label: "POES Verify",
    description:
      "Run POES verification (property testing + state exploration + temporal properties) on a " +
      "Python file that defines an event-sourced aggregate. The file must expose `def verify() " +
      "-> CheckResult` (or a module-level POES_CHECK builder) that returns builder.run(). Returns " +
      "whether all proofs passed, plus counts and any counterexample.",
    promptSnippet:
      "poes_verify: prove an event-sourced aggregate with POES before considering the task done.",
    promptGuidelines: guidelines,
    parameters: Type.Object({
      file: Type.String({
        description: "Path to the .py file defining the aggregate and its verify() entrypoint.",
      }),
    }),
    async execute(_toolCallId: string, params: any, _signal: unknown, onUpdate: any, ctx: any) {
      const cwd = ctx?.cwd ?? process.cwd();
      const abs = isAbsolute(params.file) ? params.file : resolve(cwd, params.file);
      onUpdate?.({ content: [{ type: "text", text: `Verifying ${relOrAbs(abs, cwd)}…` }] });

      const result = await runValidator(abs);
      const status = statusFromResult(result);
      tracked.set(abs, { status, reason: result.reason ?? null, lastResult: result });
      ctx?.ui?.setWidget?.("poes", renderWidget(cwd));

      const ok = result.verified && result.all_passed;
      const text = ok
        ? `POES VERIFIED: ${relOrAbs(abs, cwd)}\n` +
          `  proofs: ${result.property_proofs ?? 0} passed, ${result.property_failed ?? 0} failed\n` +
          `  states explored: ${result.states_explored ?? 0}\n` +
          `  temporal properties: ${result.temporal_properties_checked ?? 0}\n` +
          `  duration: ${result.duration_ms ?? 0}ms`
        : `POES NOT VERIFIED: ${relOrAbs(abs, cwd)}\n` +
          `  reason: ${result.reason || result.counterexample || "unknown"}\n` +
          (result.error ? `  error: ${result.error}\n` : "") +
          "  Fix the aggregate (tighten a guard, correct apply, fix the invariant) or add the " +
          "missing verify() entrypoint, then run poes_verify again.";

      return { content: [{ type: "text", text }], details: result };
    },
  });

  // ── Enforcement: validate on write, drive repair in the model's own loop ──
  // The reliable mechanism (verified against pi 0.74): `tool_result` fires DURING
  // the agent run and its returned content is awaited and shown to the model. So
  // when the model writes an aggregate, we validate it right there and, if the
  // proof is missing or failing, append the counterexample + repair instructions
  // to the write result. The model then repairs within the same run — each repair
  // write re-enters this handler, forming a natural repair loop capped per file.
  // (Message injection via agent_settled/sendMessage is NOT used: agent_settled
  // never fires in headless/RPC, and sendMessage(triggerTurn) does not reliably
  // start a turn there.)
  pi.on("tool_result", async (event: any, ctx: any) => {
    try {
      if (strictness === "off" || !isWriteTool(event?.toolName)) return;
      const cwd = ctx?.cwd ?? process.cwd();
      const aggr = [...new Set(pyPathsFrom(event?.input, cwd))].filter(isAggregateFile);
      if (aggr.length === 0) return;

      const notes: string[] = [];
      for (const abs of aggr) {
        const rel = relOrAbs(abs, cwd);
        const result = await runValidator(abs);
        const status = statusFromResult(result);
        tracked.set(abs, { status, reason: result.reason ?? null, lastResult: result });

        if (status === "passed") {
          repairAttempts.delete(abs);
          notes.push(`✓ POES verified ${rel}: ${result.property_proofs ?? 0} proofs, ${result.states_explored ?? 0} states.`);
          auditLog(`write ${rel}: PASS`);
        } else if (status === "error") {
          notes.push(`⚠ POES could not run for ${rel}: ${result.reason}`);
          auditLog(`write ${rel}: ENV ERROR (${result.reason})`);
        } else if (strictness === "warn") {
          notes.push(`⚠ POES: ${rel} is not proven — ${result.reason || result.counterexample}.`);
          auditLog(`write ${rel}: FAIL (warn) ${result.reason}`);
        } else {
          const attempt = (repairAttempts.get(abs) ?? 0) + 1;
          repairAttempts.set(abs, attempt);
          if (attempt > MAX_AUTO_FIX_ROUNDS) {
            notes.push(
              `⛔ POES: ${rel} still unproven after ${MAX_AUTO_FIX_ROUNDS} repair attempts ` +
                `(${result.reason || result.counterexample}). Stop editing it and report the blocker to the user.`,
            );
            auditLog(`write ${rel}: FAIL, repair budget exhausted`);
          } else {
            notes.push(repairInstructions(rel, result, attempt));
            auditLog(`write ${rel}: FAIL, repair attempt ${attempt}/${MAX_AUTO_FIX_ROUNDS}`);
          }
        }
      }

      ctx?.ui?.setWidget?.("poes", renderWidget(cwd));
      if (notes.length === 0) return;
      // Append our enforcement note to the original write result the model sees.
      return {
        content: [
          ...(Array.isArray(event?.content) ? event.content : []),
          { type: "text", text: "\n— POES enforcement —\n" + notes.join("\n") },
        ],
      };
    } catch {
      /* never break the tool pipeline */
    }
  });

  // Backstop report: `agent_end` fires in every mode. It can't inject into the
  // finished run, but it records the final verdict (audit + notify) so an
  // unproven aggregate is never silently accepted.
  pi.on("agent_end", async (_e: any, ctx: any) => {
    if (strictness === "off") return;
    const cwd = ctx?.cwd ?? process.cwd();
    const unproven = [...tracked.entries()].filter(([p, t]) => t.status !== "passed" && existsSync(p));
    auditLog(`agent_end: tracked=${tracked.size} unproven=${unproven.length}`);
    if (unproven.length === 0) return;
    ctx?.ui?.notify?.(
      `POES: ${unproven.length} aggregate(s) still unproven: ` +
        unproven.map(([p]) => relOrAbs(p, cwd)).join(", "),
      strictness === "block" ? "error" : "warning",
    );
  });

  // ── Commands ──────────────────────────────────────────────────────────────
  pi.registerCommand("poes-verify", {
    description: "Run POES verification on a file (defaults to all tracked aggregates).",
    handler: async (args: string, ctx: any) => {
      const cwd = ctx?.cwd ?? process.cwd();
      const arg = (args || "").trim();
      const targets = arg
        ? [isAbsolute(arg) ? arg : resolve(cwd, arg)]
        : [...tracked.keys()];

      if (targets.length === 0) {
        ctx?.ui?.notify?.("POES: no file given and no aggregates tracked yet.", "info");
        return;
      }
      for (const abs of targets) {
        const result = await runValidator(abs);
        const status = statusFromResult(result);
        tracked.set(abs, { status, reason: result.reason ?? null, lastResult: result });
        const ok = result.verified && result.all_passed;
        ctx?.ui?.notify?.(summarize(result, cwd), ok ? "info" : "error");
      }
      ctx?.ui?.setWidget?.("poes", renderWidget(cwd));
    },
    getArgumentCompletions(prefix: string) {
      const items = [...tracked.keys()]
        .filter((p) => p.includes(prefix))
        .map((p) => ({ value: p, label: p }));
      return items.length ? items : null;
    },
  });

  pi.registerCommand("poes-skill", {
    description: "Show the POES API reference bundled with this extension.",
    handler: async (_args: string, ctx: any) => {
      if (!existsSync(skillPath)) {
        ctx?.ui?.notify?.(`Bundled POES skill not found at ${skillPath}`, "error");
        return;
      }
      const content = readFileSync(skillPath, "utf-8");
      if (ctx?.ui?.editor) {
        await ctx.ui.editor("POES skill", { content });
      } else {
        ctx?.ui?.notify?.(
          `POES skill is bundled at:\n${skillPath}\nOpen it, or run /skill:poes.`,
          "info",
        );
      }
    },
  });

  pi.registerCommand("poes-status", {
    description: "Show POES enforcement mode and tracked aggregate statuses.",
    handler: async (_args: string, ctx: any) => {
      const cwd = ctx?.cwd ?? process.cwd();
      const lines = renderWidget(cwd);
      ctx?.ui?.setWidget?.("poes", lines);
      ctx?.ui?.notify?.(lines.join("\n"), "info");
    },
  });

  pi.registerCommand("poes-enforce", {
    description: "Set enforcement mode: off | warn | block (no arg shows current).",
    handler: async (args: string, ctx: any) => {
      const mode = (args || "").trim().toLowerCase();
      if (!mode) {
        ctx?.ui?.notify?.(`POES enforcement is: ${strictness}`, "info");
        return;
      }
      if (mode !== "off" && mode !== "warn" && mode !== "block") {
        ctx?.ui?.notify?.(`Unknown mode "${mode}". Use off | warn | block.`, "error");
        return;
      }
      strictness = mode as Strictness;
      const cwd = ctx?.cwd ?? process.cwd();
      ctx?.ui?.setWidget?.("poes", renderWidget(cwd));
      ctx?.ui?.notify?.(`POES enforcement set to: ${strictness}`, "info");
    },
    getArgumentCompletions() {
      return [
        { value: "block", label: "block — re-engage the model until proofs pass (default)" },
        { value: "warn", label: "warn — surface failures but do not re-engage" },
        { value: "off", label: "off — disable enforcement" },
      ];
    },
  });

  // ── Session wiring ───────────────────────────────────────────────────────
  pi.on("session_start", async (_event: any, ctx: any) => {
    const cwd = ctx?.cwd ?? process.cwd();
    ctx?.ui?.setStatus?.("poes", `POES: ${strictness}`);
    ctx?.ui?.setWidget?.("poes", renderWidget(cwd));
  });
}
