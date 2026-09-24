#!/usr/bin/env python3
"""Sync publications from Google Scholar and render them into index.html.

Usage:
    python scripts/update_site.py            # re-render index.html from data/*.json
    python scripts/update_site.py --fetch    # fetch Google Scholar first, then render

Google Scholar has no official API. By default the profile page is fetched
directly. If the environment variable SERPAPI_KEY is set, SerpAPI's
google_scholar_author engine is used instead (more reliable from CI runners).

Any paper that appears on Scholar and was not known before is added to
data/publications.json and announced at the top of data/news.json.
Only the standard library is used.
"""

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBS_FILE = ROOT / "data" / "publications.json"
NEWS_FILE = ROOT / "data" / "news.json"
INDEX_FILE = ROOT / "index.html"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
SELF_NAME = re.compile(
    r"(?:A\.?\s?R\.?\s|Amir\s?Reza\s|A\.?\s)?Naderi(?:[\s-]Yaghouti)?", re.I
)
NEWS_VISIBLE = 6
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


# ---------------------------------------------------------------- fetching


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def _text(fragment):
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def fetch_scholar_direct(user):
    pubs, metrics, start = [], None, 0
    while True:
        url = (
            "https://scholar.google.com/citations?hl=en&view_op=list_works"
            f"&sortby=pubdate&user={user}&cstart={start}&pagesize=100"
        )
        page = _get(url)
        if "gsc_a_tr" not in page and "gsc_prf_in" not in page:
            raise RuntimeError("Scholar returned an unexpected page (probably a CAPTCHA).")
        if metrics is None:
            vals = [_text(v) for v in re.findall(r'<td class="gsc_rsb_std">(.*?)</td>', page)]
            if len(vals) >= 6:
                metrics = {"citations": int(vals[0]), "h_index": int(vals[2]), "i10_index": int(vals[4])}
        rows = re.findall(r'<tr class="gsc_a_tr">(.*?)</tr>', page, re.S)
        for row in rows:
            link = re.search(r'<a href="([^"]+)" class="gsc_a_at">(.*?)</a>', row, re.S)
            if not link:
                continue
            href = html.unescape(link.group(1))
            grays = re.findall(r'<div class="gs_gray">(.*?)</div>', row, re.S)
            venue = re.sub(r'<span class="gs_oph">.*?</span>', "", grays[1], flags=re.S) if len(grays) > 1 else ""
            cites = re.search(r'class="gsc_a_ac[^"]*">(\d*)</a>', row)
            year = re.search(r'class="gsc_a_h[^"]*">(\d{4})</span>', row)
            cid = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("citation_for_view", [""])[0]
            pubs.append({
                "id": f"scholar:{cid}" if cid else None,
                "title": _text(link.group(2)),
                "authors": _text(grays[0]) if grays else "",
                "venue": _text(venue),
                "year": int(year.group(1)) if year else None,
                "citations": int(cites.group(1)) if cites and cites.group(1) else 0,
                "url": "https://scholar.google.com" + href,
            })
        if len(rows) < 100:
            break
        start += 100
    return pubs, metrics


def fetch_scholar_serpapi(user, key):
    pubs, metrics, start = [], None, 0
    while True:
        query = urllib.parse.urlencode({
            "engine": "google_scholar_author", "author_id": user, "hl": "en",
            "sort": "pubdate", "num": 100, "start": start, "api_key": key,
        })
        data = json.loads(_get("https://serpapi.com/search.json?" + query))
        if "error" in data:
            raise RuntimeError("SerpAPI: " + data["error"])
        if metrics is None:
            table = {k: v for row in data.get("cited_by", {}).get("table", []) for k, v in row.items()}
            if table:
                metrics = {
                    "citations": table.get("citations", {}).get("all", 0),
                    "h_index": table.get("h_index", {}).get("all", 0),
                    "i10_index": table.get("i10_index", {}).get("all", 0),
                }
        articles = data.get("articles", [])
        for art in articles:
            year = str(art.get("year") or "")
            pubs.append({
                "id": f"scholar:{art['citation_id']}" if art.get("citation_id") else None,
                "title": art.get("title", ""),
                "authors": art.get("authors", ""),
                "venue": art.get("publication", ""),
                "year": int(year) if year.isdigit() else None,
                "citations": (art.get("cited_by") or {}).get("value") or 0,
                "url": art.get("link", ""),
            })
        if len(articles) < 100:
            break
        start += 100
    return pubs, metrics


def _norm(title):
    return re.sub(r"[^a-z0-9]", "", title.lower())


def merge(store, fetched, news, today):
    """Merge fetched Scholar results into the store; return list of new pubs."""
    known = {_norm(p["title"]): p for p in store["publications"]}
    first_sync = store.get("last_synced") is None
    added = []
    for pub in fetched:
        if not pub["title"]:
            continue
        existing = known.get(_norm(pub["title"]))
        if existing:
            # Keep hand-curated venue/link; refresh what Scholar knows best.
            existing["citations"] = pub["citations"]
            existing["year"] = existing.get("year") or pub["year"]
            existing.setdefault("scholar_url", pub["url"])
            continue
        store["publications"].append(pub)
        known[_norm(pub["title"])] = pub
        added.append(pub)

    # On the very first sync, import silently so old papers don't flood News.
    if not first_sync:
        for pub in reversed(added):
            venue = f" in {pub['venue']}" if pub["venue"] else ""
            news.insert(0, {
                "date": today.strftime("%Y-%m"),
                "text": f"New paper published{venue}: “{pub['title']}”.",
                "link": pub["url"],
                "auto": True,
            })
    store["last_synced"] = today.isoformat()
    return added


# ---------------------------------------------------------------- rendering


def esc(value):
    return html.escape(str(value), quote=True)


def fmt_date(value):
    parts = value.split("-")
    if len(parts) >= 2:
        return f"{MONTHS[int(parts[1]) - 1]} {parts[0]}"
    return parts[0]


def render_news(news):
    def item(n):
        text = esc(n["text"])
        if n.get("link"):
            text += f' <a class="inline-link" href="{esc(n["link"])}" target="_blank" rel="noopener">Read more<span class="sr-only"> (opens in new tab)</span></a>'
        tag = '<span class="news-tag">New paper</span>' if n.get("auto") else ""
        return (
            f'<li class="news-item"><time datetime="{esc(n["date"])}">{fmt_date(n["date"])}</time>'
            f'<p>{tag}{text}</p></li>'
        )

    out = ['<ol class="news-list">'] + [item(n) for n in news[:NEWS_VISIBLE]] + ["</ol>"]
    if len(news) > NEWS_VISIBLE:
        out += ['<details class="more"><summary>Show earlier news</summary><ol class="news-list">']
        out += [item(n) for n in news[NEWS_VISIBLE:]] + ["</ol></details>"]
    return "\n".join(out)


def render_publications(pubs):
    pubs = sorted(pubs, key=lambda p: (p.get("year") or 0), reverse=True)
    out, current = [], object()
    for p in pubs:
        if p.get("year") != current:
            if out:
                out.append("</ol>")
            current = p.get("year")
            out.append(f'<h3 class="pub-year">{esc(current or "Other")}</h3><ol class="pub-list">')
        authors = SELF_NAME.sub(lambda m: f"<strong>{m.group(0)}</strong>", esc(p.get("authors", "")))
        cites = p.get("citations")
        badge = f'<span class="badge">Cited by {cites}</span>' if cites else ""
        out.append(
            '<li class="pub">'
            f'<a class="pub-title" href="{esc(p.get("url") or p.get("scholar_url") or "#")}" target="_blank" rel="noopener">{esc(p["title"])}</a>'
            f'<p class="pub-authors">{authors}</p>'
            f'<p class="pub-venue"><em>{esc(p.get("venue", ""))}</em>{badge}</p>'
            "</li>"
        )
    if out:
        out.append("</ol>")
    return "\n".join(out)


def render_stats(store):
    m = store.get("metrics")
    count = len(store["publications"])
    items = [("Publications", count)]
    if m:
        items += [("Citations", m["citations"]), ("h-index", m["h_index"]), ("i10-index", m["i10_index"])]
    return "\n".join(
        f'<div class="stat"><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>' for k, v in items
    )


def render_updated(store):
    if not store.get("last_synced"):
        return "Publications are synced automatically from Google Scholar."
    d = dt.date.fromisoformat(store["last_synced"])
    return f"Publications synced from Google Scholar on {d.day} {MONTHS[d.month - 1]} {d.year}."


def replace_block(page, name, content):
    pattern = re.compile(rf"(<!-- AUTO:{name} -->)(.*?)(<!-- /AUTO:{name} -->)", re.S)
    if not pattern.search(page):
        raise SystemExit(f"Marker AUTO:{name} not found in index.html")
    return pattern.sub(lambda m: f"{m.group(1)}\n{content}\n{m.group(3)}", page)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fetch", action="store_true", help="fetch Google Scholar before rendering")
    args = parser.parse_args()

    store = json.loads(PUBS_FILE.read_text(encoding="utf-8"))
    news = json.loads(NEWS_FILE.read_text(encoding="utf-8"))

    if args.fetch:
        key = os.environ.get("SERPAPI_KEY")
        try:
            if key:
                fetched, metrics = fetch_scholar_serpapi(store["scholar_user"], key)
            else:
                fetched, metrics = fetch_scholar_direct(store["scholar_user"])
        except Exception as exc:  # keep the current site if Scholar is unreachable
            print(f"warning: could not fetch Google Scholar ({exc}); keeping existing data", file=sys.stderr)
        else:
            if metrics:
                store["metrics"] = metrics
            added = merge(store, fetched, news, dt.date.today())
            print(f"Scholar: {len(fetched)} papers, {len(added)} new")
            PUBS_FILE.write_text(json.dumps(store, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            NEWS_FILE.write_text(json.dumps(news, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    page = INDEX_FILE.read_text(encoding="utf-8")
    page = replace_block(page, "STATS", render_stats(store))
    page = replace_block(page, "NEWS", render_news(news))
    page = replace_block(page, "PUBLICATIONS", render_publications(store["publications"]))
    page = replace_block(page, "UPDATED", render_updated(store))
    INDEX_FILE.write_text(page, encoding="utf-8")


if __name__ == "__main__":
    main()
