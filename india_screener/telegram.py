"""Private Telegram bot pairing and read-only commands. Tokens never enter logs or API responses."""
import json
import os
import re
import secrets
import threading
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from . import config, storage, db as dbm, outbox

LOCK = threading.RLock()


class TelegramError(Exception):
    def __init__(self, code='request failed', retry_after=0):
        super().__init__(f'Telegram {code}')
        self.retry_after = retry_after


def call(token, method, payload=None):
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]{20,}', token or ''):
        raise TelegramError('token format invalid')
    req = Request(f'https://api.telegram.org/bot{token}/{method}',
                  data=json.dumps(payload or {}).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urlopen(req, timeout=35) as response:
            data = json.loads(response.read(2_000_000))
    except HTTPError as exc:
        code = exc.code
        try:
            data = json.loads(exc.read(100_000))
            retry = float(data.get('parameters', {}).get('retry_after', 0))
        except Exception:
            retry = 0
        finally:
            exc.close()
        raise TelegramError(f'HTTP {code}', retry) from None
    except Exception:
        raise TelegramError('connection or response error') from None
    if not data.get('ok'):
        raise TelegramError(f"API error {data.get('error_code', 'unknown')}", data.get('parameters', {}).get('retry_after', 0))
    return data.get('result')


def _path():
    return config.ROOT / '.local' / 'telegram.json'


def load():
    return storage.read_json(_path()) or {}


def save(state):
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    storage.write_json(path, state)
    os.chmod(path, 0o600)


def begin(token):
    token = token.strip()
    me = call(token, 'getMe')
    if call(token, 'getWebhookInfo').get('url'):
        raise ValueError('This bot already uses a webhook. Use a dedicated screener bot.')
    with LOCK:
        state = {'token': token, 'username': me['username'], 'nonce': secrets.token_urlsafe(24),
                 'expires': time.time()+600, 'offset': 0}
        save(state)
    return {'url': f"https://t.me/{state['username']}?start={state['nonce']}", 'expires_in': 600}


def status():
    s = load()
    return {'linked': bool(s.get('chat_id')), 'username': s.get('username'),
            'pairing': bool(s.get('nonce') and s.get('expires', 0) > time.time()),
            'group_linked': bool(s.get('notify_chat_id') and s.get('notify_chat_id') != s.get('chat_id')),
            'group_title': s.get('group_title')}


def disconnect():
    with LOCK:
        save({'disabled': True})
        with dbm.connect() as db:
            db.execute("UPDATE alert_delivery SET status='cancelled' WHERE channel='telegram' AND status='pending'")


def command(text):
    from . import fundamentals, recommendations, portfolio
    parts = text.strip().split(maxsplit=1)
    action = parts[0].split('@')[0].lower() if parts else ''
    sym = parts[1].strip().upper() if len(parts) > 1 else ''
    if action in ('/start', '/help'):
        return 'Mentalist linked. Research commands: /status, /portfolio, /risk, /quality, /stock SYMBOL, /news SYMBOL. Rate a headline: /feedback ID useful (or noise, incorrect, duplicate). No orders are placed.'
    if action == '/feedback':
        from .quality import feedback
        try:
            aid, rating = sym.lower().split()
            feedback(int(aid),rating)
            return 'Feedback saved. It informs review; it does not automatically retune investment rules.'
        except (ValueError, KeyError):
            return 'Use /feedback ID useful (or noise, incorrect, duplicate), with the ID shown in a headline alert.'
    if action == '/quality':
        from .quality import report
        q = report()
        latency = q['queue_to_delivery']
        return (f"Last {q['days']} days: {q['counts'].get('queued',0)} measured headline alerts.\n"
                f"Delivered latency samples: {latency['samples']}; median: {latency['median_seconds']} seconds.\n"
                f"Pending Telegram deliveries: {q['pending_telegram']}.\nFeedback: {q['feedback']}.\n"
                'Measured delay is not investment accuracy. Details: local dashboard → Monitor & risk.')
    if action == '/risk':
        from .risk import report
        r = report()
        return (f"Recorded equities with prices: ₹{r['known_value']:,.2f}\n"+
                '\n'.join(f"{p['symbol']}: {p['weight']:.1%}"+(" — above your limit" if p['over_limit'] else "") if p['weight'] is not None else f"{p['symbol']}: price unavailable" for p in r['holdings'])+
                '\n'+('; '.join(r['issues']) or 'Saved prices pass freshness checks.')+'\n'+r['note'])
    if action == '/status':
        from . import monitor
        state = monitor.status()
        return ('Monitor: '+('running' if state['running'] else 'stopped/stale')+'\n' +
                '\n'.join(f"{label}: last check {(state.get(key) or {}).get('checked_at', 'not yet checked')}"
                          for key,label in [('news','Google News'),('nse','NSE announcements'),('signals','Model status')]) +
                '\nSource health: '+', '.join(x['source']+' '+x['state'] for x in state['sources'])+
                f"\nPending deliveries: {state['delivery'].get('pending', 0)}.\n"
                'Quotes and price-drop confirmations use daily data. No orders are placed.')
    if action == '/portfolio':
        holdings = portfolio.holdings()
        return '\n'.join(f"{s}: {p['quantity']} shares; average cost ₹{p['avg_cost']:.2f}" for s,p in holdings.items()) or 'No holdings recorded.'
    if action in ('/stock', '/news'):
        from .universe import SYMBOL_RE
        if not SYMBOL_RE.fullmatch(sym):
            return 'Use /stock SYMBOL or /news SYMBOL, for example /stock INFY.'
        if action == '/news':
            heads = recommendations._recent_headlines(sym)
            return '\n\n'.join(f"{h['published_utc']} — {h['title']}\n{h['url']}" for h in heads) or 'No stored news for this symbol.'
        rec = fundamentals.load(sym)
        if not rec:
            return 'Financials not imported; review required.'
        analysis, err = recommendations.analyse(sym)
        if err:
            return 'Analysis unavailable; review the local dashboard.'
        from . import shortlist
        held = sym in portfolio.holdings()
        listed = sym in {x['symbol'] for x in shortlist.load().get('stocks', [])}
        signal, why = recommendations.assess(held, listed, analysis)
        meta = rec.get('meta', {})
        return (f"{sym} — {signal or 'research only'}\nPrice: ₹{meta.get('price')} as of {meta.get('price_date')}\n" +
                '\n'.join(f"{k.title()}: {analysis[k]['score']}" for k in ('buffett','munger','lynch')) + f'\n{why}\nModel output; no trade executed.')
    return 'Use /help for commands. Forwarded stories are not automatically treated as verified company announcements.'


def poll_once():
    s = load()
    if not s.get('token'):
        return
    updates = call(s['token'], 'getUpdates', {'offset': s.get('offset', 0), 'timeout': 20, 'allowed_updates': ['message']})
    for update in updates:
        with LOCK:
            current = load()
            if current.get('token') != s['token']:
                return
            msg = update.get('message') or {}
            chat, user = msg.get('chat', {}), msg.get('from', {})
            text = msg.get('text', '')
            private = chat.get('type') == 'private' and bool(user.get('id')) and not user.get('is_bot')
            pairing = (private and current.get('nonce') and current.get('expires', 0) > time.time()
                       and secrets.compare_digest(text.encode('utf-8'), ('/start '+current['nonce']).encode('utf-8')))
            if pairing:
                current.update(chat_id=str(chat['id']), user_id=str(user['id']))
                current.pop('nonce', None)
                save(current)
            allowed = (private and str(chat.get('id')) == current.get('chat_id') and str(user.get('id')) == current.get('user_id'))
            parts = text.strip().split(maxsplit=1)
            action = parts[0].split('@')[0].lower() if parts else ''
            owner = bool(user.get('id')) and not user.get('is_bot') and str(user['id']) == current.get('user_id')
            group = chat.get('type') in ('group', 'supergroup')
            if owner and group and action == '/setgroup':
                current['notify_chat_id'] = str(chat['id'])
                current['group_title'] = str(chat.get('title') or 'Telegram group')
                save(current)
                reply = f"Group alerts enabled for {current['group_title']}. Commands and private information still reply only in your DM."
                reply_cfg = {'notify': {'telegram_bot_token':current['token'], 'telegram_chat_id':str(chat['id'])}}
                outbox.enqueue('Mentalist', reply, key=f"telegram:{s['token'].split(':')[0]}:{update['update_id']}",
                               cfg=reply_cfg)
            elif allowed and action == '/privatealerts':
                current.pop('notify_chat_id', None)
                current.pop('group_title', None)
                save(current)
                reply_cfg = {'notify': {'telegram_bot_token':current['token'], 'telegram_chat_id':current['chat_id']}}
                outbox.enqueue('Mentalist', 'Telegram alerts are back in your private chat.',
                               key=f"telegram:{s['token'].split(':')[0]}:{update['update_id']}", cfg=reply_cfg)
            elif allowed:
                try:
                    reply = command('/start' if pairing else text)
                except Exception:
                    reply = 'This command could not be completed. Please check the local dashboard and try again.'
                reply_cfg = {'notify': {'telegram_bot_token':current['token'], 'telegram_chat_id':current['chat_id']}}
                outbox.enqueue('Mentalist', reply, key=f"telegram:{s['token'].split(':')[0]}:{update['update_id']}", cfg=reply_cfg)
            current['offset'] = update['update_id']+1
            save(current)
