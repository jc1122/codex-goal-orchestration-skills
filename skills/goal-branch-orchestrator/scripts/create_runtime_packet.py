#!/usr/bin/env python3
"""Create model-aware Codex CLI worker or reviewer packets for branch orchestration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def status_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "packet_id",
            "role",
            "status",
            "branch",
            "worktree",
            "changed_files",
            "commands_run",
            "tests",
            "blockers",
            "handoff",
        ],
        "properties": {
            "packet_id": {"type": "string"},
            "role": {"const": "worker"},
            "status": {"enum": ["pass", "partial", "blocked", "failed"]},
            "branch": {"type": "string"},
            "worktree": {"type": "string"},
            "changed_files": {"type": "array", "items": {"type": "string"}},
            "commands_run": {"type": "array", "items": {"type": "string"}},
            "tests": {"type": "array", "items": {"type": "string"}},
            "blockers": {"type": "array", "items": {"type": "string"}},
            "handoff": {"type": "string"},
        },
    }


def review_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "packet_id",
            "role",
            "verdict",
            "findings",
            "commands_run",
            "verification_gaps",
            "residual_risks",
            "summary",
        ],
        "properties": {
            "packet_id": {"type": "string"},
            "role": {"const": "reviewer"},
            "verdict": {"enum": ["mergeable", "mergeable_after_fixes", "blocked", "reject"]},
            "findings": {"type": "array", "items": {"type": "string"}},
            "commands_run": {"type": "array", "items": {"type": "string"}},
            "verification_gaps": {"type": "array", "items": {"type": "string"}},
            "residual_risks": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
    }


def optional_list(title: str, values: list[str]) -> str:
    if not values:
        return f"{title}: none"
    return title + ":\n" + "\n".join(f"- {value}" for value in values)


def load_task(path: str | None) -> str:
    if not path:
        return "- Replace this section with the bounded task objective before launch."
    return Path(path).expanduser().resolve().read_text(encoding="utf-8")


def prompt_for(
    role: str,
    packet_id: str,
    branch: str,
    worktree: str,
    schema_name: str,
    owned_files: list[str],
    context_files: list[str],
    task_text: str,
) -> str:
    if role == "reviewer":
        return f"""# Branch Reviewer Packet {packet_id}

You are Reviewer {packet_id}. Do not edit files.

Worktree: {worktree}
Branch: {branch}

{optional_list("Context files to read first", context_files)}

Before reviewing, run:

```bash
pwd
git status --short --branch
git diff --check HEAD
```

Review the branch against its prompt, worker status files, diffs, test evidence, and claim-boundary rules. Lead with findings ordered by severity. Ground findings in file/line references or command evidence where possible.

Return only JSON matching `{schema_name}`.
"""

    return f"""# Spark Worker Packet {packet_id}

You are Worker {packet_id}.

Worktree: {worktree}
Branch: {branch}

You are not alone in the codebase. Do not revert edits made by others. Own only the files/modules assigned here. If the task needs more than roughly 80k-100k tokens of context, stop and return `blocked` instead of broadening scope.

{optional_list("Owned files/modules", owned_files)}

{optional_list("Context files to read first", context_files)}

Before editing, run:

```bash
pwd
git status --short --branch
```

Task:

{task_text}

Return only JSON matching `{schema_name}`.
"""


def launch_for(
    role: str,
    worktree: str,
    schema_name: str,
    output_name: str,
    primary_model: str,
    fallback_model: str,
) -> str:
    sandbox = "read-only" if role == "reviewer" else "workspace-write"
    dirty_guard = ""
    if role == "worker":
        dirty_guard = f"""
if [ -s "$(pwd)/{output_name}" ]; then
  exit 1
fi

if [ -n "$(git -C {shell_quote(worktree)} status --porcelain)" ]; then
  echo "Primary worker failed after leaving dirty worktree; refusing fallback in same worktree." > "$(pwd)/fallback.blocked.txt"
  exit 2
fi
"""
    else:
        dirty_guard = f"""
if [ -s "$(pwd)/{output_name}" ]; then
  exit 1
fi
"""

    return f"""#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

run_model() {{
  local model="$1"
  codex exec --ephemeral \\
    -m "$model" \\
    -C {shell_quote(worktree)} \\
    -s {sandbox} \\
    --json \\
    --output-schema "$(pwd)/{schema_name}" \\
    -o "$(pwd)/{output_name}" \\
    - < "$(pwd)/prompt.md" \\
    > "$(pwd)/events-${{model}}.jsonl" 2>&1
}}

if run_model {shell_quote(primary_model)}; then
  exit 0
fi
{dirty_guard}
run_model {shell_quote(fallback_model)}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["worker", "reviewer"], required=True)
    parser.add_argument("--packet-id", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--task-file")
    parser.add_argument("--owned-file", action="append", default=[])
    parser.add_argument("--context-file", action="append", default=[])
    parser.add_argument("--worker-model", default="gpt-5.3-codex-spark")
    parser.add_argument("--worker-fallback-model", default="gpt-5.4-mini")
    parser.add_argument("--reviewer-model", default="gpt-5.5")
    parser.add_argument("--reviewer-fallback-model", default="gpt-5.4")
    args = parser.parse_args()

    packet_dir = Path(args.out_dir).expanduser().resolve() / args.packet_id
    packet_dir.mkdir(parents=True, exist_ok=True)

    if args.role == "reviewer":
        schema_name = "review.schema.json"
        output_name = "review.json"
        schema = review_schema()
        primary_model = args.reviewer_model
        fallback_model = args.reviewer_fallback_model
    else:
        schema_name = "status.schema.json"
        output_name = "status.json"
        schema = status_schema()
        primary_model = args.worker_model
        fallback_model = args.worker_fallback_model

    (packet_dir / schema_name).write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    (packet_dir / "prompt.md").write_text(
        prompt_for(
            args.role,
            args.packet_id,
            args.branch,
            str(Path(args.worktree).expanduser().resolve()),
            schema_name,
            args.owned_file,
            args.context_file,
            load_task(args.task_file),
        ),
        encoding="utf-8",
    )
    launch_path = packet_dir / "launch.sh"
    launch_path.write_text(
        launch_for(
            args.role,
            str(Path(args.worktree).expanduser().resolve()),
            schema_name,
            output_name,
            primary_model,
            fallback_model,
        ),
        encoding="utf-8",
    )
    os.chmod(launch_path, 0o755)
    print(packet_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
