# Lite Advisor Contract

Lite advisors are optional, CLI-only, read-only helper packets. They consume explicit input files and write one advisory output file. Lite output is never pass/fail evidence, never a mergeability verdict, never a scientific claim judgment, and never permission to skip validators or heavy reviewers.

Use Lite as a context router:

1. Read Lite output first.
2. Open only the cited original files or spans needed for verification.
3. Ignore Lite advice when it is missing, blocked, stale, invalid, contradicted by original files, or contradicted by deterministic validators.

Original files are mandatory for prompt audit, branch review, scientific claim judgment, merge readiness, validator failures, and Definition-of-Done evidence.

Lite receives focused context by default. Broad context is allowed only for preflight source digestion from a long report or roadmap. Do not give Lite full repository dumps, full event logs, or unrelated result histories. For branch and main runtime use, provide only the branch prompt, manifest excerpt, completed status/review files, selected read-first files, or blocked packet excerpts needed for the specific purpose.

## Allowed Purposes

- `preflight-decomposition`: source brief/report/roadmap to branch and work-item advice.
- `lint-repair`: deterministic preflight lint failures to minimal repair advice.
- `audit-defect-summary`: prompt-audit defects to a concise handoff.
- `branch-packet-planning`: branch prompt and manifest entry to worker packet advice.
- `context-pack`: selected branch context to targeted worker read advice.
- `worker-summary`: completed worker statuses and diffs to summary advice.
- `blocked-triage`: blocked worker status and failure excerpts to next repair advice.
- `main-summary`: completed branch statuses/reviews to main handoff advice.

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

Generated launchers run:

```bash
gemini --model gemini-3.1-flash-lite-preview \
  --approval-mode plan \
  --skip-trust \
  --output-format text \
  -p "$(cat prompt.md)"
```

The launcher extracts JSON between `BEGIN_LITE_ADVICE_JSON` and `END_LITE_ADVICE_JSON`, writes `advice.json`, and validates it with `scripts/validate_lite_advice.py`. If Gemini is unavailable, quota-limited, or emits invalid output, the launcher writes a blocked `advice.json`; the parent workflow continues unless the user explicitly required Lite.

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

Validate advice explicitly before using it:

```bash
python3 "$GOAL_SKILLS_ROOT/<skill-name>/scripts/validate_lite_advice.py" \
  --advice /absolute/path/to/lite/B01-L01/advice.json \
  --inputs /absolute/path/to/lite/B01-L01/input-files.json
```
