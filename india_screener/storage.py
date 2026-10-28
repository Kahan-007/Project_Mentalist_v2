"""Atomic JSON (optionally gzipped) files."""
import gzip
import json
import os
import tempfile
import time
from pathlib import Path


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(tmp, "wt", encoding="utf-8") as f:
            json.dump(obj, f, separators=(",", ":"), allow_nan=False)
        # Windows virus scanners can briefly hold a destination file open.
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(.02 * (attempt + 1))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path):
    path = Path(path)
    try:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, EOFError):
        return None
