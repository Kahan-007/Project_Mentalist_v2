"""Polite HTTP: timeouts, size cap, gzip, shared 429 back-off, limited retries."""
import gzip
import json
import random
import re
from urllib.parse import urlsplit
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
MAX_BYTES = 12_000_000
_lock = threading.Lock()
_backoff_until = {}


class FetchError(Exception):
    pass


def _wait_backoff(host):
    with _lock:
        delay = _backoff_until.get(host, 0) - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def _set_backoff(host, seconds):
    global _backoff_until
    with _lock:
        _backoff_until[host] = max(_backoff_until.get(host, 0), time.monotonic() + seconds)


def fetch(url, *, timeout=15, retries=2, headers=None, data=None):
    h = {"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "gzip"}
    h.update(headers or {})
    last = None
    for attempt in range(retries + 1):
        _wait_backoff(urlsplit(url).netloc)
        try:
            with urlopen(Request(url, headers=h, data=data), timeout=timeout) as resp:
                body = resp.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise FetchError("response too large")
                if resp.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                    if len(body) > 4 * MAX_BYTES:
                        raise FetchError("decompressed response too large")
                return body
        except HTTPError as exc:
            if exc.code == 429:
                try:
                    retry = max(30.0, float(exc.headers.get("Retry-After", "60")))
                except (TypeError, ValueError):
                    retry = 60.0
                _set_backoff(urlsplit(url).netloc, retry * (attempt + 1))
                last = exc
                continue
            if exc.code in (400, 401, 403, 404):
                raise FetchError(f"HTTP {exc.code} for {urlsplit(url).hostname}") from None
            last = exc
        except (URLError, TimeoutError, OSError) as exc:
            last = exc
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1) + random.random())
    raise FetchError(f"{type(last).__name__}: request failed for {urlsplit(url).hostname}" if last else "fetch failed")


def post_json(url, payload, timeout=15):
    body = json.dumps(payload).encode()
    return fetch(url, timeout=timeout, retries=1, data=body, headers={"Content-Type": "application/json"})
