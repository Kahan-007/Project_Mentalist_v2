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
    from .telegram import call
    result = call(token, "sendMessage", {"chat_id": chat_id, "text": text[:4000], "link_preview_options": {"is_disabled": True}})
    if not isinstance(result, dict) or not result.get('message_id'):
        from .telegram import TelegramError
        raise TelegramError('missing delivery confirmation')
    return result


def _ntfy(server, topic, title, body):
    """ntfy JSON publish (JSON, not headers, so non-ASCII titles like the rupee sign survive)."""
    net.post_json(server.rstrip("/") + "/", {"topic": topic, "title": title[:200], "message": body[:3900],
                                             "priority": 4, "tags": ["chart_with_downwards_trend"]})


def _discord(webhook, title, body):
    net.post_json(webhook, {"content": f"**{title[:200]}**\n{body}"[:1990], "allowed_mentions": {"parse": []}})


def deliver(channel, destination, title, body, cfg):
    n = cfg["notify"]
    if channel == "log":
        config.ensure_dirs()
        with open(config.LOGS / "alerts.log", "a", encoding="utf-8") as f:
            f.write(f"[{config.now_ist().isoformat()}] {title}\n{body}\n\n")
    elif channel == "telegram":
        if str(n.get("telegram_chat_id")) != destination:
            raise ValueError("Telegram recipient changed; queued delivery requires review")
        _telegram(n["telegram_bot_token"], destination, f"{title}\n\n{body}")
    elif channel == "toast":
        _toast(title, body)
    elif channel == "ntfy":
        import json
        server, topic = json.loads(destination)
        _ntfy(server, topic, title, body)
    elif channel == "discord":
        import hashlib
        if hashlib.sha256(n.get("discord_webhook", "").encode()).hexdigest() != destination:
            raise ValueError("Discord destination changed")
        _discord(n["discord_webhook"], title, body)
    else:
        raise ValueError("Unknown delivery channel")


def send(title, body, cfg=None, key=None):
    from . import outbox
    outbox.enqueue(title, body, key, cfg)
    return outbox.flush(cfg)
