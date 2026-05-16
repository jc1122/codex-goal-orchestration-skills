#!/usr/bin/env python3
"""Create model-aware worker or reviewer packets for branch orchestration."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path, PurePosixPath


SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
INVALID_BRANCH_CHARS = set(" ~^:?*[\\")
GEMINI_PRO_MODEL = "gemini-3.1-pro"
GEMINI_FLASH_MODEL = "gemini-3.1-flash"


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

    return f"""# Worker Packet {packet_id}

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
    worker_model: str,
    worker_fallback_model: str,
    reviewer_model: str,
    reviewer_fallback_model: str,
    gemini_command: str,
    gemini_approval_mode: str,
) -> str:
    sandbox = "read-only" if role == "reviewer" else "workspace-write"
    if role == "reviewer":
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

if run_model primary {shell_quote(reviewer_model)}; then
  exit 0
fi

if [ -s "$(pwd)/{output_name}" ]; then
  exit 1
fi

run_model fallback {shell_quote(reviewer_fallback_model)}
"""

    return f"""#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

git -C {shell_quote(worktree)} rev-parse --show-toplevel >/dev/null

packet_dir="$(pwd)"
prompt_path="$packet_dir/prompt.md"
schema_path="$packet_dir/{schema_name}"
output_path="$packet_dir/{output_name}"
gemini_command={shell_quote(gemini_command)}
gemini_approval_mode={shell_quote(gemini_approval_mode)}

worktree_dirty() {{
  [ -n "$(git -C {shell_quote(worktree)} status --porcelain)" ]
}}

guard_clean_for_fallback() {{
  local label="$1"
  if [ -s "$output_path" ]; then
    exit 1
  fi
  if worktree_dirty; then
    echo "$label failed after leaving dirty worktree; refusing fallback in same worktree." > "$packet_dir/fallback.blocked.txt"
    exit 2
  fi
}}

extract_status_json() {{
  local raw_path="$1"
  python3 - "$raw_path" "$schema_path" "$output_path" <<'PY'
import json
import sys
from pathlib import Path

raw_path = Path(sys.argv[1])
schema_path = Path(sys.argv[2])
output_path = Path(sys.argv[3])


def iter_json_objects(text):
    for start, char in enumerate(text):
        if char != "{{":
            continue
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            current = text[index]
            if in_string:
                if escape:
                    escape = False
                elif current == "\\\\":
                    escape = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{{":
                depth += 1
            elif current == "}}":
                depth -= 1
                if depth == 0:
                    yield text[start : index + 1]
                    break


def validate_type(value, expected_type):
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "object":
        return isinstance(value, dict)
    return True


def validate(instance, schema):
    if schema.get("type") == "object" and not isinstance(instance, dict):
        raise ValueError("status is not a JSON object")
    required = schema.get("required", [])
    missing = [field for field in required if field not in instance]
    if missing:
        raise ValueError(f"status missing required fields: {{', '.join(missing)}}")
    properties = schema.get("properties", {{}})
    if schema.get("additionalProperties") is False:
        extra = sorted(set(instance) - set(properties))
        if extra:
            raise ValueError(f"status has unsupported fields: {{', '.join(extra)}}")
    for field, field_schema in properties.items():
        if field not in instance:
            continue
        value = instance[field]
        if "const" in field_schema and value != field_schema["const"]:
            raise ValueError(f"{{field}} must be {{field_schema['const']!r}}")
        if "enum" in field_schema and value not in field_schema["enum"]:
            raise ValueError(f"{{field}} must be one of {{field_schema['enum']!r}}")
        if "type" in field_schema and not validate_type(value, field_schema["type"]):
            raise ValueError(f"{{field}} has wrong type")
        if field_schema.get("type") == "array":
            item_schema = field_schema.get("items", {{}})
            item_type = item_schema.get("type")
            if item_type:
                for item in value:
                    if not validate_type(item, item_type):
                        raise ValueError(f"{{field}} contains item with wrong type")


text = raw_path.read_text(encoding="utf-8", errors="replace")
schema = json.loads(schema_path.read_text(encoding="utf-8"))
errors = []
for candidate in iter_json_objects(text):
    try:
        data = json.loads(candidate)
        validate(data, schema)
    except Exception as exc:
        errors.append(str(exc))
        continue
    output_path.write_text(json.dumps(data, indent=2) + "\\n", encoding="utf-8")
    raise SystemExit(0)

if errors:
    print("No valid worker status JSON found. Last error: " + errors[-1], file=sys.stderr)
else:
    print("No JSON object found in Gemini output.", file=sys.stderr)
raise SystemExit(1)
PY
}}

run_gemini() {{
  local label="$1"
  local model="$2"
  local raw_path="$packet_dir/events-${{label}}.log"
  command -v "$gemini_command" >/dev/null 2>&1 || return 127
  (
    cd {shell_quote(worktree)}
    "$gemini_command" \\
      --model "$model" \\
      --approval-mode "$gemini_approval_mode" \\
      --skip-trust \\
      -p "$(cat "$prompt_path")"
  ) > "$raw_path" 2>&1
  extract_status_json "$raw_path"
}}

run_codex() {{
  local label="$1"
  local model="$2"
  codex exec --ephemeral \\
    -m "$model" \\
    -C {shell_quote(worktree)} \\
    -s workspace-write \\
    --json \\
    --output-schema "$schema_path" \\
    -o "$output_path" \\
    - < "$prompt_path" \\
    > "$packet_dir/events-${{label}}.jsonl" 2>&1
}}

if run_gemini gemini-pro {shell_quote(GEMINI_PRO_MODEL)}; then
  exit 0
fi
guard_clean_for_fallback "Gemini Pro"

if run_gemini gemini-flash {shell_quote(GEMINI_FLASH_MODEL)}; then
  exit 0
fi
guard_clean_for_fallback "Gemini Flash"

if run_codex spark {shell_quote(worker_model)}; then
  exit 0
fi
guard_clean_for_fallback "Codex Spark"

run_codex mini {shell_quote(worker_fallback_model)}
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
    parser.add_argument("--gemini-command", default="gemini")
    parser.add_argument("--gemini-approval-mode", default="yolo")
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
    else:
        schema_name = "status.schema.json"
        output_name = "status.json"
        schema = status_schema()

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
            args.worker_model,
            args.worker_fallback_model,
            args.reviewer_model,
            args.reviewer_fallback_model,
            args.gemini_command,
            args.gemini_approval_mode,
        ),
        encoding="utf-8",
    )
    os.chmod(launch_path, 0o755)
    print(packet_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
