#!/usr/bin/env python3
"""Render branch worktree creation commands after prompt audit passes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


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

    if audit.get("status") != "pass" or audit.get("can_start") is not True:
        raise SystemExit("prompt audit did not pass; refusing to render branch creation commands")

    max_active = manifest.get("max_active_branch_agents", 5)
    if not isinstance(max_active, int) or max_active > 5:
        raise SystemExit("max_active_branch_agents must be an integer <= 5")

    waves = manifest.get("waves") or []
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
        if len(selected_ids) > max_active:
            raise SystemExit(f"wave {args.wave} exceeds max_active_branch_agents")

    base_ref = manifest.get("base_ref", "main")
    for branch in manifest.get("branches", []):
        if selected_ids is not None and branch.get("id") not in selected_ids:
            continue
        name = branch["branch_name"]
        worktree = resolve(repo_root, branch["worktree_path"]).as_posix()
        print(f"git worktree add -b {shell_quote(name)} {shell_quote(worktree)} {shell_quote(base_ref)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
