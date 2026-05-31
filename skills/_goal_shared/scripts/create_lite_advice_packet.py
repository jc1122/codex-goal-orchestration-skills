#!/usr/bin/env python3
"""Create a CLI-only Lite advisory packet for goal orchestration."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


BRANCH_LITE_PACKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*-L[A-Za-z0-9_.-]+$")
LITE_MODEL = "gemini-3.1-flash-lite-preview"
GEMINI_COMMAND = "gemini"
GEMINI_APPROVAL_MODE = "plan"
LITE_ATTEMPT_TIMEOUT_SECONDS = 600
TIMEOUT_KILL_AFTER_SECONDS = 30
LITE_STATUS_BEGIN = "BEGIN_LITE_ADVICE_JSON"
LITE_STATUS_END = "END_LITE_ADVICE_JSON"
SKILL_NAME_OVERRIDE: str | None = None
SCRIPT_DIR_OVERRIDE: Path | None = None
SKILL_PURPOSES = {
    "goal-preflight": {"preflight-decomposition", "lint-repair"},
    "goal-main-orchestrator": {"audit-defect-summary", "main-summary"},
    "goal-branch-orchestrator": {
        "branch-packet-planning",
        "context-pack",
        "worker-summary",
        "blocked-triage",
    },
    "goal-plan-amender": {
        "amendment-summary",
        "amendment-defect-summary",
    },
}


def _load_contract():
    path = Path(__file__).resolve().parent / "orchestration_contract.py"
    if not path.exists():
        raise SystemExit(f"missing shared orchestration contract: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_orchestration_contract", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared orchestration contract: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_path_rules():
    path = Path(__file__).resolve().parent / "path_rules.py"
    if not path.exists():
        raise SystemExit(f"missing shared path rules: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_path_rules", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared path rules: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT = _load_contract()
PATH_RULES = _load_path_rules()
require_safe_label = PATH_RULES.require_safe_packet_label
resolve_absolute_path = PATH_RULES.resolve_absolute_path
repo_relative_path = PATH_RULES.repo_relative_path
shell_quote = CONTRACT.shell_quote


def current_skill_name() -> str:
    if SKILL_NAME_OVERRIDE is not None:
        return SKILL_NAME_OVERRIDE
    try:
        return Path(__file__).resolve().parents[1].name
    except IndexError:
        return ""


def current_script_dir() -> Path:
    if SCRIPT_DIR_OVERRIDE is not None:
        return SCRIPT_DIR_OVERRIDE
    return Path(__file__).resolve().parent


def allowed_purposes() -> set[str]:
    skill = current_skill_name()
    if skill not in SKILL_PURPOSES:
        raise SystemExit("Lite advice scripts must be run through a goal skill wrapper, not _goal_shared directly.")
    return SKILL_PURPOSES[skill]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_gemini() -> tuple[str, str, str]:
    executable = shutil.which(GEMINI_COMMAND)
    if executable is None:
        return "", "unavailable", "unavailable"
    path = Path(executable).resolve()
    try:
        gemini_sha256 = sha256_file(path)
    except Exception as exc:  # noqa: BLE001
        gemini_sha256 = f"sha256-unavailable: {exc}"
    try:
        completed = subprocess.run(
            [path.as_posix(), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        return path.as_posix(), f"version-unavailable: {exc}", gemini_sha256
    version = (completed.stdout or completed.stderr).strip().splitlines()
    return path.as_posix(), version[0] if version else "version-unavailable", gemini_sha256


def source_metadata(path: Path, base_dir: Path) -> dict:
    return {
        "path": repo_relative_path(path, base_dir, "--input-file"),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "reason": "explicit Lite input",
    }


def advice_command(gemini_path: str) -> str:
    command = gemini_path if gemini_path else GEMINI_COMMAND
    return f"{command} --model {LITE_MODEL} --approval-mode {GEMINI_APPROVAL_MODE} --skip-trust --output-format text"


def lite_telemetry_attempts(gemini_path: str) -> list[dict]:
    return [
        {
            "alias": "gemini-lite",
            "provider": "gemini",
            "model": LITE_MODEL,
            "effort": "",
            "command": advice_command(gemini_path),
            "timeout_seconds": LITE_ATTEMPT_TIMEOUT_SECONDS,
            "event_logs": ["advice.raw.txt"],
            "probe_logs": [],
        }
    ]


def telemetry_function(packet_id: str, gemini_path: str) -> str:
    script = (current_script_dir() / "extract_telemetry.py").as_posix()
    return CONTRACT.telemetry_shell_function(
        script_path=script,
        packet_dir_expr="$packet_dir",
        packet_id=packet_id,
        role="lite_advisor",
        output_name="advice.json",
        prompt_name="prompt.md",
        attempts=lite_telemetry_attempts(gemini_path),
    )


def prompt_for(
    packet_id: str,
    purpose: str,
    base_dir: Path,
    sources: list[dict],
    extra: str,
    *,
    skill: str,
    model: str,
    gemini_path: str,
    gemini_version: str,
    gemini_sha256: str,
    task_sha256: str,
) -> str:
    source_lines = "\n".join(
        f"- {item['path']} ({item['sha256']}, {item['size_bytes']} bytes)"
        for item in sources
    )
    example_sources = json.dumps(sources, indent=2, sort_keys=True)
    command = advice_command(gemini_path)
    return f"""# Lite Advisory Packet {packet_id}

You are a CLI-only Lite advisor. Do not edit files, create branches, create worktrees, run tests, or decide pass/fail. Your job is to route context cheaply for heavier agents.

Purpose: {purpose}
Base directory: {base_dir}

Deterministic envelope:
- Skill: {skill}
- Model: {model}
- Gemini path: {gemini_path if gemini_path else "unavailable"}
- Gemini version: {gemini_version}
- Gemini sha256: {gemini_sha256}
- Task guidance sha256: {task_sha256}

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
  "commands_run": [{json.dumps(command)}]
}}
{LITE_STATUS_END}
"""


def launch_for(packet_id: str, purpose: str, base_dir: Path, gemini_path: str) -> str:
    telemetry = telemetry_function(packet_id, gemini_path)
    return f"""#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

packet_dir="$(pwd)"
prompt_path="$packet_dir/prompt.md"
inputs_path="$packet_dir/input-files.json"
output_path="$packet_dir/advice.json"
raw_path="$packet_dir/advice.raw.txt"
task_path="$packet_dir/task.md"
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
attempt_timeout_seconds={LITE_ATTEMPT_TIMEOUT_SECONDS}
timeout_kill_after_seconds={TIMEOUT_KILL_AFTER_SECONDS}
rm -f "$output_path" "$raw_path" "$packet_dir/telemetry.json"

run_with_timeout() {{
  local seconds="$1"
  shift
  if ! command -v timeout >/dev/null 2>&1; then
    echo "timeout command not found; refusing unbounded Lite advisor attempt." >&2
    return 127
  fi
  timeout --foreground --kill-after="${{timeout_kill_after_seconds}}s" "${{seconds}}s" "$@"
}}

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
gemini_path = inputs.get("gemini_path") or "gemini"
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
        f"{{gemini_path}} --model {LITE_MODEL} --approval-mode {GEMINI_APPROVAL_MODE} --skip-trust --output-format text"
    ],
}}
output_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
PY
}}

{telemetry}

validate_advice() {{
  python3 {shell_quote((current_script_dir() / "validate_lite_advice.py").as_posix())} \\
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

verify_prompt_current() {{
  python3 - "$inputs_path" "$prompt_path" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

inputs_path = Path(sys.argv[1])
prompt_path = Path(sys.argv[2])
data = json.loads(inputs_path.read_text(encoding="utf-8"))
expected = data.get("prompt_sha256")
if not isinstance(expected, str) or not expected.startswith("sha256:"):
    print("missing prompt_sha256 in input-files.json", file=sys.stderr)
    raise SystemExit(1)
if not prompt_path.exists():
    print(f"Lite prompt missing: {{prompt_path}}", file=sys.stderr)
    raise SystemExit(1)
actual = "sha256:" + hashlib.sha256(prompt_path.read_bytes()).hexdigest()
if actual != expected:
    print(f"Lite prompt stale: expected {{expected}} got {{actual}}", file=sys.stderr)
    raise SystemExit(1)
PY
}}

verify_task_current() {{
  python3 - "$inputs_path" "$task_path" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

inputs_path = Path(sys.argv[1])
task_path = Path(sys.argv[2])
data = json.loads(inputs_path.read_text(encoding="utf-8"))
expected = data.get("task_sha256")
if not isinstance(expected, str) or not expected.startswith("sha256:"):
    print("missing task_sha256 in input-files.json", file=sys.stderr)
    raise SystemExit(1)
if not task_path.exists():
    print(f"Lite task missing: {{task_path}}", file=sys.stderr)
    raise SystemExit(1)
actual = "sha256:" + hashlib.sha256(task_path.read_bytes()).hexdigest()
if actual != expected:
    print(f"Lite task stale: expected {{expected}} got {{actual}}", file=sys.stderr)
    raise SystemExit(1)
PY
}}

verify_gemini_binary() {{
  python3 - "$inputs_path" "$gemini_command" <<'PY'
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

inputs_path = Path(sys.argv[1])
gemini_command = sys.argv[2]
data = json.loads(inputs_path.read_text(encoding="utf-8"))
expected_path = data.get("gemini_path")
if not isinstance(expected_path, str) or not expected_path.strip():
    print("missing captured Gemini path in input-files.json", file=sys.stderr)
    raise SystemExit(1)
if expected_path != gemini_command:
    print(f"Gemini CLI path changed: expected {{expected_path!r}} got {{gemini_command!r}}", file=sys.stderr)
    raise SystemExit(1)
path = Path(gemini_command)
if not path.is_absolute() or not path.exists() or not os.access(path, os.X_OK):
    print(f"captured Gemini CLI path is unavailable or not executable: {{gemini_command}}", file=sys.stderr)
    raise SystemExit(1)
expected_sha = data.get("gemini_sha256")
if not isinstance(expected_sha, str) or re.fullmatch(r"sha256:[0-9a-f]{{64}}", expected_sha) is None:
    print("missing captured Gemini sha256 in input-files.json", file=sys.stderr)
    raise SystemExit(1)
digest = hashlib.sha256()
with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
actual_sha = "sha256:" + digest.hexdigest()
if actual_sha != expected_sha:
    print(f"Gemini CLI binary changed: expected {{expected_sha}} got {{actual_sha}}", file=sys.stderr)
    raise SystemExit(1)
expected = data.get("gemini_version")
if not isinstance(expected, str) or not expected.strip() or expected == "unavailable":
    print("missing captured Gemini version in input-files.json", file=sys.stderr)
    raise SystemExit(1)
try:
    completed = subprocess.run(
        [gemini_command, "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
except Exception as exc:  # noqa: BLE001
    print(f"could not recheck Gemini version: {{exc}}", file=sys.stderr)
    raise SystemExit(1)
version_lines = (completed.stdout or completed.stderr).strip().splitlines()
actual = version_lines[0] if version_lines else "version-unavailable"
if actual != expected:
    print(f"Gemini CLI version changed: expected {{expected!r}} got {{actual!r}}", file=sys.stderr)
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
output_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
PY
}}

if [[ -z "$gemini_command" || ! -x "$gemini_command" ]]; then
  write_terminal_advice blocked "Gemini CLI command unavailable at packet creation path: $gemini_command"
  write_telemetry
  exit 0
fi

if ! verify_inputs_current; then
  write_terminal_advice blocked "Lite advisor input files changed or became unavailable after packet creation."
  write_telemetry
  exit 0
fi

if ! verify_prompt_current; then
  write_terminal_advice blocked "Lite advisor prompt.md changed or became unavailable after packet creation."
  write_telemetry
  exit 0
fi

if ! verify_task_current; then
  write_terminal_advice blocked "Lite advisor task.md changed or became unavailable after packet creation."
  write_telemetry
  exit 0
fi

if ! verify_gemini_binary; then
  write_terminal_advice blocked "Gemini CLI binary or version changed or could not be verified after packet creation."
  write_telemetry
  exit 0
fi

(
  cd "$base_dir"
  run_with_timeout "$attempt_timeout_seconds" "$gemini_command" \\
    --model "$lite_model" \\
    --approval-mode "$approval_mode" \\
    --skip-trust \\
    --output-format text \\
    -p "Follow the complete Lite advisory packet instructions provided on stdin." < "$prompt_path"
) > "$raw_path" 2>&1 || {{
  write_terminal_advice blocked "Lite advisor command failed. Inspect advice.raw.txt for CLI, quota, auth, or model errors."
  write_telemetry
  exit 0
}}

if extract_advice_json; then
  write_telemetry
  if validate_advice; then
    exit 0
  fi
fi

write_terminal_advice blocked "Lite advisor did not produce valid advice JSON."
write_telemetry
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
    skill = current_skill_name()
    if skill == "goal-branch-orchestrator" and not BRANCH_LITE_PACKET_RE.fullmatch(packet_id):
        raise SystemExit("branch Lite packet-id must be scoped as <branch-id>-L<suffix>")
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
    task_sha256 = sha256_text(extra)
    gemini_path, gemini_version, gemini_sha256 = resolve_gemini()
    prompt_text = prompt_for(
        packet_id,
        args.purpose,
        base_dir,
        sources,
        extra,
        skill=skill,
        model=LITE_MODEL,
        gemini_path=gemini_path,
        gemini_version=gemini_version,
        gemini_sha256=gemini_sha256,
        task_sha256=task_sha256,
    )
    inputs = {
        "packet_id": packet_id,
        "purpose": args.purpose,
        "skill": skill,
        "base_dir": base_dir.as_posix(),
        "model": LITE_MODEL,
        "gemini_path": gemini_path,
        "gemini_version": gemini_version,
        "gemini_sha256": gemini_sha256,
        "task_sha256": task_sha256,
        "prompt_sha256": sha256_text(prompt_text),
        "source_files": sources,
    }

    (packet_dir / "input-files.json").write_text(json.dumps(inputs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (packet_dir / "prompt.md").write_text(
        prompt_text,
        encoding="utf-8",
    )
    (packet_dir / "task.md").write_text(extra, encoding="utf-8")
    launch_path = packet_dir / "launch.sh"
    launch_path.write_text(launch_for(packet_id, args.purpose, base_dir, gemini_path), encoding="utf-8")
    os.chmod(launch_path, 0o755)
    print(packet_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
