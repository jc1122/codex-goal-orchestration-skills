#!/usr/bin/env python3
"""Create a CLI-only Lite advisory packet for goal orchestration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath


SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
LITE_MODEL = "gemini-3.1-flash-lite-preview"
GEMINI_COMMAND = "gemini"
GEMINI_APPROVAL_MODE = "plan"
LITE_STATUS_BEGIN = "BEGIN_LITE_ADVICE_JSON"
LITE_STATUS_END = "END_LITE_ADVICE_JSON"
ALL_PURPOSES = {
    "preflight-decomposition",
    "lint-repair",
    "audit-defect-summary",
    "branch-packet-planning",
    "context-pack",
    "worker-summary",
    "blocked-triage",
    "main-summary",
}
SKILL_PURPOSES = {
    "goal-preflight": {"preflight-decomposition", "lint-repair"},
    "goal-main-orchestrator": {"audit-defect-summary", "main-summary"},
    "goal-branch-orchestrator": {
        "branch-packet-planning",
        "context-pack",
        "worker-summary",
        "blocked-triage",
    },
}


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def require_safe_label(value: str, field: str) -> str:
    if not SAFE_LABEL_RE.fullmatch(value):
        raise SystemExit(f"{field} must match {SAFE_LABEL_RE.pattern}: {value!r}")
    return value


def current_skill_name() -> str:
    try:
        return Path(__file__).resolve().parents[1].name
    except IndexError:
        return ""


def allowed_purposes() -> set[str]:
    return SKILL_PURPOSES.get(current_skill_name(), ALL_PURPOSES)


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


def repo_relative_path(path: Path, base_dir: Path, field: str) -> str:
    try:
        relative = path.resolve().relative_to(base_dir.resolve())
    except ValueError as exc:
        raise SystemExit(f"{field} must be inside --base-dir: {path}") from exc
    text = relative.as_posix()
    parts = PurePosixPath(text).parts
    if not text or any(part in {"", ".", ".."} for part in parts):
        raise SystemExit(f"{field} resolved to an unsafe relative path: {text!r}")
    return text


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def resolve_gemini() -> tuple[str, str]:
    executable = shutil.which(GEMINI_COMMAND)
    if executable is None:
        return "", "unavailable"
    path = Path(executable).resolve()
    try:
        completed = subprocess.run(
            [path.as_posix(), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        return path.as_posix(), f"version-unavailable: {exc}"
    version = (completed.stdout or completed.stderr).strip().splitlines()
    return path.as_posix(), version[0] if version else "version-unavailable"


def source_metadata(path: Path, base_dir: Path) -> dict:
    return {
        "path": repo_relative_path(path, base_dir, "--input-file"),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "reason": "explicit Lite input",
    }


def advice_schema(packet_id: str, purpose: str) -> dict:
    nonempty_string = {"type": "string", "minLength": 1}
    relative_path = {
        "type": "string",
        "minLength": 1,
        "pattern": r"^(?!/)(?!.*//)(?!.*\\)(?!.*(?:^|/)\.(?:/|$))(?!.*(?:^|/)\.\.(?:/|$)).+",
    }
    source_file = {
        "type": "object",
        "additionalProperties": False,
        "required": ["path", "sha256", "size_bytes", "reason"],
        "properties": {
            "path": relative_path,
            "sha256": {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"},
            "size_bytes": {"type": "integer", "minimum": 0},
            "reason": nonempty_string,
        },
    }
    recommended_read = {
        "type": "object",
        "additionalProperties": False,
        "required": ["path", "anchor", "reason"],
        "properties": {
            "path": relative_path,
            "anchor": nonempty_string,
            "reason": nonempty_string,
        },
    }
    risk_flag = {
        "type": "object",
        "additionalProperties": False,
        "required": ["label", "path", "reason"],
        "properties": {
            "label": {
                "type": "string",
                "enum": ["unsupported", "unresolved", "negative", "weakened", "probe-only", "blocked"],
            },
            "path": relative_path,
            "reason": nonempty_string,
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "packet_id",
            "role",
            "purpose",
            "status",
            "source_files",
            "recommended_reads",
            "risk_flags",
            "advice",
            "summary",
            "blockers",
            "commands_run",
        ],
        "properties": {
            "packet_id": {"type": "string", "const": packet_id},
            "role": {"type": "string", "const": "lite_advisor"},
            "purpose": {"type": "string", "const": purpose},
            "status": {"type": "string", "enum": ["ok", "partial", "blocked"]},
            "source_files": {"type": "array", "items": source_file},
            "recommended_reads": {"type": "array", "items": recommended_read},
            "risk_flags": {"type": "array", "items": risk_flag},
            "advice": {"type": "object"},
            "summary": nonempty_string,
            "blockers": {"type": "array", "items": nonempty_string},
            "commands_run": {"type": "array", "items": nonempty_string, "minItems": 1},
        },
    }


def prompt_for(packet_id: str, purpose: str, base_dir: Path, sources: list[dict], extra: str) -> str:
    source_lines = "\n".join(
        f"- {item['path']} ({item['sha256']}, {item['size_bytes']} bytes)"
        for item in sources
    )
    example_sources = json.dumps(sources, indent=2)
    return f"""# Lite Advisory Packet {packet_id}

You are a CLI-only Lite advisor. Do not edit files, create branches, create worktrees, run tests, or decide pass/fail. Your job is to route context cheaply for heavier agents.

Purpose: {purpose}
Base directory: {base_dir}

Read only these explicit input files:
{source_lines if source_lines else "- none"}

Policy:
- Lite output is advisory only.
- Do not decide mergeability, prompt-audit pass/fail, scientific claim support, or Definition-of-Done satisfaction.
- Preserve labels exactly when present: `unsupported`, `unresolved`, `negative`, `weakened`, `probe-only`, `blocked`.
- Recommend targeted original reads with path, anchor, and reason. Do not tell heavy agents to reread every source file by default.
- For any purpose other than `preflight-decomposition`, `recommended_reads` may cite only the explicit input files listed above.
- Use focused context. Do not broaden beyond the listed files unless the purpose is `preflight-decomposition`; even then, only recommend additional paths rather than reading the whole repository.
- If an input file is missing, unreadable, stale, or insufficient, return `status: "blocked"` or `status: "partial"` with blockers.

Additional task guidance:
{extra.strip() if extra.strip() else "- No extra guidance."}

Return exactly one JSON object between these marker lines. Do not print any other JSON object between them. The `source_files` array must echo this exact metadata for every listed input file:

{LITE_STATUS_BEGIN}
{{
  "packet_id": "{packet_id}",
  "role": "lite_advisor",
  "purpose": "{purpose}",
  "status": "ok",
  "source_files": {example_sources},
  "recommended_reads": [],
  "risk_flags": [],
  "advice": {{}},
  "summary": "replace with concise advisory summary",
  "blockers": [],
  "commands_run": ["gemini --model {LITE_MODEL} --approval-mode {GEMINI_APPROVAL_MODE} --output-format text"]
}}
{LITE_STATUS_END}
"""


def launch_for(packet_id: str, purpose: str, base_dir: Path) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

packet_dir="$(pwd)"
prompt_path="$packet_dir/prompt.md"
inputs_path="$packet_dir/input-files.json"
schema_path="$packet_dir/advice.schema.json"
output_path="$packet_dir/advice.json"
raw_path="$packet_dir/advice.raw.txt"
gemini_command="$(python3 - "$inputs_path" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(data.get("gemini_path", ""))
PY
)"
lite_model={shell_quote(LITE_MODEL)}
approval_mode={shell_quote(GEMINI_APPROVAL_MODE)}
base_dir={shell_quote(base_dir.as_posix())}
rm -f "$output_path" "$raw_path"

write_terminal_advice() {{
  local status="$1"
  local message="$2"
  python3 - "$output_path" "$inputs_path" {shell_quote(packet_id)} {shell_quote(purpose)} "$status" "$message" <<'PY'
import json
import sys
from pathlib import Path

output_path = Path(sys.argv[1])
inputs_path = Path(sys.argv[2])
packet_id = sys.argv[3]
purpose = sys.argv[4]
status = sys.argv[5]
message = sys.argv[6]
inputs = json.loads(inputs_path.read_text(encoding="utf-8"))
data = {{
    "packet_id": packet_id,
    "role": "lite_advisor",
    "purpose": purpose,
    "status": status,
    "source_files": inputs.get("source_files", []),
    "recommended_reads": [],
    "risk_flags": [],
    "advice": {{}},
    "summary": message,
    "blockers": [message],
    "commands_run": [
        "gemini --model {LITE_MODEL} --approval-mode {GEMINI_APPROVAL_MODE} --output-format text"
    ],
}}
output_path.write_text(json.dumps(data, indent=2) + "\\n", encoding="utf-8")
PY
}}

validate_advice() {{
  python3 {shell_quote((Path(__file__).resolve().parent / "validate_lite_advice.py").as_posix())} \\
    --advice "$output_path" \\
    --inputs "$inputs_path" \\
    --packet-id {shell_quote(packet_id)} \\
    --purpose {shell_quote(purpose)} >/dev/null
}}

verify_inputs_current() {{
  python3 - "$inputs_path" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

inputs_path = Path(sys.argv[1])
data = json.loads(inputs_path.read_text(encoding="utf-8"))
base_dir = Path(data.get("base_dir", ""))
if not base_dir.is_absolute() or not base_dir.exists():
    print(f"invalid or missing Lite base_dir: {base_dir}", file=sys.stderr)
    raise SystemExit(1)

def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()

for item in data.get("source_files", []):
    rel = item.get("path", "")
    path = (base_dir / rel).resolve()
    try:
        path.relative_to(base_dir.resolve())
    except ValueError:
        print(f"Lite input escaped base_dir: {{rel}}", file=sys.stderr)
        raise SystemExit(1)
    if not path.exists():
        print(f"Lite input missing: {{rel}}", file=sys.stderr)
        raise SystemExit(1)
    actual_hash = sha256_file(path)
    actual_size = path.stat().st_size
    if actual_hash != item.get("sha256") or actual_size != item.get("size_bytes"):
        print(
            f"Lite input stale: {{rel}} expected {{item.get('sha256')}}/{{item.get('size_bytes')}} "
            f"got {{actual_hash}}/{{actual_size}}",
            file=sys.stderr,
        )
        raise SystemExit(1)
PY
}}

extract_advice_json() {{
  python3 - "$raw_path" "$output_path" <<'PY'
import json
import sys
from pathlib import Path

raw_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
begin = "{LITE_STATUS_BEGIN}"
end = "{LITE_STATUS_END}"
text = raw_path.read_text(encoding="utf-8", errors="replace")
begin_count = text.count(begin)
end_count = text.count(end)
if begin_count != 1 or end_count != 1:
    print(
        f"expected exactly one {{begin}} and one {{end}} marker; "
        f"found {{begin_count}} begin marker(s) and {{end_count}} end marker(s).",
        file=sys.stderr,
    )
    raise SystemExit(1)
start = text.index(begin) + len(begin)
finish = text.index(end)
if finish <= start:
    print("Lite advice end marker appears before begin marker.", file=sys.stderr)
    raise SystemExit(1)
candidate = text[start:finish].strip()
data = json.loads(candidate)
output_path.write_text(json.dumps(data, indent=2) + "\\n", encoding="utf-8")
PY
}}

if [[ -z "$gemini_command" || ! -x "$gemini_command" ]]; then
  write_terminal_advice blocked "Gemini CLI command unavailable at packet creation path: $gemini_command"
  exit 0
fi

if ! verify_inputs_current; then
  write_terminal_advice blocked "Lite advisor input files changed or became unavailable after packet creation."
  exit 0
fi

(
  cd "$base_dir"
  "$gemini_command" \\
    --model "$lite_model" \\
    --approval-mode "$approval_mode" \\
    --skip-trust \\
    --output-format text \\
    -p "$(cat "$prompt_path")"
) > "$raw_path" 2>&1 || {{
  write_terminal_advice blocked "Lite advisor command failed. Inspect advice.raw.txt for CLI, quota, auth, or model errors."
  exit 0
}}

if extract_advice_json && validate_advice; then
  exit 0
fi

write_terminal_advice blocked "Lite advisor did not produce valid advice JSON."
exit 0
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet-id", required=True)
    parser.add_argument("--purpose", choices=sorted(allowed_purposes()), required=True)
    parser.add_argument("--base-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--input-file", action="append", default=[])
    parser.add_argument("--task-file")
    parser.add_argument("--replace", action="store_true", help="Replace an existing packet directory after removing it first.")
    args = parser.parse_args()

    packet_id = require_safe_label(args.packet_id, "packet-id")
    base_dir = resolve_absolute_path(args.base_dir, "--base-dir", must_exist=True)
    if not base_dir.is_dir():
        raise SystemExit(f"--base-dir must be a directory: {base_dir}")
    out_dir = resolve_absolute_path(args.out_dir, "--out-dir", must_exist=False)
    task_file = (
        resolve_absolute_path(args.task_file, "--task-file", must_exist=True)
        if args.task_file
        else None
    )
    input_files = [
        resolve_absolute_path(value, "--input-file", must_exist=True)
        for value in args.input_file
    ]
    if not input_files:
        raise SystemExit("at least one --input-file is required")
    sources = [source_metadata(path, base_dir) for path in input_files]

    packet_dir = out_dir / packet_id
    if packet_dir.exists():
        if not args.replace:
            raise SystemExit(f"Lite packet already exists; pass --replace to recreate deterministically: {packet_dir}")
        shutil.rmtree(packet_dir)
    packet_dir.mkdir(parents=True, exist_ok=True)
    extra = task_file.read_text(encoding="utf-8") if task_file else ""
    gemini_path, gemini_version = resolve_gemini()
    inputs = {
        "packet_id": packet_id,
        "purpose": args.purpose,
        "skill": current_skill_name(),
        "base_dir": base_dir.as_posix(),
        "model": LITE_MODEL,
        "gemini_path": gemini_path,
        "gemini_version": gemini_version,
        "source_files": sources,
    }

    (packet_dir / "input-files.json").write_text(json.dumps(inputs, indent=2) + "\n", encoding="utf-8")
    (packet_dir / "advice.schema.json").write_text(
        json.dumps(advice_schema(packet_id, args.purpose), indent=2) + "\n",
        encoding="utf-8",
    )
    (packet_dir / "prompt.md").write_text(
        prompt_for(packet_id, args.purpose, base_dir, sources, extra),
        encoding="utf-8",
    )
    launch_path = packet_dir / "launch.sh"
    launch_path.write_text(launch_for(packet_id, args.purpose, base_dir), encoding="utf-8")
    os.chmod(launch_path, 0o755)
    print(packet_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
