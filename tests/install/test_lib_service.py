"""Layer 5: the install library refuses to adopt or overwrite what Kent didn't
create, records exactly what it creates, and uninstalls only that. Runs
lib-service.sh functions in --dry-run with a temporary manifest (no root)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

LIB = Path(__file__).resolve().parents[2] / "install" / "services" / "lib-service.sh"


def sh(tmp_path, script: str, manifest: str = "") -> subprocess.CompletedProcess:
    mdir = tmp_path / "manifest"
    mdir.mkdir(exist_ok=True)
    (mdir / "t").write_text(manifest)
    env = {**os.environ, "KENT_MANIFEST_DIR": str(mdir), "DRY_RUN": "1", "SERVICE": "t"}
    return subprocess.run(["bash", "-c", f"source {LIB}; SERVICE=t; {script}"], env=env,
                          capture_output=True, text=True)


def test_refuses_to_adopt_existing_account(tmp_path):
    r = sh(tmp_path, "ensure_service_account daemon /nonexistent test")
    assert r.returncode != 0 and "refusing to adopt" in r.stderr


def test_reuses_account_it_created(tmp_path):
    r = sh(tmp_path, "ensure_service_account daemon /nonexistent test", "user daemon\ngroup daemon\n")
    assert r.returncode == 0 and "created by Kent" in r.stdout


def test_new_account_is_created_and_recorded(tmp_path):
    r = sh(tmp_path, "ensure_service_account kent-nonexistent-xyz /x t")
    assert r.returncode == 0
    assert "useradd --system --user-group" in r.stdout and "manifest += user kent-nonexistent-xyz" in r.stdout


def test_refuses_existing_group_without_user(tmp_path):
    r = sh(tmp_path, "ensure_service_account adm /x t")   # 'adm' exists as user and group on Ubuntu
    assert r.returncode != 0 and "refusing to adopt" in r.stderr


def test_claim_path_refuses_foreign_path(tmp_path):
    f = tmp_path / "vendor.conf"
    f.write_text("x")
    r = sh(tmp_path, f"claim_path file {f}")
    assert r.returncode != 0 and "refusing to overwrite" in r.stderr


def test_claim_path_allows_own_path(tmp_path):
    f = tmp_path / "ours.conf"
    f.write_text("x")
    assert sh(tmp_path, f"claim_path file {f}", f"file {f}\n").returncode == 0


def test_ensure_dir_never_repermissions_foreign_dir(tmp_path):
    d = tmp_path / "vendor"
    d.mkdir()
    r = sh(tmp_path, f"ensure_dir {d} 0700 root root")
    assert r.returncode == 0 and "install -d" not in r.stdout


def test_ensure_dir_records_missing_ancestors(tmp_path):
    d = tmp_path / "a" / "b" / "c"
    r = sh(tmp_path, f"ensure_dir {d} 0750 root root")
    assert r.returncode == 0
    for p in (tmp_path / "a", tmp_path / "a" / "b", d):
        assert f"manifest += dir {p}" in r.stdout


def test_manifest_add_is_idempotent(tmp_path):
    r = sh(tmp_path, "manifest_add file /x; manifest_add file /x", "file /x\n")
    assert "manifest +=" not in r.stdout


def test_record_unit_state_only_once(tmp_path):
    r = sh(tmp_path, "record_unit_state ssh.service", "unitstate ssh.service enabled active\n")
    assert "manifest +=" not in r.stdout


def test_uninstall_touches_only_manifest_entries(tmp_path):
    m = ("user kent-x\ngroup kent-x\nfile /etc/kent/x.conf\nunit /etc/systemd/system/kent-x.service\n"
         "dir /etc/kent/x\nstate /var/lib/kent-x\n")
    r = sh(tmp_path, "uninstall_module", m)
    out = r.stdout
    assert r.returncode == 0, r.stderr
    assert "rm -f /etc/kent/x.conf" in out and "rm -f /etc/systemd/system/kent-x.service" in out
    assert "kept state: /var/lib/kent-x" in out and "/var/lib/kent-x" not in out.split("kept state")[0].split("rm -rf")[-1]
    for line in out.splitlines():
        if "[dry-run]" in line and any(k in line for k in (" rm ", " userdel ", " groupdel ", " rmdir ")):
            assert any(p in line for p in ("/etc/kent/x", "kent-x", str(tmp_path))), line


def test_uninstall_purge_removes_state(tmp_path):
    r = sh(tmp_path, "PURGE_STATE=1 uninstall_module", "state /var/lib/kent-x\n")
    assert "rm -rf --one-file-system /var/lib/kent-x" in r.stdout


def test_uninstall_restores_moved_paths_and_unit_state(tmp_path):
    bak = tmp_path / "bak"
    bak.mkdir()
    r = sh(tmp_path, "uninstall_module",
           f"unitstate grafana-server.service disabled inactive\nmoved /var/lib/grafana {bak}\n")
    assert "systemctl disable grafana-server.service" in r.stdout
    assert f"mv {bak} /var/lib/grafana" in r.stdout


def test_uninstall_without_manifest_refuses(tmp_path):
    env = {**os.environ, "KENT_MANIFEST_DIR": str(tmp_path / "none"), "DRY_RUN": "1"}
    r = subprocess.run(["bash", "-c", f"source {LIB}; SERVICE=t; uninstall_module"], env=env,
                       capture_output=True, text=True)
    assert r.returncode != 0 and "nothing recorded" in r.stderr


def test_uninstall_disables_kent_enabled_vendor_unit_before_purge(tmp_path):
    r = sh(tmp_path, "uninstall_module", "package alloy\nenabled alloy.service\n")
    out = r.stdout
    assert "systemctl disable --now alloy.service" in out
    assert out.index("systemctl disable --now alloy.service") < out.index("apt-get purge")


def test_uninstall_removes_group_membership_it_added(tmp_path):
    user = os.environ.get("USER", "root")
    grp = subprocess.run(["id", "-gn"], capture_output=True, text=True).stdout.strip()
    r = sh(tmp_path, "uninstall_module", f"member {grp} {user}\n")
    assert f"gpasswd -d {user} {grp}" in r.stdout
