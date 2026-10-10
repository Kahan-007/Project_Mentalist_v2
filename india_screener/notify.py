"""Alerts: always logged to data/logs/alerts.log; optionally Windows toast, Telegram, ntfy and/or Discord (all free).
Only stock / news alerts go through here; program problems stay in the in-app notification centre."""
import os
import subprocess
import sys

from . import config, net

_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$n = $t.GetElementsByTagName('text')
$n.Item(0).AppendChild($t.CreateTextNode($env:SCREENER_TOAST_TITLE)) > $null
$n.Item(1).AppendChild($t.CreateTextNode($env:SCREENER_TOAST_BODY)) > $null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""


def _toast(title, body):
    env = dict(os.environ, SCREENER_TOAST_TITLE=title[:120], SCREENER_TOAST_BODY=body[:240])
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS],
                   env=env, check=True, capture_output=True, timeout=30)


def _telegram(token, chat_id, text):
    net.post_json(f"https://api.telegram.org/bot{token}/sendMessage",
                  {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True})


def _ntfy(server, topic, title, body):
    """ntfy JSON publish (JSON, not headers, so non-ASCII titles like the rupee sign survive)."""
    net.post_json(server.rstrip("/") + "/", {"topic": topic, "title": title[:200], "message": body[:3900],
                                             "priority": 4, "tags": ["chart_with_downwards_trend"]})


def _discord(webhook, title, body):
    net.post_json(webhook, {"content": f"**{title[:200]}**\n{body}"[:1990], "allowed_mentions": {"parse": []}})


def send(title, body, cfg=None):
    cfg = cfg or config.load_config()
    config.ensure_dirs()
    stamp = config.now_ist().strftime("%Y-%m-%d %H:%M")
    with open(config.LOGS / "alerts.log", "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {title}\n{body}\n\n")
    print(f"ALERT: {title}\n{body}\n")
    problems = []
    n = cfg["notify"]
    if n.get("windows_toast") and sys.platform == "win32":
        try:
            _toast(title, body)
        except Exception as exc:
            problems.append(f"toast failed: {exc}")
    if n.get("telegram_bot_token") and n.get("telegram_chat_id"):
        try:
            _telegram(n["telegram_bot_token"], n["telegram_chat_id"], f"{title}\n\n{body}")
        except Exception as exc:
            problems.append(f"telegram failed: {exc}")
    if n.get("ntfy_topic"):
        try:
            _ntfy(n.get("ntfy_server") or "https://ntfy.sh", n["ntfy_topic"], title, body)
        except Exception as exc:
            problems.append(f"ntfy failed: {exc}")
    if n.get("discord_webhook"):
        try:
            _discord(n["discord_webhook"], title, body)
        except Exception as exc:
            problems.append(f"discord failed: {exc}")
    for p in problems:
        with open(config.LOGS / "alerts.log", "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] NOTIFY PROBLEM: {p}\n")
    return problems
