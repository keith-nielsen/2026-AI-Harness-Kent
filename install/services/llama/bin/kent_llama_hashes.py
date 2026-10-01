#!/usr/bin/python3 -I
"""kent-llama-hashes OLD NEW — what a model-hash rehash changed, one line per file.

Both files hold `<sha256>  <basename>` lines (sha256sum format). OLD may be missing or empty
(first install). Prints, sorted by name:

  added <name> sha256 <new>
  changed <name> sha256 <old> -> <new>
  removed <name> sha256 <old>

or `unchanged (<n> files)` when nothing differs. Lines that are not a 64-hex hash followed by a
name are reported as `ignored malformed line in <file>: <line>` and otherwise skipped. Exit 0.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

LINE = re.compile(r"^([0-9a-fA-F]{64}) [ *](.+)$")


def read_record(path: str, notes: list[str]) -> dict[str, str]:
    p = Path(path)
    if not p.is_file():
        return {}
    record: dict[str, str] = {}
    for raw in p.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = LINE.match(line)
        if not m:
            notes.append(f"ignored malformed line in {path}: {line[:120]}")
            continue
        record[m.group(2).strip()] = m.group(1).lower()
    return record


def diff(old: dict[str, str], new: dict[str, str]) -> list[str]:
    out = []
    for name in sorted(old.keys() | new.keys()):
        o, n = old.get(name), new.get(name)
        if o is None:
            out.append(f"added {name} sha256 {n}")
        elif n is None:
            out.append(f"removed {name} sha256 {o}")
        elif o != n:
            out.append(f"changed {name} sha256 {o} -> {n}")
    return out or [f"unchanged ({len(new)} files)"]


def main(args: list[str]) -> int:
    if len(args) != 2:
        print("usage: kent-llama-hashes OLD NEW", file=sys.stderr)
        return 2
    notes: list[str] = []
    old, new = read_record(args[0], notes), read_record(args[1], notes)
    print("\n".join(notes + diff(old, new)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
