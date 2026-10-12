"""Event study maths: did a news event coincide with a statistically abnormal drop, and has price levelled out since?

Method (standard market-model event study):
  r_t   = ln(adj_t / adj_{t-1})         stock log return
  m_t   = same for the benchmark (Nifty 50)
  fit     r_t = alpha + beta * m_t + e_t on an estimation window that ends `gap_sessions` before the event
  AR_t  = r_t - (alpha + beta * m_t)    abnormal return
  CAR   = sum of AR_t over the event window (event session + following sessions)
  A dip is CONFIRMED when CAR <= -max(-ln(1 - min_abnormal_drop), k * sigma_e * sqrt(n)).

What this does and does not prove: it shows the stock fell abnormally (beyond market moves and its own
normal noise) in the session(s) the news became public. It cannot prove the headline caused it; two events
in one session share the attribution. That limit is reported, not hidden.
"""
import datetime as dt
import math
import statistics

from . import config


class InsufficientData(Exception):
    pass


def align(stock_bars, bench_bars):
    bm = {b["date"]: b for b in bench_bars}
    return [(s, bm[s["date"]]) for s in stock_bars if s["date"] in bm]


def event_session(published_utc_iso, trading_dates):
    """First session in which the news could be traded on. None if that session isn't in the data yet."""
    t = dt.datetime.fromisoformat(published_utc_iso).astimezone(config.IST)
    day = t.date().isoformat()
    after_close = t.time() >= config.MARKET_CLOSE
    for td in trading_dates:
        if td > day or (td == day and not after_close):
            return td
    return None


def _r(pairs, i, which):
    k = 0 if which == "stock" else 1
    return math.log(pairs[i][k]["adjclose"] / pairs[i - 1][k]["adjclose"])


def market_model(pairs, e, ev):
    end = e - ev["gap_sessions"]
    start = max(1, end - ev["estimation_sessions"])
    if end - start < ev["min_estimation_sessions"]:
        raise InsufficientData(f"need {ev['min_estimation_sessions']} estimation sessions, have {max(0, end - start)}")
    ys = [_r(pairs, i, "stock") for i in range(start, end)]
    xs = [_r(pairs, i, "bench") for i in range(start, end)]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx > 0 else 0.0
    alpha = my - beta * mx
    resid = [y - alpha - beta * x for x, y in zip(xs, ys)]
    sigma = math.sqrt(sum(u * u for u in resid) / (len(resid) - 2))
    vols = [pairs[i][0]["volume"] for i in range(start, end)]
    return {"alpha": alpha, "beta": beta, "sigma": sigma, "n": len(ys),
            "median_volume": statistics.median(vols) if vols else 0.0}


def _ar(pairs, i, mm):
    return _r(pairs, i, "stock") - (mm["alpha"] + mm["beta"] * _r(pairs, i, "bench"))


def _index_of(pairs, date):
    for i, (s, _) in enumerate(pairs):
        if s["date"] == date:
            return i
    return None


def _tr_pct(pairs, i):
    """True range of session i as a fraction of the previous adjusted close (dividend-consistent)."""
    s, p = pairs[i][0], pairs[i - 1][0]
    f = s["adjclose"] / s["close"]
    h, l, pc = s["high"] * f, s["low"] * f, p["adjclose"]
    return max(h - l, abs(h - pc), abs(l - pc)) / pc


def assess_dip(stock_bars, bench_bars, session_date, ev):
    pairs = align(stock_bars, bench_bars)
    if not pairs:
        return {"status": "insufficient_data", "reason": "no overlapping stock/benchmark sessions"}
    e = _index_of(pairs, session_date)
    if e is None:
        if session_date > pairs[-1][0]["date"]:
            return {"status": "awaiting_data", "reason": "event session not in completed data yet"}
        return {"status": "insufficient_data", "reason": "stock did not trade on the event session"}
    try:
        mm = market_model(pairs, e, ev)
    except InsufficientData as exc:
        return {"status": "insufficient_data", "reason": str(exc)}
    window = ev["window_sessions"]
    n = min(window, len(pairs) - e)
    car = sum(_ar(pairs, i, mm) for i in range(e, e + n))
    threshold = max(-math.log(1 - ev["min_abnormal_drop"]), ev["sigma_multiple"] * mm["sigma"] * math.sqrt(n))
    raw = math.log(pairs[e + n - 1][0]["adjclose"] / pairs[e - 1][0]["adjclose"])
    mkt = math.log(pairs[e + n - 1][1]["adjclose"] / pairs[e - 1][1]["adjclose"])
    vol_ratio = (statistics.fmean(pairs[i][0]["volume"] for i in range(e, e + n)) / mm["median_volume"]
                 if mm["median_volume"] > 0 else None)
    if car <= -threshold:
        status = "dip_confirmed"
    elif n < window:
        status = "provisional"
    else:
        status = "no_dip"
    return {
        "status": status,
        "event_session": session_date,
        "sessions_evaluated": n,
        "abnormal_return": math.expm1(car),
        "threshold": -math.expm1(-threshold),        # as a positive % drop
        "z_score": car / (mm["sigma"] * math.sqrt(n)) if mm["sigma"] > 0 else None,
        "raw_return": math.expm1(raw),
        "market_return": math.expm1(mkt),
        "beta": mm["beta"],
        "daily_sigma": mm["sigma"],
        "volume_ratio": vol_ratio,
        "high_volume": vol_ratio is not None and vol_ratio >= ev["volume_ratio_flag"],
        "pre_event_date": pairs[e - 1][0]["date"],
        "pre_event_close": pairs[e - 1][0]["close"],
        "pre_event_adjclose": pairs[e - 1][0]["adjclose"],
    }


def assess_levelling(stock_bars, bench_bars, session_date, ev, lv):
    """Has price stabilised after a confirmed dip? All three checks must pass:
      1. calm:      mean true range% over last N sessions <= atr_ratio_max x pre-event 14-session mean
      2. no drift:  |abnormal return over last N| <= drift_sigma_max * sigma * sqrt(N)
      3. base held: the post-event low is older than `no_new_low_sessions` sessions
    """
    pairs = align(stock_bars, bench_bars)
    e = _index_of(pairs, session_date)
    if e is None or e < 15:
        return {"levelled": False, "reason": "event session missing or too early in history"}
    mm = market_model(pairs, e, ev)
    N = lv["sessions"]
    after = len(pairs) - (e + ev["window_sessions"])
    needed = max(N, lv["min_sessions_after_event"])
    last = len(pairs) - 1
    pre_adj = pairs[e - 1][0]["adjclose"]
    lows = [(pairs[i][0]["low"] * pairs[i][0]["adjclose"] / pairs[i][0]["close"], i) for i in range(e, len(pairs))]
    trough, trough_i = min(lows, key=lambda pair: (pair[0], -pair[1]))
    base = {
        "sessions_since_event": len(pairs) - e,
        "recovery_vs_pre_event": pairs[last][0]["adjclose"] / pre_adj - 1,
        "trough_vs_pre_event": trough / pre_adj - 1,
        "trough_date": pairs[trough_i][0]["date"],
        "last_date": pairs[last][0]["date"],
        "last_close": pairs[last][0]["close"],
    }
    if after < needed:
        return {**base, "levelled": False, "reason": f"need {needed} sessions after the event window, have {max(0, after)}"}
    pre_atr = statistics.fmean(_tr_pct(pairs, i) for i in range(e - 14, e))
    recent_atr = statistics.fmean(_tr_pct(pairs, i) for i in range(len(pairs) - N, len(pairs)))
    drift = sum(_ar(pairs, i, mm) for i in range(len(pairs) - N, len(pairs)))
    drift_limit = lv["drift_sigma_max"] * mm["sigma"] * math.sqrt(N)
    checks = {
        "calm": recent_atr <= lv["atr_ratio_max"] * pre_atr,
        "no_drift": abs(drift) <= drift_limit,
        "base_held": trough_i < len(pairs) - lv["no_new_low_sessions"],
    }
    return {**base, "levelled": all(checks.values()), "checks": checks,
            "atr_ratio": recent_atr / pre_atr if pre_atr > 0 else None,
            "abnormal_drift": math.expm1(drift), "drift_limit": math.expm1(drift_limit)}
