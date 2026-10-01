#!/usr/bin/env python3
"""kent-llama-source — where the model files live, recorded by filesystem so a moved mount is named.

  kent-llama-source record DIR   print the record for DIR (the installer keeps it as
                                 /etc/kent/llama/models.source): path, filesystem UUID, the
                                 directory's path inside that filesystem, the mount point, and
                                 whether that mount is a desktop automount (udisks, not in fstab)
  kent-llama-source diagnose [RECORD]
                                 exit 0 if the recorded directory is where it was recorded; else
                                 print the likely cause and a fix, exit 1

A desktop automount appears only after the operator logs in, and its mount point can change name
(DATA -> DATA1) when an unclean shutdown leaves the old, empty directory behind. The record lets
`kent llama start` say so instead of reporting a bare mount failure. Kent never removes that
directory itself: it is not a path Kent created.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RECORD = "/etc/kent/llama/models.source"
BY_UUID = "/dev/disk/by-uuid"


def _unescape(s: str) -> str:  # mountinfo and fstab escape spaces etc. as \040 (octal)
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and s[i + 1:i + 4].isdigit():
            out.append(chr(int(s[i + 1:i + 4], 8))); i += 4
        else:
            out.append(s[i]); i += 1
    return "".join(out)


def parse_mountinfo(text: str) -> list[dict]:
    """One dict per mount: root (path inside the filesystem), target, fstype, source."""
    mounts = []
    for line in text.splitlines():
        left, sep, right = line.partition(" - ")
        f, r = left.split(), right.split()
        if not sep or len(f) < 5 or len(r) < 2:
            continue
        mounts.append({"root": _unescape(f[3]), "target": _unescape(f[4]), "fstype": r[0], "source": _unescape(r[1])})
    return mounts


def fstab_targets(text: str) -> set[str]:
    return {_unescape(l.split()[1]) for l in text.splitlines()
            if l.strip() and not l.lstrip().startswith("#") and len(l.split()) >= 2}


def containing_mount(path: str, mounts: list[dict]) -> dict | None:
    """The mount a path lives on: the longest mount point that is a prefix of it (last one wins)."""
    best = None
    for m in mounts:
        t = m["target"].rstrip("/") or "/"
        if path == t or path.startswith(t.rstrip("/") + "/"):
            if best is None or len(t) >= len(best["target"].rstrip("/") or "/"):
                best = m
    return best


def is_automount(target: str, fstab: set[str]) -> bool:
    """Mounted by the desktop (udisks) for a logged-in user rather than by the system."""
    return target.startswith(("/media/", "/run/media/")) and target not in fstab


def record(path: str, mounts: list[dict], fstab: set[str], uuid_of: dict[str, str]) -> dict:
    m = containing_mount(path, mounts)
    if m is None:
        raise ValueError(f"{path}: no mount found")
    t = m["target"].rstrip("/") or "/"
    inside = path[len(t):].lstrip("/") if t != "/" else path.lstrip("/")
    sub = "/".join(p for p in (m["root"].strip("/"), inside) if p)   # a bind mount's own root counts
    return {"path": path, "uuid": uuid_of.get(os.path.realpath(m["source"]), ""), "subpath": sub,
            "mountpoint": t, "automount": "1" if is_automount(t, fstab) else "0"}


def diagnose(rec: dict, mounts: list[dict], dev_of_uuid: str | None,
             exists=os.path.isdir, is_empty=lambda p: not os.listdir(p)) -> str | None:
    """None if the recorded directory is in place, else the cause and a fix (plain language)."""
    path, uuid, sub, mp = rec.get("path", ""), rec.get("uuid", ""), rec.get("subpath", ""), rec.get("mountpoint", "")
    if not uuid:   # nothing recorded to compare against (e.g. models on the root filesystem)
        return None if exists(path) else f"the models directory {path} is missing"
    here = containing_mount(path, mounts) if exists(path) else None
    if here is not None and dev_of_uuid and os.path.realpath(here["source"]) == dev_of_uuid:
        return None
    if dev_of_uuid is None:
        return (f"the models drive (filesystem UUID {uuid}) is not connected, so {path} is missing. "
                "Connect it; then: kent llama start")
    # The directory can exist on the wrong filesystem (e.g. an empty one on the root disk); the bind
    # mount would then serve that instead of the models.
    state = (f"{path} is on {here['source']}, not on the models drive {dev_of_uuid}" if here is not None
             else f"{path} is missing")
    elsewhere = [m["target"] for m in mounts if os.path.realpath(m["source"]) == dev_of_uuid and m["root"] == "/"]
    if not elsewhere:
        return (f"the models drive ({dev_of_uuid}, UUID {uuid}) is not mounted, so {state}. Open it in the "
                f"file manager (or: udisksctl mount -b {dev_of_uuid}); then: kent llama start")
    at = elsewhere[0]
    msg = f"the models drive is mounted at {at}, not {mp}, so {state}."
    if exists(mp) and is_empty(mp):
        msg += (f" An empty {mp} left by an unclean shutdown is blocking the usual name. Fix: "
                f"udisksctl unmount -b {dev_of_uuid}; sudo rmdir {mp}; udisksctl mount -b {dev_of_uuid}")
    else:
        msg += (f" Mount it at {mp} again, or point Kent at the new place: "
                f"sudo install/services/llama/install.sh --models-dir {at}/{sub}")
    return msg

def _uuid_map() -> dict[str, str]:
    d = Path(BY_UUID)
    return {os.path.realpath(p): p.name for p in d.iterdir()} if d.is_dir() else {}


def main(args: list[str]) -> int:
    mounts = parse_mountinfo(Path("/proc/self/mountinfo").read_text())
    if args[:1] == ["record"] and len(args) == 2:
        fstab = fstab_targets(Path("/etc/fstab").read_text()) if Path("/etc/fstab").exists() else set()
        try:
            rec = record(os.path.realpath(args[1]), mounts, fstab, _uuid_map())
        except ValueError as e:
            print(f"kent-llama-source: {e}", file=sys.stderr); return 1
        print("\n".join(f"{k} {v}" for k, v in rec.items()))
        return 0
    if args[:1] == ["diagnose"] and len(args) <= 2:
        try:
            text = Path(args[1] if len(args) == 2 else RECORD).read_text()
        except OSError as e:
            print(f"kent-llama-source: no record of the models directory ({e})", file=sys.stderr); return 1
        rec = dict(l.split(" ", 1) for l in text.splitlines() if " " in l)
        dev = os.path.realpath(f"{BY_UUID}/{rec['uuid']}") if rec.get("uuid") and os.path.exists(f"{BY_UUID}/{rec['uuid']}") else None
        cause = diagnose(rec, mounts, dev)
        if cause:
            print(cause); return 1
        return 0
    print(__doc__.strip().splitlines()[0], file=sys.stderr)
    print("usage: kent-llama-source record DIR | diagnose [RECORD]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
