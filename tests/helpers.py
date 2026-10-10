import datetime as dt
import math
import random


def business_days(start, n):
    d, out = dt.date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def make_series(n=400, seed=7, shocks=None, beta=1.1, noise=0.012, calm_after=None):
    """Synthetic stock + benchmark bars. shocks: {index: extra log return added to the stock}."""
    rng = random.Random(seed)
    dates = business_days("2025-01-01", n)
    shocks = shocks or {}
    s_px, b_px = 1000.0, 20000.0
    stock, bench = [], []
    for i, d in enumerate(dates):
        m = rng.gauss(0.0003, 0.01) if i else 0.0
        e = rng.gauss(0, noise if not (calm_after and i >= calm_after) else noise * 0.5) if i else 0.0
        r = 0.0002 + beta * m + e + shocks.get(i, 0.0) if i else 0.0
        prev_s, prev_b = s_px, b_px
        s_px *= math.exp(r)
        b_px *= math.exp(m)
        for lst, px, prev in ((stock, s_px, prev_s), (bench, b_px, prev_b)):
            hi = max(px, prev) * (1 + abs(rng.gauss(0, 0.004)))
            lo = min(px, prev) * (1 - abs(rng.gauss(0, 0.004)))
            lst.append({"date": d, "open": prev, "high": hi, "low": lo, "close": px, "adjclose": px,
                        "volume": 1_000_000 * (3 if i in shocks else 1)})
    return stock, bench
