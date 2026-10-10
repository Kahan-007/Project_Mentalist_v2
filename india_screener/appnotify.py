"""In-app notification centre, separate from Windows pop-ups.

Two kinds, colour-coded in the dashboard:
  stock   (light blue)   - the same stock / news alerts that also go out as pop-ups, with their full detail
  program (light yellow) - about the program itself: a data source failed, a refresh finished, stocks left
                           with stale prices, the daily check crashed. These are never sent as pop-ups.
Program notices with the same `key` are merged per day, so a source that keeps failing doesn't flood the list.
"""
from . import config, db as dbm

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_notifications(
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('stock','program')),
    level TEXT NOT NULL DEFAULT 'info',     -- info | warning | error
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    symbol TEXT,
    dedupe_key TEXT,
    read INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS app_notifications_recent ON app_notifications(id DESC);
"""


def add(kind, title, body="", level="info", symbol=None, key=None, path=None):
    now = config.now_ist().isoformat(timespec="seconds")
    with dbm.connect(path) as db:
        db.executescript(SCHEMA)
        if key:
            row = db.execute("SELECT id FROM app_notifications WHERE dedupe_key=? AND substr(created_at,1,10)=?",
                             (key, now[:10])).fetchone()
            if row:
                db.execute("UPDATE app_notifications SET title=?, body=?, level=?, created_at=?, read=0 WHERE id=?",
                           (title, body, level, now, row["id"]))
                return row["id"]
        return db.execute("INSERT INTO app_notifications(created_at,kind,level,title,body,symbol,dedupe_key) VALUES(?,?,?,?,?,?,?)",
                          (now, kind, level, title, body, symbol, key)).lastrowid


def program(title, body="", level="warning", key=None):
    """Never raises: a failure to record a problem must not become a second problem."""
    try:
        return add("program", title, body, level=level, key=key)
    except Exception:
        return None


def stock(title, body, symbol=None):
    try:
        return add("stock", title, body, symbol=symbol)
    except Exception:
        return None


def check_schedule_health():
    """Warn if the scheduled daily check seems to have stopped: 2+ weekdays since the last daily report
    (evening runs only count once the 18:30 run is due). Does nothing before the first report exists."""
    import datetime as dt
    try:
        reports = sorted(config.REPORTS.glob("*.md")) if config.REPORTS.exists() else []
        if not reports:
            return
        last = dt.date.fromisoformat(reports[-1].stem)
        now = config.now_ist()
        end = now.date() if now.time() >= dt.time(19, 30) else now.date() - dt.timedelta(days=1)
        missed = sum(1 for i in range(1, (end - last).days + 1) if (last + dt.timedelta(days=i)).weekday() < 5)
        if missed >= 2:
            program("The daily check hasn't run recently",
                    f"The last daily report is from {last:%d-%b-%Y}; {missed} weekday runs have been missed.\n\n"
                    "Check that the PC was on and that the 'IndiaScreener Daily' task exists in Windows Task Scheduler "
                    "(double-click 'Set up daily check.bat' to reinstall it). You can also run it now from News alerts.",
                    level="error", key="schedule-health")
    except Exception:
        pass


def items(kind=None, limit=300, path=None):
    with dbm.connect(path) as db:
        db.executescript(SCHEMA)
        q, args = "SELECT * FROM app_notifications", []
        if kind:
            q += " WHERE kind=?"
            args.append(kind)
        rows = [dict(r) for r in db.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit))]
        unread = {k: n for k, n in db.execute("SELECT kind, COUNT(*) FROM app_notifications WHERE read=0 GROUP BY kind")}
    return {"items": rows, "unread": sum(unread.values()), "unread_by_kind": unread}


def mark_read(ids=None, path=None):
    with dbm.connect(path) as db:
        db.executescript(SCHEMA)
        if ids:
            db.execute("UPDATE app_notifications SET read=1 WHERE id IN (%s)" % ",".join("?" * len(ids)), [int(i) for i in ids])
        else:
            db.execute("UPDATE app_notifications SET read=1")


def unread_count(path=None):
    with dbm.connect(path) as db:
        db.executescript(SCHEMA)
        return db.execute("SELECT COUNT(*) FROM app_notifications WHERE read=0").fetchone()[0]
