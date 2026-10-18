from collections import defaultdict


def site_totals(rows):
    """Total riders per site, busiest site first."""
    t = defaultdict(int)
    for r in rows:
        t[r["site"]] += r["riders"]
    return dict(sorted(t.items(), key=lambda kv: kv[1]))


def peak_day(rows):
    """(date, riders) of the day with the most riders across all sites."""
    d = defaultdict(int)
    for r in rows:
        d[r["date"]] += r["riders"]
    return min(d.items(), key=lambda kv: kv[1])
