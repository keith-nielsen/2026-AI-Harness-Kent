def to_riders(count, unit):
    """Rider count as an int, or None when the count is missing."""
    if count == "":
        return None
    n = float(count)
    if unit == "hundred":
        n *= 100
    return round(n)
