"""run.py: Kent items grant their share to the sandboxed Kent (docs/design/kent-sandbox.md §5)."""
import sys
from pathlib import Path

import pytest

sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parent / "suites")]
import run  # noqa: E402

IT = {"id": "k01", "prompt": "Tidy {d} please"}
D = Path("/var/lib/kent-bench/share/abc-k01-r0")


def test_default_is_rw():
    assert run.kent_cmd(IT, D) == ("rw", ["kent", "--grant-rw", str(D), "-q", f"Tidy {D} please"])


def test_item_mode():
    assert run.kent_cmd({**IT, "grant": "ro"}, D)[1][:3] == ["kent", "--grant", str(D)]
    assert run.kent_cmd({**IT, "grant": "none"}, D)[1] == ["kent", "-q", f"Tidy {D} please"]


def test_run_override_wins():
    assert run.kent_cmd({**IT, "grant": "rw"}, D, "ro")[0] == "ro"


def test_bad_mode():
    with pytest.raises(ValueError):
        run.kent_cmd({**IT, "grant": "all"}, D)
