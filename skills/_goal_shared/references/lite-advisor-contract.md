# Lite Advisor Contract

Lite advisors are optional, CLI-only, read-only helper packets. They consume explicit input files and write one advisory output file plus one telemetry file. Lite work is delegated through the opencode-worker-bridge `ds-flash-max` route (deepseek-v4-flash `--variant max`) under permission profile `read-only`. Lite output is never pass/fail evidence, never a mergeability verdict, never a scientific claim judgment, and never permission to skip validators or heavy reviewers. Determinism means a deterministic envelope around nondeterministic model text: fixed skill allowlist, fixed model string, the captured bridge control-script path (`opencode_worker.py`) and control version, immutable input/prompt/task hashes, regenerated prompt consistency checks, fail-closed validation, manifest-owned artifact paths, unrecorded-packet discovery, packet-local telemetry, and auditable status records.

Use Lite as a context router:

1. Read Lite output first.
2. Open only the cited original files or spans needed for verification.
3. Ignore Lite advice when it is missing, blocked, stale, invalid, contradicted by original files, or contradicted by deterministic validators.

Original files are mandatory for prompt audit, branch review, scientific claim judgment, merge readiness, validator failures, and Definition-of-Done evidence.

Lite receives focused context by default. Broad context is allowed only for preflight source digestion from a long report or roadmap. Do not give Lite full repository dumps, full event logs, or unrelated result histories. For branch and main runtime use, provide only the branch prompt, manifest excerpt, completed status/review files, selected read-first files, or blocked packet excerpts needed for the specific purpose.

## Allowed Purposes

Scripts enforce the purpose allowlist for the skill they live under:

- `goal-preflight`: `preflight-decomposition`, `lint-repair`.
- `goal-main-orchestrator`: `audit-defect-summary`, `main-summary`.
- `goal-branch-orchestrator`: `branch-packet-planning`, `context-pack`, `worker-summary`, `blocked-triage`.
- `goal-plan-amender`: `amendment-summary`, `amendment-defect-summary`.

## Invocation

Create a packet with `scripts/create_lite_advice_packet.py`:

```bash
python3 "$GOAL_SKILLS_ROOT/<skill-name>/scripts/create_lite_advice_packet.py" \
  --packet-id B01-L01 \
  --purpose context-pack \
  --base-dir /absolute/path/to/repo-or-worktree \
  --out-dir /absolute/path/to/plans/orchestration/<job-id>/lite \
  --input-file /absolute/path/to/repo-or-worktree/plans/orchestration/<job-id>/branches/B01.prompt.md
```

All `--input-file` paths must be inside `--base-dir`; use the repository root as `--base-dir` when one Lite packet needs both orchestration bundle files and branch worktree files.

Packet ids are immutable by default. If the packet directory already exists, the generator fails. Pass `--replace` only when intentionally deleting and regenerating that packet.

Generated launchers capture the absolute bridge control-script path (`opencode_worker.py`), its control version, the `task.md` hash, and the `prompt.md` hash in `input-files.json`, then delegate through the bridge:

```bash
python3 /absolute/path/to/opencode-worker-bridge/scripts/opencode_worker.py delegate \
  --provider deepseek --model deepseek-v4-flash --variant max \
  --permission-profile read-only
```

The launcher rehashes every input, rehashes `task.md`, rehashes `prompt.md`, and re-resolves the captured bridge control script and version before delegating. It writes `telemetry.json` next to `advice.json` on every terminal path. The validator rehashes every input, rehashes `task.md`, regenerates `prompt.md` from `input-files.json` plus `task.md`, verifies packet telemetry, and verifies the captured bridge control-script path/version for non-blocked advice. If the bridge control script is unavailable or missing, its version changed, inputs changed, the prompt or task changed, quota is exhausted, or output is invalid, the launcher writes blocked `advice.json`; the parent workflow continues unless the user explicitly required Lite.

For offline fixture capture, set `GOAL_LITE_OFFLINE_BRIDGE_METADATA=1` plus `GOAL_LITE_BRIDGE_CONTROL_SCRIPT=/abs/opencode_worker.py` and `GOAL_LITE_BRIDGE_CONTROL_VERSION=<captured>`; the live deepseek delegate is never invoked at packet creation in that mode.

## Output

Lite advice must include:

- `packet_id`
- `role: "lite_advisor"`
- `purpose`
- `status: "ok" | "partial" | "blocked"`
- `source_files` with relative path, `sha256:<hex>`, byte size, and reason
- `recommended_reads` with relative path, anchor, and reason
- `risk_flags` preserving `unsupported`, `unresolved`, `negative`, `weakened`, `probe-only`, and `blocked`
- `advice` object
- `summary`
- `blockers`
- `commands_run`

For all non-`preflight-decomposition` purposes, `recommended_reads` may only cite explicit Lite input files. `preflight-decomposition` may suggest additional follow-up paths, but the parent agent must open and verify originals before acting.

`telemetry.json` must include `packet_id`, `role: "lite_advisor"`, the fixed `ds-flash-max` bridge attempt with provider/model id and `--variant max`, `called`/`accepted` booleans, prompt/output/log character and byte counts, and any token counts that the bridge exposes in logs. Character and byte counts are the deterministic spending proxy when token counts are unavailable.

Validate advice explicitly before using it:

```bash
python3 "$GOAL_SKILLS_ROOT/<skill-name>/scripts/validate_lite_advice.py" \
  --advice /absolute/path/to/lite/B01-L01/advice.json \
  --inputs /absolute/path/to/lite/B01-L01/input-files.json
```

When preflight used or ignored a Lite packet, `job.manifest.json.preflight_lite_advice` must include a record with `packet_id`, `purpose`, `status`, `disposition` (`used`, `ignored`, or `unused`), manifest-relative `advice_path`, manifest-relative `inputs_path`, exact `source_files`, exact `validation_command`, `validation_status`, `validation_defects`, and `reason`. Preflight Lite paths are exactly `lite/<packet_id>/advice.json` and `lite/<packet_id>/input-files.json`; if no preflight Lite was used, record `preflight_lite_advice: []`. The preflight linter scans `lite/` and fails on unrecorded preflight Lite packets or non-canonical validation commands.

When a runtime orchestrator used or ignored a Lite packet, its branch/main status must include a `lite_advice` record with `packet_id`, `purpose`, `status`, `disposition`, absolute manifest-owned `advice_path`, absolute manifest-owned `inputs_path`, exact `source_files`, exact `validation_command`, `validation_status`, `validation_defects`, and `reason`. Runtime manifest-owned paths are exactly `<manifest-dir>/lite/<packet_id>/advice.json` and `<manifest-dir>/lite/<packet_id>/input-files.json`. If no runtime Lite packet was used, status must contain `lite_advice: []`. Runtime validators scan `lite/` for relevant main/branch Lite packets and fail if they are not recorded or use a non-canonical validation command.
