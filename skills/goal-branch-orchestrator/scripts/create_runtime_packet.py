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
GEMINI_COMMAND = "gemini"
GEMINI_APPROVAL_MODE = "yolo"
GEMINI_PRO_MODEL = "gemini-3.1-pro-preview"
GEMINI_FLASH_MODEL = "gemini-3-flash-preview"
GEMINI_PROBE_TIMEOUT_SECONDS = 20
GEMINI_PROBE_PROMPT = "Return exactly: GEMINI_MODEL_PROBE_OK"
SPARK_MODEL = "gpt-5.3-codex-spark"
MINI_MODEL = "gpt-5.4-mini"
REVIEWER_MODEL = "gpt-5.5"
REVIEWER_FALLBACK_MODEL = "gpt-5.4"
GEMINI_STATUS_BEGIN = "BEGIN_WORKER_STATUS_JSON"
GEMINI_STATUS_END = "END_WORKER_STATUS_JSON"


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def require_safe_label(value: str, field: str) -> str:
    if not SAFE_LABEL_RE.fullmatch(value):
        raise SystemExit(f"{field} must match {SAFE_LABEL_RE.pattern}: {value!r}")
    return value


def resolve_absolute_path(value: str, field: str, *, must_exist: bool) -> Path:
    if "\\" in value:
        raise SystemExit(f"{field} must use POSIX '/' separators: {value!r}")
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        raise SystemExit(f"{field} must be an absolute path: {value!r}")
    if ".." in expanded.parts:
        raise SystemExit(f"{field} must not contain '..' traversal: {value!r}")
    if must_exist and not expanded.exists():
        raise SystemExit(f"{field} does not exist: {expanded}")
    return expanded.resolve(strict=must_exist)


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
        path = resolve_absolute_path(value, "--context-file", must_exist=True)
        normalized.append(path.as_posix())
    return normalized


def exact_string_schema(value: str) -> dict:
    return {"const": value}


def status_schema(packet_id: str, branch: str, worktree: str) -> dict:
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
            "packet_id": exact_string_schema(packet_id),
            "role": {"const": "worker"},
            "status": {"enum": ["pass", "partial", "blocked", "failed"]},
            "branch": exact_string_schema(branch),
            "worktree": exact_string_schema(worktree),
            "changed_files": {"type": "array", "items": {"type": "string"}},
            "commands_run": {"type": "array", "items": {"type": "string"}},
            "tests": {"type": "array", "items": {"type": "string"}},
            "blockers": {"type": "array", "items": {"type": "string"}},
            "handoff": {"type": "string"},
        },
    }


def review_schema(packet_id: str) -> dict:
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
            "packet_id": exact_string_schema(packet_id),
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


def load_task(path: Path | None) -> str:
    if not path:
        return "- Replace this section with the bounded task objective before launch."
    return path.read_text(encoding="utf-8")


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

Return a worker status object matching `{schema_name}`.

If you are running under Gemini CLI, print the final status object between these exact marker lines and do not print any other JSON object between them:

{GEMINI_STATUS_BEGIN}
{{"packet_id":"{packet_id}","role":"worker","status":"blocked","branch":"{branch}","worktree":"{worktree}","changed_files":[],"commands_run":[],"tests":[],"blockers":[],"handoff":"replace with concise handoff"}}
{GEMINI_STATUS_END}
"""


def launch_for(
    role: str,
    packet_id: str,
    branch: str,
    worktree: str,
    schema_name: str,
    output_name: str,
) -> str:
    sandbox = "read-only" if role == "reviewer" else "workspace-write"
    if role == "reviewer":
        return f"""#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

git -C {shell_quote(worktree)} rev-parse --show-toplevel >/dev/null

packet_dir="$(pwd)"
output_path="$packet_dir/{output_name}"

write_terminal_review() {{
  local message="$1"
  python3 - "$output_path" {shell_quote(packet_id)} "$message" <<'PY'
import json
import sys
from pathlib import Path

output_path = Path(sys.argv[1])
packet_id = sys.argv[2]
message = sys.argv[3]
data = {{
    "packet_id": packet_id,
    "role": "reviewer",
    "verdict": "blocked",
    "findings": [message],
    "commands_run": [
        "codex exec --ephemeral -m {REVIEWER_MODEL} -s read-only",
        "codex exec --ephemeral -m {REVIEWER_FALLBACK_MODEL} -s read-only",
    ],
    "verification_gaps": [message],
    "residual_risks": [],
    "summary": message,
}}
output_path.write_text(json.dumps(data, indent=2) + "\\n", encoding="utf-8")
PY
}}

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

if run_model primary {shell_quote(REVIEWER_MODEL)}; then
  exit 0
fi

if [ -s "$(pwd)/{output_name}" ]; then
  exit 1
fi

if run_model fallback {shell_quote(REVIEWER_FALLBACK_MODEL)}; then
  exit 0
fi

if [ -s "$output_path" ]; then
  exit 1
fi

write_terminal_review "Reviewer primary and fallback failed without producing {output_name}."
exit 1
"""

    return f"""#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

git -C {shell_quote(worktree)} rev-parse --show-toplevel >/dev/null

packet_dir="$(pwd)"
prompt_path="$packet_dir/prompt.md"
schema_path="$packet_dir/{schema_name}"
output_path="$packet_dir/{output_name}"
packet_id={shell_quote(packet_id)}
branch_name={shell_quote(branch)}
worktree_path={shell_quote(worktree)}
gemini_command={shell_quote(GEMINI_COMMAND)}
gemini_approval_mode={shell_quote(GEMINI_APPROVAL_MODE)}
gemini_probe_timeout_seconds={GEMINI_PROBE_TIMEOUT_SECONDS}
gemini_probe_prompt={shell_quote(GEMINI_PROBE_PROMPT)}

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
    write_terminal_status blocked "$label failed after leaving dirty worktree; refusing fallback in same worktree."
    exit 2
  fi
}}

write_terminal_status() {{
  local status="$1"
  local message="$2"
  python3 - "$output_path" "$packet_id" "$branch_name" "$worktree_path" "$status" "$message" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

output_path = Path(sys.argv[1])
packet_id = sys.argv[2]
branch = sys.argv[3]
worktree = sys.argv[4]
status = sys.argv[5]
message = sys.argv[6]

try:
    changed_files = subprocess.check_output(
        ["git", "-C", worktree, "status", "--short"],
        text=True,
        stderr=subprocess.DEVNULL,
    ).splitlines()
except Exception:
    changed_files = []

data = {{
    "packet_id": packet_id,
    "role": "worker",
    "status": status,
    "branch": branch,
    "worktree": worktree,
    "changed_files": changed_files,
    "commands_run": [
        "gemini --model {GEMINI_PRO_MODEL} --approval-mode {GEMINI_APPROVAL_MODE}",
        "gemini --model {GEMINI_FLASH_MODEL} --approval-mode {GEMINI_APPROVAL_MODE}",
        "codex exec --ephemeral -m {SPARK_MODEL} -s workspace-write",
        "codex exec --ephemeral -m {MINI_MODEL} -s workspace-write",
    ],
    "tests": [],
    "blockers": [message],
    "handoff": message,
}}
output_path.write_text(json.dumps(data, indent=2) + "\\n", encoding="utf-8")
PY
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
begin = "{GEMINI_STATUS_BEGIN}"
end = "{GEMINI_STATUS_END}"


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
begin_count = text.count(begin)
end_count = text.count(end)
if begin_count != 1 or end_count != 1:
    print(
        f"Expected exactly one {{begin}} and one {{end}} marker; "
        f"found {{begin_count}} begin marker(s) and {{end_count}} end marker(s).",
        file=sys.stderr,
    )
    raise SystemExit(1)
start = text.index(begin) + len(begin)
finish = text.index(end)
if finish <= start:
    print("Worker status end marker appears before begin marker.", file=sys.stderr)
    raise SystemExit(1)
candidate = text[start:finish].strip()
try:
    data = json.loads(candidate)
    validate(data, schema)
except Exception as exc:
    print("Invalid marked worker status JSON: " + str(exc), file=sys.stderr)
    raise SystemExit(1)
output_path.write_text(json.dumps(data, indent=2) + "\\n", encoding="utf-8")
PY
}}

probe_gemini_model() {{
  local label="$1"
  local model="$2"
  local probe_path="$packet_dir/events-${{label}}-probe.log"
  (
    cd {shell_quote(worktree)}
    python3 - "$gemini_command" "$model" "$gemini_approval_mode" "$gemini_probe_timeout_seconds" "$gemini_probe_prompt" <<'PY'
import subprocess
import sys

command, model, approval_mode, timeout_seconds, prompt = sys.argv[1:6]
try:
    result = subprocess.run(
        [
            command,
            "--model",
            model,
            "--approval-mode",
            approval_mode,
            "--skip-trust",
            "-p",
            prompt,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=int(timeout_seconds),
        check=False,
    )
except subprocess.TimeoutExpired as exc:
    output = exc.stdout or ""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    if output:
        sys.stdout.write(output)
    print(f"Gemini model probe timed out after {{timeout_seconds}} seconds.", file=sys.stderr)
    raise SystemExit(124)

if result.stdout:
    sys.stdout.write(result.stdout)
raise SystemExit(result.returncode)
PY
  ) > "$probe_path" 2>&1
}}

run_gemini() {{
  local label="$1"
  local model="$2"
  local raw_path="$packet_dir/events-${{label}}.log"
  if ! command -v "$gemini_command" >/dev/null 2>&1; then
    echo "Gemini command not found: $gemini_command" > "$raw_path"
    return 127
  fi
  probe_gemini_model "$label" "$model" || return $?
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

if run_codex spark {shell_quote(SPARK_MODEL)}; then
  exit 0
fi
guard_clean_for_fallback "Codex Spark"

if run_codex mini {shell_quote(MINI_MODEL)}; then
  exit 0
fi

if [ -s "$output_path" ]; then
  exit 1
fi

if worktree_dirty; then
  echo "Codex mini failed after leaving dirty worktree; no fallback remains." > "$packet_dir/fallback.blocked.txt"
  write_terminal_status blocked "Codex mini failed after leaving dirty worktree; no fallback remains."
  exit 2
fi

write_terminal_status blocked "All worker attempts failed cleanly without producing {output_name}."
exit 1
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
    args = parser.parse_args()

    packet_id = require_safe_label(args.packet_id, "packet-id")
    branch = args.branch
    if not safe_branch_name(branch):
        raise SystemExit(f"branch is not a safe git branch name: {branch!r}")
    worktree = resolve_absolute_path(args.worktree, "--worktree", must_exist=True)
    owned_files = normalize_owned_paths(args.owned_file)
    context_files = normalize_context_files(args.context_file)
    task_file = (
        resolve_absolute_path(args.task_file, "--task-file", must_exist=True)
        if args.task_file
        else None
    )

    out_dir = resolve_absolute_path(args.out_dir, "--out-dir", must_exist=False)
    packet_dir = out_dir / packet_id
    packet_dir.mkdir(parents=True, exist_ok=True)

    if args.role == "reviewer":
        schema_name = "review.schema.json"
        output_name = "review.json"
        schema = review_schema(packet_id)
    else:
        schema_name = "status.schema.json"
        output_name = "status.json"
        schema = status_schema(packet_id, branch, str(worktree))

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
            load_task(task_file),
        ),
        encoding="utf-8",
    )
    launch_path = packet_dir / "launch.sh"
    launch_path.write_text(
        launch_for(
            args.role,
            packet_id,
            branch,
            str(worktree),
            schema_name,
            output_name,
        ),
        encoding="utf-8",
    )
    os.chmod(launch_path, 0o755)
    print(packet_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
