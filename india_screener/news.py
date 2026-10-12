"""Company headlines from Google News RSS (free, no key) with relevance filtering and category tags.

Tags are keyword-based labels, not sentiment. A headline is kept only if its title
mentions one of the company's search terms, which removes most unrelated matches.
"""
import datetime as dt
import email.utils
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

from . import net, config
from .universe import short_name

RSS = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
NSE_RSS = "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"


def parse_nse(xml_bytes, watched):
    """Match the issuer's exact normalized name, never incidental company mentions in a disclosure."""
    from urllib.parse import urlsplit
    import html
    normalize = lambda s: re.sub(r'[^a-z0-9]', '', short_name(s).lower())
    names = {}
    for item in watched:
        if item.get('exchange', 'NSE') != 'NSE':
            continue
        for name in [item.get('name', ''), *item.get('aliases', [])]:
            if name:
                names.setdefault(normalize(name), set()).add(item['symbol'])
    result = {item['symbol']: [] for item in watched}
    for entry in ET.fromstring(xml_bytes).findall('./channel/item'):
        issuer = (entry.findtext('title') or '').strip()
        symbols = names.get(normalize(issuer), set())
        if len(symbols) != 1:
            continue
        link = (entry.findtext('link') or '').strip()
        parts = urlsplit(link)
        if parts.scheme != 'https' or parts.hostname not in ('nsearchives.nseindia.com', 'www.nseindia.com', 'archives.nseindia.com'):
            continue
        try:
            published = dt.datetime.strptime(entry.findtext('pubDate') or '', '%d-%b-%Y %H:%M:%S').replace(tzinfo=config.IST)
        except ValueError:
            continue
        detail = html.unescape(re.sub(r'<[^>]*>', '', entry.findtext('description') or '')).strip()
        title = f'{issuer}: {detail}'
        result[next(iter(symbols))].append({'title': title, 'url': link, 'source': 'NSE corporate announcement',
            'published_utc': published.astimezone(dt.timezone.utc).isoformat(), 'categories': categorize(detail)})
    return result

CATEGORIES = {
    "merger/acquisition": ["merger", "mergers", "merge", "merges", "acquisition", "acquisitions", "acquire", "acquires", "acquired",
                           "takeover", "amalgamation", "demerger", "open offer", "buyout"],
    "deal/order": ["deal", "deals", "order win", "bags order", "wins order", "bags orders", "contract", "contracts", "agreement",
                   "mou", "partnership", "joint venture", "stake sale"],
    "legal/regulatory": ["lawsuit", "sued", "sues", "court", "nclt", "nclat", "sebi", "penalty", "fined", "fines", "fine of", "probe",
                         "raid", "raids", "ed", "cbi", "investigation", "show cause", "show-cause", "ban", "banned",
                         "litigation", "arbitration", "tax demand", "gst notice", "insolvency"],
    "results": ["q1", "q2", "q3", "q4", "quarterly", "results", "net profit", "profit", "revenue", "earnings", "ebitda", "margin", "margins"],
    "governance/management": ["ceo", "cfo", "md", "resigns", "resignation", "steps down", "appoints", "appointed", "auditor",
                              "fraud", "whistleblower"],
    "promoter/pledge": ["pledge", "pledged", "promoter", "promoters", "block deal", "bulk deal", "stake", "ofs", "qip"],
    "rating/broker": ["downgrade", "downgrades", "downgraded", "upgrade", "upgrades", "upgraded", "target price", "rating",
                      "moody's", "crisil", "icra", "fitch"],
}


def search_terms(name, symbol, aliases=()):
    terms = []
    sn = short_name(name) if name else ""
    if sn:
        terms.append(sn)
    # A bare ticker is only a useful search term if it's a real word-like token (e.g. ONGC, ITC), not 'LT'.
    if symbol and symbol.isalpha() and len(symbol) >= 3 and symbol.lower() != sn.lower():
        terms.append(symbol)
    for a in aliases:
        if a and a not in terms:
            terms.append(a)
    return terms


def _mentions(title, terms):
    t = title.lower()
    return any(re.search(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])", t) for term in terms)


def _kw_pattern(word):
    word = word.strip().lower()
    # Whole words/phrases only: 'fine' must not match 'financial'; variants are listed explicitly.
    return re.compile(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])")


_PATTERNS = {cat: [_kw_pattern(w) for w in words] for cat, words in CATEGORIES.items()}


def categorize(title):
    t = title.lower()
    return sorted(cat for cat, pats in _PATTERNS.items() if any(p.search(t) for p in pats))


def parse_rss(xml_bytes, terms, now_utc=None, days=7, limit=40):
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    root = ET.fromstring(xml_bytes)
    items, seen = [], set()
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source = (item.findtext("source") or "").strip()
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)]
        key = re.sub(r"\W+", " ", title.lower()).strip()
        if not title or key in seen or not link.startswith("https://"):
            continue
        try:
            published = email.utils.parsedate_to_datetime(item.findtext("pubDate") or "")
            if published.tzinfo is None:
                published = published.replace(tzinfo=dt.timezone.utc)
        except (TypeError, ValueError):
            continue
        age = (now_utc - published).total_seconds()
        if not (-3600 <= age <= days * 86400):
            continue
        if not _mentions(title, terms):
            continue
        seen.add(key)
        items.append({"title": title, "url": link, "source": source,
                      "published_utc": published.astimezone(dt.timezone.utc).isoformat(),
                      "categories": categorize(title)})
    items.sort(key=lambda x: x["published_utc"], reverse=True)
    return items[:limit]


def fetch_news(symbol, name, aliases=(), days=7):
    terms = search_terms(name, symbol, aliases)
    if not terms:
        return []
    query = "(" + " OR ".join(f'"{t}"' for t in terms) + f") when:{max(1, int(days))}d"
    return parse_rss(net.fetch(RSS.format(q=quote(query))), terms, days=days)
