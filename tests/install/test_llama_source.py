"""Where the models live (kent-llama-source): recorded by filesystem at install, so a failed
`kent llama start` names a moved, missing or unmounted models drive. Regression: on 2026-09-29 an
unclean udisks clean-up left an empty /media/administrator/DATA behind and the drive came back as
DATA1; the model server could not start and nothing said why."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SVC = Path(__file__).resolve().parents[2] / "install" / "services"
spec = importlib.util.spec_from_file_location("kent_llama_source", SVC / "llama" / "bin" / "kent_llama_source.py")
src = importlib.util.module_from_spec(spec)
spec.loader.exec_module(src)

DEV, UUID = "/dev/nvme1n1p2", "a3c42b48-778a-4f6f-afab-38d764122839"
ROOT = "29 1 259:2 / / rw,relatime shared:1 - ext4 /dev/nvme0n1p2 rw"


def mi(*targets: str, root: str = "/") -> list[dict]:
    lines = [ROOT] + [f"1067 33 259:6 {root} {t.replace(' ', chr(92) + '040')} rw,nosuid,nodev shared:561 - ext4 {DEV} rw"
                      for t in targets]
    return src.parse_mountinfo("\n".join(lines))


REC = {"path": "/media/administrator/DATA/models", "uuid": UUID, "subpath": "models",
       "mountpoint": "/media/administrator/DATA", "automount": "1"}


def test_mountinfo_unescapes_spaces():
    assert mi("/media/administrator/Dump 1")[1]["target"] == "/media/administrator/Dump 1"


def test_record_names_filesystem_and_path_inside_it():
    r = src.record("/media/administrator/DATA/models", mi("/media/administrator/DATA"), set(), {DEV: UUID})
    assert r == REC


def test_record_counts_a_bind_mounts_own_root():
    r = src.record("/mnt/m/models", mi("/mnt/m", root="/data"), set(), {DEV: UUID})
    assert r["subpath"] == "data/models" and r["automount"] == "0"


def test_fstab_mount_under_media_is_not_an_automount():
    fstab = src.fstab_targets(f"# comment\nUUID={UUID} /media/administrator/DATA ext4 nofail 0 2\n")
    r = src.record("/media/administrator/DATA/models", mi("/media/administrator/DATA"), fstab, {DEV: UUID})
    assert r["automount"] == "0"


def test_in_place():
    assert src.diagnose(REC, mi("/media/administrator/DATA"), DEV, exists=lambda p: True) is None


def test_the_data1_incident_names_the_stale_directory_and_the_fix():
    exists = {"/media/administrator/DATA"}.__contains__          # empty leftover; no models inside
    msg = src.diagnose(REC, mi("/media/administrator/DATA1"), DEV, exists=exists, is_empty=lambda p: True)
    assert "mounted at /media/administrator/DATA1, not /media/administrator/DATA" in msg
    assert "sudo rmdir /media/administrator/DATA" in msg and f"udisksctl mount -b {DEV}" in msg


def test_moved_without_a_leftover_suggests_the_new_path():
    msg = src.diagnose(REC, mi("/media/administrator/DATA1"), DEV, exists=lambda p: False)
    assert "--models-dir /media/administrator/DATA1/models" in msg and "rmdir" not in msg


def test_not_mounted():
    msg = src.diagnose(REC, mi(), DEV, exists=lambda p: False)
    assert "not mounted" in msg and f"udisksctl mount -b {DEV}" in msg


def test_not_connected():
    msg = src.diagnose(REC, mi(), None, exists=lambda p: False)
    assert UUID in msg and "not connected" in msg


def test_directory_on_the_wrong_filesystem_is_not_in_place():
    # e.g. someone recreated DATA/models on the root disk: the bind mount would serve an empty directory.
    msg = src.diagnose(REC, mi(), DEV, exists=lambda p: True)
    assert "is on /dev/nvme0n1p2, not on the models drive" in msg


def test_mount_unit_releases_before_udisks_and_never_stalls_shutdown():
    unit = (SVC / "llama" / "systemd" / "srv-kent-models.mount.in").read_text()
    assert "After=udisks2.service" in unit and "LazyUnmount=yes" in unit
    assert "Options=bind,ro,nodev,nosuid,noexec" in unit


def test_config_dir_lets_operators_read_records_by_name():
    text = (SVC / "llama" / "install.sh").read_text()
    assert 'ensure_dir "$CONF" 0751 root kent-llama' in text
    assert 'place_file file "$HERE/llama.env" "$CONF/llama.env" 0640' in text
