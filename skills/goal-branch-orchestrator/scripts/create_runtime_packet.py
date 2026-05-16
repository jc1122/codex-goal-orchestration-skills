#!/usr/bin/env python3
"""Create model-aware Codex CLI worker or reviewer packets for branch orchestration."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path, PurePosixPath


SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
INVALID_BRANCH_CHARS = set(" ~^:?*[\\")


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def require_safe_label(value: str, field: str) -> str:
    if not SAFE_LABEL_RE.fullmatch(value):
        raise SystemExit(f"{field} must match {SAFE_LABEL_RE.pattern}: {value!r}")
    return value


def safe_branch_name(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return not (
        any(char in INVALID_BRANCH_CHARS for char in value)
        or any(char.isspace() for char in value)
        or value.startswith(("/", "."))
        or value.endswith(("/", ".", ".lock"))
        or ".." in value
        or "@{" in value
        or "//" in value
    )


def normalize_owned_paths(values: list[str]) -> list[str]:
    normalized = []
    for value in values:
        if "\\" in value:
            raise SystemExit(f"owned paths must use POSIX '/' separators: {value!r}")
        if "//" in value:
            raise SystemExit(f"owned paths must not contain empty path segments: {value!r}")
        if value.startswith("./") or "/./" in value or value.endswith("/."):
            raise SystemExit(f"owned paths must not contain '.' path segments: {value!r}")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise SystemExit(f"owned paths must be repo-relative without traversal: {value!r}")
        normalized.append(path.as_posix())
    return normalized


def normalize_context_files(values: list[str]) -> list[str]:
    normalized = []
    for value in values:
        path = Path(value).expanduser().resolve()
        if not path.exists():
            raise SystemExit(f"context file does not exist: {path}")
        normalized.append(path.as_posix())
    return normalized


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

git -C {shell_quote(worktree)} rev-parse --show-toplevel >/dev/null

run_model() {{
  local label="$1"
  local model="$2"
  codex exec --ephemeral \\
    -m "$model" \\
    -C {shell_quote(worktree)} \\
    -s {sandbox} \\
    --json \\
    --output-schema "$(pwd)/{schema_name}" \\
    -o "$(pwd)/{output_name}" \\
    - < "$(pwd)/prompt.md" \\
    > "$(pwd)/events-${{label}}.jsonl" 2>&1
}}

if run_model primary {shell_quote(primary_model)}; then
  exit 0
fi
{dirty_guard}
run_model fallback {shell_quote(fallback_model)}
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

    packet_id = require_safe_label(args.packet_id, "packet-id")
    branch = args.branch
    if not safe_branch_name(branch):
        raise SystemExit(f"branch is not a safe git branch name: {branch!r}")
    worktree = Path(args.worktree).expanduser().resolve()
    owned_files = normalize_owned_paths(args.owned_file)
    context_files = normalize_context_files(args.context_file)

    packet_dir = Path(args.out_dir).expanduser().resolve() / packet_id
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
            packet_id,
            branch,
            str(worktree),
            schema_name,
            owned_files,
            context_files,
            load_task(args.task_file),
        ),
        encoding="utf-8",
    )
    launch_path = packet_dir / "launch.sh"
    launch_path.write_text(
        launch_for(
            args.role,
            str(worktree),
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
