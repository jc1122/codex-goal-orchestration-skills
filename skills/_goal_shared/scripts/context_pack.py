#!/usr/bin/env python3
"""Create bounded deterministic context packs for runtime agent packets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_TOTAL_CHARS = 24000
DEFAULT_PER_FILE_CHARS = 8000


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def excerpt_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text.rstrip(), False
    marker = f"\n\n[... deterministic excerpt omitted {len(text) - max_chars} source chars ...]\n\n"
    if max_chars <= len(marker) + 200:
        return text[:max_chars].rstrip(), True
    head_chars = max_chars // 2
    tail_chars = max_chars - head_chars - len(marker)
    return (text[:head_chars].rstrip() + marker + text[-tail_chars:].lstrip()).rstrip(), True


def pack_context(
    *,
    worktree: Path,
    context_files: list[Path],
    total_chars: int = DEFAULT_TOTAL_CHARS,
    per_file_chars: int = DEFAULT_PER_FILE_CHARS,
    include_worktree_excerpts: bool = False,
) -> dict[str, Any]:
    entries = []
    remaining = total_chars
    for index, path in enumerate(context_files, start=1):
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(worktree)
        except ValueError:
            relative_text = None
        else:
            relative_text = relative.as_posix()
            if not include_worktree_excerpts:
                entries.append(
                    {
                        "kind": "worktree_path",
                        "label": f"context-{index}",
                        "path": relative_text,
                        "absolute_path": resolved.as_posix(),
                    }
                )
                continue

        text = resolved.read_text(encoding="utf-8", errors="replace")
        limit = min(per_file_chars, max(0, remaining))
        excerpt, truncated = excerpt_text(text, limit)
        remaining -= len(excerpt)
        label = (
            f"context-{index}: {relative_text}"
            if relative_text is not None
            else f"external-context-{index}: {resolved.name}"
        )
        entries.append(
            {
                "kind": "embedded_excerpt",
                "label": label,
                "path": relative_text,
                "absolute_path": resolved.as_posix(),
                "origin": "worktree" if relative_text is not None else "external",
                "sha256": sha256_file(resolved),
                "source_chars": len(text),
                "included_chars": len(excerpt),
                "truncated": truncated,
                "excerpt": excerpt,
            }
        )
        if remaining <= 0:
            break
    omitted = len(context_files) - sum(1 for _ in entries)
    return {
        "schema_version": 1,
        "total_char_limit": total_chars,
        "per_file_char_limit": per_file_chars,
        "worktree_context_mode": "embedded_excerpt" if include_worktree_excerpts else "path_reference",
        "omitted_context_files": max(0, omitted),
        "entries": entries,
    }


def markdown_from_pack(pack: dict[str, Any]) -> str:
    entries = pack.get("entries", [])
    if not entries:
        return "Context files to read first: none"
    lines = [
        "Context files to read first:",
        "- Prefer listed paths and deterministic excerpts over broad repository reads.",
        "- Do not open skill Python scripts unless a validator/launcher fails and debugging is required.",
    ]
    for entry in entries:
        if entry.get("kind") == "worktree_path":
            lines.append(f"- {entry['path']}")
        elif entry.get("kind") == "embedded_excerpt":
            truncation = "truncated" if entry.get("truncated") else "complete"
            lines.append(
                f"- {entry['label']} embedded below ({truncation}; "
                f"{entry.get('included_chars', 0)}/{entry.get('source_chars', 0)} chars; {entry.get('sha256')})."
            )
    if pack.get("omitted_context_files"):
        lines.append(f"- {pack['omitted_context_files']} context file(s) omitted after total context limit.")
    embedded = [entry for entry in entries if entry.get("kind") == "embedded_excerpt"]
    if embedded:
        lines.extend(["", "Deterministic context excerpts:"])
        for entry in embedded:
            lines.extend(
                [
                    "",
                    f"BEGIN_CONTEXT_EXCERPT {entry['label']}",
                    str(entry.get("excerpt", "")).rstrip(),
                    f"END_CONTEXT_EXCERPT {entry['label']}",
                ]
            )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read one or more input context files and render a bounded context pack. "
            "By default the pack is printed to stdout; use --output to write it to a file."
        )
    )
    parser.add_argument("--worktree", required=True)
    parser.add_argument(
        "--context-file",
        action="append",
        default=[],
        help="Input context file to include or reference. Repeat for multiple inputs; this is not an output path.",
    )
    parser.add_argument("--total-chars", type=int, default=DEFAULT_TOTAL_CHARS)
    parser.add_argument("--per-file-chars", type=int, default=DEFAULT_PER_FILE_CHARS)
    parser.add_argument(
        "--include-worktree-excerpts",
        action="store_true",
        help="Embed bounded excerpts for context files inside --worktree instead of path-only references.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--output", help="Optional output file for the rendered JSON or Markdown pack.")
    args = parser.parse_args()

    if bool(args.json) == bool(args.markdown):
        raise SystemExit("choose exactly one of --json or --markdown")
    worktree = Path(args.worktree).resolve()
    paths = [Path(value).resolve() for value in args.context_file]
    pack = pack_context(
        worktree=worktree,
        context_files=paths,
        total_chars=args.total_chars,
        per_file_chars=args.per_file_chars,
        include_worktree_excerpts=args.include_worktree_excerpts,
    )
    rendered = json.dumps(pack, indent=2, sort_keys=True) + "\n" if args.json else markdown_from_pack(pack) + "\n"
    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
        print(output_path.as_posix())
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
