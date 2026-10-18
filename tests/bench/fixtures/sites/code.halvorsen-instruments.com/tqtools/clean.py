import csv
import io

from .units import to_riders


def clean_rows(text):
    """Parse the CSV export: drop exact duplicate rows and missing counts; convert units."""
    seen, out = set(), []
    for r in csv.DictReader(io.StringIO(text)):
        key = r["date"]
        if key in seen:
            continue
        seen.add(key)
        n = to_riders(r["count"], r["unit"])
        if n is None:
            continue
        out.append({"date": r["date"], "site": r["site"], "riders": n})
    return out
