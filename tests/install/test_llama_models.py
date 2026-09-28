"""Model files by profile (llama module, models-perms.sh): the lab profile leaves the operator's
files as they are (write bits removed only), the hardened profile locks them down and restores
them on uninstall. Runs the real scripts in --dry-run with a temporary manifest and models
directory (no root)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SVC = Path(__file__).resolve().parents[2] / "install" / "services"
LIB, PERMS = SVC / "lib-service.sh", SVC / "llama" / "models-perms.sh"


@pytest.fixture
def env(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    for name, mode in (("a.gguf", 0o644), ("b.gguf", 0o446), ("c.gguf", 0o444), ("d.safetensors", 0o600)):
        (models / name).write_bytes(b"x")
        (models / name).chmod(mode)
    mdir = tmp_path / "manifest"
    mdir.mkdir()
    e = {**os.environ, "KENT_MANIFEST_DIR": str(mdir), "DRY_RUN": "1", "SERVICE": "llama",
         "KENT_PERMS_BACKUP": str(tmp_path / "models-perms"), "KENT_PROFILE_FILE": str(tmp_path / "profile"),
         "KENT_OPERATOR": "administrator"}
    return {"env": e, "models": models, "manifest": mdir / "llama", "tmp": tmp_path}


def funcs(env, script: str, manifest: str = "") -> subprocess.CompletedProcess:
    env["manifest"].write_text(manifest)
    return subprocess.run(["bash", "-c", f"source {LIB}; SERVICE=llama; source {PERMS}; "
                           f"MODELS_DIR={env['models']}; {script}"],
                          env=env["env"], capture_output=True, text=True)


def installer(env, *args: str, manifest: str) -> subprocess.CompletedProcess:
    # --rehash re-applies the models mode and re-records hashes; it runs before the build/GPU preflight.
    env["manifest"].write_text(manifest + "file /etc/kent/llama/models.sha256\n")
    return subprocess.run([str(SVC / "llama" / "install.sh"), "--dry-run", "--rehash",
                           "--models-dir", str(env["models"]), *args],
                          env=env["env"], capture_output=True, text=True)


def test_lab_keeps_owner_and_only_removes_write_bits(env):
    r = funcs(env, "apply_models_mode lab")
    assert r.returncode == 0, r.stderr
    assert "chown" not in r.stdout
    m = env["models"]
    assert f"chmod a-w {m}/a.gguf" in r.stdout and f"chmod a-w {m}/b.gguf" in r.stdout
    assert f"{m}/c.gguf" not in r.stdout                      # already read-only: untouched
    assert f"chmod 0750 {m}" not in r.stdout and "chmod 0440" not in r.stdout
    assert "manifest += modelsmode lab" in r.stdout


def test_lab_warns_about_files_kent_llama_cannot_read(env):
    r = funcs(env, "apply_models_mode lab")
    assert "d.safetensors is not world-readable" in r.stderr and "chown" not in r.stdout


def test_hardened_locks_down_and_records(env):
    r = funcs(env, "apply_models_mode hardened")
    assert r.returncode == 0, r.stderr
    m = env["models"]
    assert f"chown root:kent-models {m}" in r.stdout and f"chmod 0750 {m}" in r.stdout
    assert f"chown root:kent-models {m}/a.gguf" in r.stdout and f"chmod 0440 {m}/a.gguf" in r.stdout
    assert "manifest += modelsmode hardened" in r.stdout


@pytest.mark.parametrize("explicit, manifest, profile_file, want", [
    ("hardened", "modelsmode lab\n", "lab", "hardened"),    # explicit --profile wins
    ("", "modelsmode hardened\n", "lab", "hardened"),       # then what was recorded
    ("", "modelsdir /x\n", "lab", "hardened"),              # pre-profile (rc.4) install = hardened
    ("", "", "hardened", "hardened"),                       # then Kent's profile
    ("", "", "", "lab"),                                    # else lab
])
def test_mode_resolution(env, explicit, manifest, profile_file, want):
    if profile_file:
        (env["tmp"] / "profile").write_text(profile_file + "\n")
    r = funcs(env, f'resolve_models_mode "{explicit}"', manifest)
    assert r.returncode == 0 and r.stdout.strip() == want, r.stderr


def test_rejects_unknown_profile(env):
    r = funcs(env, 'resolve_models_mode bogus')
    assert r.returncode != 0 and "lab or hardened" in r.stderr


def test_switch_hardened_to_lab_restores_first(env):
    m = env["models"]
    (env["tmp"] / "models-perms").write_text(f"dir 755 administrator administrator {m}\n"
                                              f"file 446 administrator administrator {m}/b.gguf\n")
    r = funcs(env, "apply_models_mode lab hardened", f"modelsdir {m}\nmodelsmode hardened\n")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert f"chown administrator:administrator {m}/b.gguf" in out
    assert out.index(f"chown administrator:administrator {m}/b.gguf") < out.index(f"chmod a-w {m}/b.gguf")
    assert "chown root:kent-models" not in out


def test_installer_lab_dry_run_has_no_chown(env):
    r = installer(env, "--profile", "lab", manifest=f"modelsdir {env['models']}\n")
    assert r.returncode == 0, r.stderr
    assert "chown" not in r.stdout and "chmod a-w" in r.stdout
    assert "manifest += modelsmode lab" in r.stdout


def test_installer_hardened_dry_run_locks_down(env):
    r = installer(env, "--profile", "hardened", manifest=f"modelsdir {env['models']}\n")
    assert r.returncode == 0, r.stderr
    assert f"chown root:kent-models {env['models']}" in r.stdout


def test_installer_keeps_recorded_mode_without_profile(env):
    r = installer(env, manifest=f"modelsdir {env['models']}\nmodelsmode lab\n")
    assert r.returncode == 0, r.stderr
    assert "chown" not in r.stdout and "chmod a-w" in r.stdout


def uninstall(env, manifest: str) -> subprocess.CompletedProcess:
    env["manifest"].write_text(manifest)
    return subprocess.run([str(SVC / "llama" / "uninstall.sh"), "--dry-run"],
                          env=env["env"], capture_output=True, text=True)


def test_uninstall_lab_leaves_model_files_alone(env):
    r = uninstall(env, f"modelsdir {env['models']}\nmodelsmode lab\n")
    assert r.returncode == 0, r.stderr
    assert "left as they are" in r.stdout
    assert str(env["models"]) not in "".join(l for l in r.stdout.splitlines(True) if "chown" in l or "chmod" in l)


def test_uninstall_hardened_restores_recorded_perms(env):
    m = env["models"]
    (env["tmp"] / "models-perms").write_text(f"file 446 administrator administrator {m}/b.gguf\n")
    r = uninstall(env, f"modelsdir {m}\nmodelsmode hardened\nmember kent-models administrator\n")
    assert r.returncode == 0, r.stderr
    assert f"chown administrator:administrator {m}/b.gguf" in r.stdout and f"chmod 446 {m}/b.gguf" in r.stdout
    assert f"chmod 0444 {m}/a.gguf" in r.stdout            # added after install: to the operator, 0444
