#!/usr/bin/env python3
"""Shared path and naming rules for goal orchestration scripts."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath


SAFE_ID_RE = re.compile(r"^[A-Z][A-Z0-9_-]{1,31}$")
SAFE_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,63}$")
SAFE_PACKET_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
INVALID_BRANCH_CHARS = set(" ~^:?*[\\")
PORCELAIN_PREFIX_RE = re.compile(r"^[ MADRCU?!]{2} ")


def is_strict_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def require_safe_id(value: str, field: str) -> str:
    if not SAFE_ID_RE.fullmatch(value):
        raise SystemExit(f"{field} must match {SAFE_ID_RE.pattern}: {value!r}")
    return value


def require_safe_label(value: str, field: str) -> str:
    if not SAFE_LABEL_RE.fullmatch(value):
        raise SystemExit(f"{field} must match {SAFE_LABEL_RE.pattern}: {value!r}")
    return value


def require_safe_packet_label(value: str, field: str) -> str:
    if not SAFE_PACKET_LABEL_RE.fullmatch(value):
        raise SystemExit(f"{field} must match {SAFE_PACKET_LABEL_RE.pattern}: {value!r}")
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


def is_absolute_path(value: str) -> bool:
    path = Path(value)
    return not (
        "\\" in value
        or not path.is_absolute()
        or ".." in path.parts
    )


def resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def relative_path_defect(value: object, field: str, *, include_value: bool = False) -> str | None:
    suffix = f": {value!r}" if include_value else ""
    if not isinstance(value, str) or not value:
        return f"{field} must be a non-empty relative path"
    if value == ".":
        return f"{field} must not be '.'{suffix}"
    if "\\" in value:
        return f"{field} must use POSIX '/' separators, not backslashes{suffix}"
    if "//" in value:
        return f"{field} must not contain empty path segments{suffix}"
    if value.startswith("./") or "/./" in value or value.endswith("/."):
        return f"{field} must not contain '.' path segments{suffix}"
    path = PurePosixPath(value)
    if path.is_absolute():
        return f"{field} must be relative, not absolute{suffix}"
    if any(part in {"", ".", ".."} for part in path.parts):
        return f"{field} must not contain empty, '.', or '..' segments{suffix}"
    return None


def require_relative_path(value: object, field: str) -> str:
    message = relative_path_defect(value, field, include_value=True)
    if message:
        raise SystemExit(message)
    return PurePosixPath(str(value)).as_posix()


def repo_relative_path(path: Path, base_dir: Path, field: str) -> str:
    try:
        relative = path.resolve().relative_to(base_dir.resolve())
    except ValueError as exc:
        raise SystemExit(f"{field} must be inside --base-dir: {path}") from exc
    text = relative.as_posix()
    parts = PurePosixPath(text).parts
    if not text or text == "." or any(part in {"", ".", ".."} for part in parts):
        raise SystemExit(f"{field} resolved to an unsafe relative path: {text!r}")
    return text


def is_repo_relative_path(value: str, *, reject_porcelain: bool = False) -> bool:
    path = Path(value)
    return not (
        "\\" in value
        or value.startswith("/")
        or value.startswith("./")
        or value == "."
        or "/./" in value
        or value.endswith("/.")
        or "//" in value
        or any(part in {"", ".", ".."} for part in path.parts)
        or (reject_porcelain and PORCELAIN_PREFIX_RE.match(value) is not None)
    )


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


def require_branch_name(value: str, field: str = "branch_name") -> str:
    if not safe_branch_name(value):
        raise SystemExit(f"{field} is not a safe git branch name: {value!r}")
    return value
