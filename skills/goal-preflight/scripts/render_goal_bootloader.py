#!/usr/bin/env python3
"""Print the final /goal bootloader text from a preflight bundle."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", required=True)
    args = parser.parse_args()

    path = Path(args.bundle_dir).expanduser().resolve() / "goal-bootloader.md"
    if not path.exists():
        raise SystemExit(f"missing bootloader: {path}")
    text = path.read_text(encoding="utf-8")
    print(text, end="" if text.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
