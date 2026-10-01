"""The llama uninstall refuses to run while the recorded model directory is unreachable (drive not
mounted): deleting the kent-models group then would leave the model files owned by a group that no
longer exists. Runs the module's uninstall in --dry-run with a temporary manifest (no root)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

UNINSTALL = Path(__file__).resolve().parents[2] / "install" / "services" / "llama" / "uninstall.sh"


def run(tmp_path, models_dir: Path) -> subprocess.CompletedProcess:
    mdir = tmp_path / "manifest"
    mdir.mkdir()
    (mdir / "llama").write_text(f"modelsdir {models_dir}\n")
    env = {**os.environ, "KENT_MANIFEST_DIR": str(mdir)}
    return subprocess.run(["bash", str(UNINSTALL), "--dry-run"], env=env, capture_output=True, text=True)


def test_missing_models_dir_is_reported_before_any_change(tmp_path):
    r = run(tmp_path, tmp_path / "not-mounted" / "models")
    assert "is not available (drive not mounted?)" in r.stderr
    assert "a real run would stop here" in r.stderr
    # The warning comes before the first action (stopping the server).
    out = r.stderr + r.stdout
    assert r.stdout.find("systemctl stop") == -1 or out.index("not available") < out.index("systemctl stop")


def test_present_models_dir_passes_the_guard(tmp_path):
    d = tmp_path / "models"
    d.mkdir()
    r = run(tmp_path, d)
    assert r.returncode == 0 and "not available" not in r.stderr
