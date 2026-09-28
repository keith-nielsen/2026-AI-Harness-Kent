"""verify-clean.sh finds every kind of Kent leftover in a fake root and passes on a clean one.
Live-system checks (units via systemctl, docker, ports) are skipped; accounts, groups and ufw
rules are read from the fake root's /etc files."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "install" / "services" / "verify-clean.sh"
HOME = "/home/op"


def run(root: Path, skip: str = "docker ports") -> subprocess.CompletedProcess:
    env = {**os.environ, "KENT_VERIFY_ROOT": str(root), "KENT_VERIFY_HOME": HOME, "KENT_VERIFY_SKIP": skip}
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)


def clean_root(tmp_path: Path) -> Path:
    r = tmp_path / "root"
    (r / "etc").mkdir(parents=True)
    (r / "etc/passwd").write_text("root:x:0:0::/root:/bin/bash\ngrafana:x:127:134::/usr/share/grafana:/bin/false\n"
                                  "administrator:x:1000:1000::/home/administrator:/bin/bash\n")
    (r / "etc/group").write_text("root:x:0:\ngrafana:x:134:\nadministrator:x:1000:\n")
    (r / "etc/ufw").mkdir()
    (r / "etc/ufw/user.rules").write_text("### tuple ### allow tcp 22 0.0.0.0/0 any 0.0.0.0/0 in\n")
    for d in ("etc/systemd/system/multi-user.target.wants", "opt/other", "var/lib/other", "srv",
              "usr/local/bin", "etc/sudoers.d", "var/lib/systemd/timers", "var/lib/systemd/linger",
              f"{HOME[1:]}/.config/systemd/user", f"{HOME[1:]}/.local/share/systemd/timers"):
        (r / d).mkdir(parents=True, exist_ok=True)
    (r / "etc/sudoers.d/mintupdate").write_text("x")
    (r / "var/lib/systemd/timers/stamp-apt-daily.timer").write_text("")
    return r


def test_clean_root_passes(tmp_path):
    r = run(clean_root(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no Kent components found" in r.stdout and "LEFTOVER" not in r.stdout


def test_grafana_account_is_never_flagged(tmp_path):
    assert "grafana" not in run(clean_root(tmp_path)).stdout


LEFTOVERS = {  # relative path -> content ("" = file, None = directory, "->x" = symlink)
    "etc/kent": None,
    "opt/kent-llama": None,
    "var/lib/kent": None,
    "var/lib/kent-install": None,
    "var/cache/kent-llama": None,
    "var/log/kent-squid": None,
    "srv/kent": None,
    "usr/local/bin/kent": "->/opt/kent-core/bin/kent",
    "etc/sudoers.d/92-kent-operators": "",
    "etc/polkit-1/rules.d/60-kent-llama.rules": "",
    "etc/systemd/system/kent-litellm.service": "",
    "etc/systemd/system/srv-kent-models.mount": "",
    "etc/systemd/system/multi-user.target.wants/kent-gitea.service": "->/etc/systemd/system/kent-gitea.service",
    "etc/systemd/system/grafana-server.service.d/kent.conf": "",
    "var/lib/systemd/timers/stamp-kent-digest.timer": "",
    "var/lib/systemd/linger/kent": "",
    f"{HOME[1:]}/.local/share/systemd/timers/stamp-kent-qa-audit.timer": "",
    f"{HOME[1:]}/.config/systemd/user/kent-digest.timer": "",
}


@pytest.mark.parametrize("rel", sorted(LEFTOVERS))
def test_each_leftover_path_is_found(tmp_path, rel):
    root = clean_root(tmp_path)
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    content = LEFTOVERS[rel]
    if content is None:
        p.mkdir()
    elif content.startswith("->"):
        p.symlink_to(content[2:])
    else:
        p.write_text(content)
    r = run(root)
    assert r.returncode == 1, r.stdout
    assert any(l.startswith("LEFTOVER") and l.endswith("/" + rel) for l in r.stdout.splitlines()), r.stdout


@pytest.mark.parametrize("name", ["litellm", "kent", "kent-llama", "kent-searxng", "gent-3bb1a77d"])
def test_accounts_are_found(tmp_path, name):
    root = clean_root(tmp_path)
    with (root / "etc/passwd").open("a") as f:
        f.write(f"{name}:x:990:990::/nonexistent:/usr/sbin/nologin\n")
    r = run(root)
    assert r.returncode == 1 and f"account   {name}" in r.stdout, r.stdout


@pytest.mark.parametrize("name", ["kent-operators", "kent-models", "gent-aa06ebe9"])
def test_groups_are_found(tmp_path, name):
    root = clean_root(tmp_path)
    with (root / "etc/group").open("a") as f:
        f.write(f"{name}:x:970:administrator\n")
    r = run(root)
    assert r.returncode == 1 and f"group     {name}" in r.stdout, r.stdout


def test_ufw_rules_tagged_kent_gent_are_found(tmp_path):
    root = clean_root(tmp_path)
    with (root / "etc/ufw/user.rules").open("a") as f:
        f.write("### tuple ### allow tcp 3129 0.0.0.0/0 any 172.30.0.1 in_kent-gent0 comment=6b656e742d67656e74\n"
                "# kent-gent\n")
    r = run(root)
    assert r.returncode == 1 and "LEFTOVER     ufw" in r.stdout, r.stdout


def test_unreadable_polkit_dir_is_reported_not_checked(tmp_path):
    root = clean_root(tmp_path)
    d = root / "etc/polkit-1/rules.d"
    d.mkdir(parents=True)
    d.chmod(0o000)
    try:
        r = run(root)
    finally:
        d.chmod(0o755)
    if os.geteuid() == 0:
        pytest.skip("root can read any directory")
    assert r.returncode == 0 and "NOT CHECKED" in r.stdout and "polkit" in r.stdout, r.stdout


def test_skips_and_usage():
    r = subprocess.run(["bash", str(SCRIPT), "--bogus"], capture_output=True, text=True)
    assert r.returncode == 2
    h = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert h.returncode == 0 and "LEFTOVER" in h.stdout


def test_operator_config_is_info_not_a_leftover(tmp_path):
    # uninstall deliberately leaves ~/.config/kent for the operator; it must not fail --purge's check.
    root = clean_root(tmp_path)
    (root / HOME[1:] / ".config/kent").mkdir(parents=True)
    r = run(root)
    assert r.returncode == 0, r.stdout
    assert any(l.startswith("INFO") and ".config/kent" in l for l in r.stdout.splitlines()), r.stdout
