"""Scoring-settings versions, named presets, undo / redo and reset.

Every save (and every preset load or reset) is appended to a version history, so any earlier set of
settings can be returned to with Undo/Redo or picked from the list. Named presets are separate,
permanent snapshots you save on purpose ("Strict value", "Growth-friendly", ...).

The 10-year G-Sec yield (risk_free) is deliberately left out: it is live market data refreshed
automatically every day, so undoing, resetting or loading a preset never rolls it back to an old yield.
"""
from . import config, scoring, storage

KEYS = ("equity_premium", "terminal_growth", "pass_score", "min_coverage", "large_cap_min_cr", "mid_cap_min_cr",
        "price_discipline")
LIMIT = 100


def _path():
    return config.DATA / "scoring_presets.json"      # looked up each time, so tests' temporary folders are respected


def _load():
    d = storage.read_json(_path()) or {}
    d.setdefault("presets", {})
    d.setdefault("history", [])
    d.setdefault("pos", len(d["history"]) - 1)
    return d


def current():
    st = scoring.settings()
    return {k: st[k] for k in KEYS}


def _apply(values):
    user = storage.read_json(config.CONFIG_PATH) or {}
    sc = user.setdefault("scoring", {})
    for k in KEYS:
        if k in values:
            sc[k] = float(values[k])
    storage.write_json(config.CONFIG_PATH, user)


def _push(d, label):
    entry = {"at": config.now_ist().isoformat(timespec="seconds"), "label": label, "settings": current()}
    d["history"] = d["history"][: d["pos"] + 1] + [entry]      # a new change discards the redo branch
    d["history"] = d["history"][-LIMIT:]
    d["pos"] = len(d["history"]) - 1
    storage.write_json(_path(), d)


def ensure_baseline():
    """Record the settings in force before the first change, so the first save can be undone."""
    d = _load()
    if not d["history"]:
        _push(d, "starting settings")


def record(label="saved"):
    """Call after settings were changed by a save: adds a version."""
    d = _load()
    last = d["history"][d["pos"]]["settings"] if d["history"] and d["pos"] >= 0 else None
    if last != current():
        _push(d, label)


def undo():
    return _step(-1)


def redo():
    return _step(1)


def _step(delta):
    d = _load()
    new = d["pos"] + delta
    if not 0 <= new < len(d["history"]):
        raise ValueError("nothing to undo" if delta < 0 else "nothing to redo")
    _apply(d["history"][new]["settings"])
    d["pos"] = new
    storage.write_json(_path(), d)
    return state()


def reset():
    ensure_baseline()
    _apply({k: scoring.DEFAULT_SETTINGS[k] for k in KEYS})
    record("reset to defaults")
    return state()


def save_preset(name):
    name = (name or "").strip()
    if not name or len(name) > 60:
        raise ValueError("give the preset a name (up to 60 characters)")
    d = _load()
    d["presets"][name] = {"settings": current(), "saved_at": config.now_ist().isoformat(timespec="seconds")}
    storage.write_json(_path(), d)
    return state()


def load_preset(name):
    d = _load()
    if name not in d["presets"]:
        raise ValueError(f"no preset called {name!r}")
    ensure_baseline()
    _apply(d["presets"][name]["settings"])
    record(f"loaded preset '{name}'")
    return state()


def load_version(index):
    d = _load()
    if not 0 <= index < len(d["history"]):
        raise ValueError("no such version")
    _apply(d["history"][index]["settings"])
    record(f"restored version from {d['history'][index]['at'][:16].replace('T', ' ')}")
    return state()


def delete_preset(name):
    d = _load()
    d["presets"].pop(name, None)
    storage.write_json(_path(), d)
    return state()


def state():
    d = _load()
    return {"presets": [{"name": n, **p} for n, p in sorted(d["presets"].items())],
            "history": d["history"][::-1], "pos": d["pos"], "count": len(d["history"]),
            "can_undo": d["pos"] > 0, "can_redo": d["pos"] < len(d["history"]) - 1,
            "defaults": {k: scoring.DEFAULT_SETTINGS[k] for k in KEYS}, "current": current()}
