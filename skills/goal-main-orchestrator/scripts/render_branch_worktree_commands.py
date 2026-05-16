#!/usr/bin/env python3
"""Render branch worktree creation commands after prompt audit passes."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path, PurePosixPath


INVALID_BRANCH_CHARS = set(" ~^:?*[\\")


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def require_relative_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise SystemExit(f"{field} must be a non-empty relative path")
    if "\\" in value:
        raise SystemExit(f"{field} must use POSIX '/' separators, not backslashes: {value!r}")
    if "//" in value:
        raise SystemExit(f"{field} must not contain empty path segments: {value!r}")
    if value.startswith("./") or "/./" in value or value.endswith("/."):
        raise SystemExit(f"{field} must not contain '.' path segments: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise SystemExit(f"{field} must be relative, not absolute: {value!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise SystemExit(f"{field} must not contain empty, '.', or '..' segments: {value!r}")
    return path.as_posix()


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


def git_ok(repo_root: Path, *args: str) -> bool:
    return subprocess.run(
        ["git", "-C", repo_root.as_posix(), *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--wave")
    parser.add_argument("--list-waves", action="store_true")
    args = parser.parse_args()

    manifest_path = Path(args.manifest).expanduser().resolve()
    repo_root = Path(args.repo_root).expanduser().resolve()
    audit_path = Path(args.audit).expanduser().resolve()
    manifest = load_json(manifest_path)
    audit = load_json(audit_path)

    if not git_ok(repo_root, "rev-parse", "--show-toplevel"):
        raise SystemExit(f"repo root is not a git checkout: {repo_root}")

    if audit.get("status") != "pass" or audit.get("can_start") is not True:
        raise SystemExit("prompt audit did not pass; refusing to render branch creation commands")

    max_active = manifest.get("max_active_branch_agents", 5)
    if not isinstance(max_active, int) or max_active < 1 or max_active > 5:
        raise SystemExit("max_active_branch_agents must be an integer from 1 to 5")

    waves = manifest.get("waves") or []
    manifest_branch_ids = [branch.get("id") for branch in manifest.get("branches", [])]
    if len(manifest_branch_ids) != len(set(manifest_branch_ids)):
        raise SystemExit("manifest branch ids must be unique")
    wave_branch_ids = []
    for wave in waves:
        branch_ids = wave.get("branches", [])
        if len(branch_ids) > max_active:
            raise SystemExit(f"wave {wave.get('id')} exceeds max_active_branch_agents")
        wave_branch_ids.extend(branch_ids)
    if len(wave_branch_ids) != len(set(wave_branch_ids)):
        raise SystemExit("branch ids must not appear in more than one wave")
    if waves and set(wave_branch_ids) != set(manifest_branch_ids):
        raise SystemExit("waves must cover exactly the manifest branch ids")

    if args.list_waves:
        for wave in waves:
            print(f"{wave.get('id')}: {', '.join(wave.get('branches', []))}")
        return 0

    selected_ids = None
    if waves:
        if not args.wave:
            raise SystemExit("manifest has waves; pass --wave <wave-id> to avoid creating too many worktrees")
        matches = [wave for wave in waves if wave.get("id") == args.wave]
        if not matches:
            raise SystemExit(f"unknown wave: {args.wave}")
        selected_ids = set(matches[0].get("branches", []))

    base_ref = manifest.get("base_ref", "main")
    if not git_ok(repo_root, "rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}"):
        raise SystemExit(f"base_ref does not resolve to a commit: {base_ref}")
    seen_names = set()
    seen_worktrees = set()
    for branch in manifest.get("branches", []):
        if selected_ids is not None and branch.get("id") not in selected_ids:
            continue
        name = branch["branch_name"]
        if name in seen_names:
            raise SystemExit(f"duplicate branch_name: {name}")
        seen_names.add(name)
        if not safe_branch_name(name) or not git_ok(repo_root, "check-ref-format", "--branch", name):
            raise SystemExit(f"branch_name is not safe: {name!r}")
        if git_ok(repo_root, "show-ref", "--verify", "--quiet", f"refs/heads/{name}"):
            raise SystemExit(f"target branch already exists: {name}")
        worktree_rel = require_relative_path(branch["worktree_path"], "worktree_path")
        worktree_path = resolve(repo_root, worktree_rel)
        if worktree_path in seen_worktrees:
            raise SystemExit(f"duplicate worktree_path: {worktree_path}")
        seen_worktrees.add(worktree_path)
        if worktree_path.exists():
            raise SystemExit(f"target worktree path already exists: {worktree_path}")
        worktree = worktree_path.as_posix()
        print(f"git worktree add -b {shell_quote(name)} {shell_quote(worktree)} {shell_quote(base_ref)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
