"""A model-hash rehash reports exactly what changed, per file (audit trail for
`kent-admin models rehash`): added, changed, removed, or unchanged."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "install" / "services" / "llama" / "bin" / "kent_llama_hashes.py"
A, B, C = "a" * 64, "b" * 64, "c" * 64


def run(tmp_path, old: str | None, new: str) -> list[str]:
    o, n = tmp_path / "old", tmp_path / "new"
    if old is not None:
        o.write_text(old)
    n.write_text(new)
    r = subprocess.run([sys.executable, str(SRC), str(o), str(n)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines()


def test_unchanged(tmp_path):
    rec = f"{A}  m1.gguf\n{B}  m2.gguf\n"
    assert run(tmp_path, rec, rec) == ["unchanged (2 files)"]


def test_added_changed_removed_sorted_by_name(tmp_path):
    old = f"{A}  keep.gguf\n{B}  swap.gguf\n{C}  gone.gguf\n"
    new = f"{A}  keep.gguf\n{C}  swap.gguf\n{B}  new.gguf\n"
    assert run(tmp_path, old, new) == [
        f"removed gone.gguf sha256 {C}",
        f"added new.gguf sha256 {B}",
        f"changed swap.gguf sha256 {B} -> {C}",
    ]


def test_missing_old_record_means_everything_added(tmp_path):
    assert run(tmp_path, None, f"{A}  m.gguf\n") == [f"added m.gguf sha256 {A}"]


def test_empty_old_record_means_everything_added(tmp_path):
    assert run(tmp_path, "", f"{A}  m.gguf\n") == [f"added m.gguf sha256 {A}"]


def test_names_with_spaces_and_uppercase_hex(tmp_path):
    old = f"{A.upper()}  my model.gguf\n"
    assert run(tmp_path, old, f"{A}  my model.gguf\n") == ["unchanged (1 files)"]


def test_malformed_lines_are_reported_and_ignored(tmp_path):
    new = f"{A}  ok.gguf\nnot-a-hash  bad.gguf\n\n"
    out = run(tmp_path, f"{A}  ok.gguf\n", new)
    assert out[0].startswith("ignored malformed line in ") and "not-a-hash" in out[0]
    assert out[1:] == ["unchanged (1 files)"]


def test_usage_error_without_two_arguments():
    r = subprocess.run([sys.executable, str(SRC)], capture_output=True, text=True)
    assert r.returncode == 2 and "usage" in r.stderr
