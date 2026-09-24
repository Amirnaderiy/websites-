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
It renders both language versions (index.html and de/index.html), plus
sitemap.xml and robots.txt. Only the standard library is used.
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
SITE_FILE = ROOT / "data" / "site.json"

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
                "title": pub["title"],
                "venue": pub["venue"],
                "link": pub["url"],
                "auto": True,
            })
    store["last_synced"] = today.isoformat()
    return added


# ---------------------------------------------------------------- rendering

STRINGS = {
    "en": {
        "months": MONTHS,
        "read_more": "Read more", "new_tab": " (opens in new tab)",
        "new_paper": "New paper", "earlier": "Show earlier news",
        "cited_by": "Cited by {n}", "other": "Other", "under_review": "Under review",
        "paper": "Paper", "cite": "Cite", "copy": "Copy BibTeX", "copied": "Copied",
        "stats": ["Publications", "Citations", "h-index", "i10-index"],
        "synced_auto": "Publications are synced automatically from Google Scholar.",
        "synced_on": "Publications synced from Google Scholar on {date}.",
        "date": "{d} {m} {y}",
        "og_locale": "en_US",
        "job": "Doctoral Researcher in Visual Neuroscience and Biomedical AI",
        "og_desc": "PhD researcher in visual neuroscience and biomedical AI. Deep learning, computer vision and medical imaging for glaucoma.",
    },
    "de": {
        "months": "Jan. Feb. März Apr. Mai Juni Juli Aug. Sept. Okt. Nov. Dez.".split(),
        "read_more": "Mehr lesen", "new_tab": " (öffnet in neuem Tab)",
        "new_paper": "Neue Publikation", "earlier": "Ältere News anzeigen",
        "cited_by": "{n}× zitiert", "other": "Sonstige", "under_review": "In Begutachtung",
        "paper": "Artikel", "cite": "Zitieren", "copy": "BibTeX kopieren", "copied": "Kopiert",
        "stats": ["Publikationen", "Zitationen", "h-Index", "i10-Index"],
        "synced_auto": "Publikationen werden automatisch aus Google Scholar übernommen.",
        "synced_on": "Publikationen zuletzt am {date} aus Google Scholar übernommen.",
        "date": "{d}. {m} {y}",
        "og_locale": "de_DE",
        "job": "Doktorand in visueller Neurowissenschaft und biomedizinischer KI",
        "og_desc": "Doktorand in visueller Neurowissenschaft und biomedizinischer KI. Deep Learning, Computer Vision und medizinische Bildgebung für Glaukom.",
    },
}

# (file, language, path of the page below the site root)
PAGES = [("index.html", "en", ""), ("de/index.html", "de", "de/")]


def esc(value):
    return html.escape(str(value), quote=True)


def fmt_date(value, t):
    parts = value.split("-")
    if len(parts) >= 2:
        return f"{t['months'][int(parts[1]) - 1]} {parts[0]}"
    return parts[0]


def news_text(n, lang):
    if lang == "de":
        if n.get("text_de"):
            return n["text_de"]
        if n.get("auto") and n.get("title"):
            venue = f" in {n['venue']}" if n.get("venue") else ""
            return f"Neue Publikation{venue}: „{n['title']}“."
    return n["text"]


def render_news(news, t, lang):
    def item(n):
        text = esc(news_text(n, lang))
        if n.get("link"):
            text += (
                f' <a class="inline-link" href="{esc(n["link"])}" target="_blank" rel="noopener">'
                f'{t["read_more"]}<span class="sr-only">{t["new_tab"]}</span></a>'
            )
        tag = f'<span class="news-tag">{t["new_paper"]}</span>' if n.get("auto") else ""
        return (
            f'<li class="news-item"><time datetime="{esc(n["date"])}">{fmt_date(n["date"], t)}</time>'
            f'<p>{tag}{text}</p></li>'
        )

    out = ['<ol class="news-list">'] + [item(n) for n in news[:NEWS_VISIBLE]] + ["</ol>"]
    if len(news) > NEWS_VISIBLE:
        out += [f'<details class="more"><summary>{t["earlier"]}</summary><ol class="news-list">']
        out += [item(n) for n in news[NEWS_VISIBLE:]] + ["</ol></details>"]
    return "\n".join(out)


VENUE_SHORT = {
    "scientific reports": "Sci. Rep.",
    "journal of biomedical physics and engineering": "J. Biomed. Phys. Eng.",
    "tehran university medical journal": "Tehran Univ. Med. J.",
    "archives of computational methods in engineering": "Arch. Comput. Methods Eng.",
}


def venue_short(venue):
    v = venue.lower()
    return next((abbr for name, abbr in VENUE_SHORT.items() if v.startswith(name)), "")


def bibtex(p):
    """Best-effort BibTeX from Scholar-style data ("AR Naderi Yaghouti, H Zamanian, ...")."""
    names = []
    for a in [x.strip() for x in p.get("authors", "").split(",") if x.strip()]:
        if a in ("...", "…"):
            names.append("others")
            continue
        initials, _, last = a.partition(" ")
        if last and initials.isupper() and len(initials) <= 3:
            names.append(f"{last}, {' '.join(c + '.' for c in initials)}")
        else:
            names.append(a)
    first = re.sub(r"[^a-z]", "", (names[0].split(",")[0] if names else "paper").lower())
    word = re.sub(r"[^a-z]", "", p["title"].split()[0].lower())
    journal = re.split(r"\s+\d", p.get("venue", ""))[0].strip()
    fields = [("title", "{" + p["title"] + "}"), ("author", " and ".join(names)), ("journal", journal)]
    m = re.search(r"\s(\d+)(?:\s*\((\d+)\))?,\s*([\d–-]+)", p.get("venue", ""))
    if m:
        fields.append(("volume", m.group(1)))
        if m.group(2):
            fields.append(("number", m.group(2)))
        fields.append(("pages", m.group(3).replace("–", "--").replace("-", "--").replace("----", "--")))
    fields.append(("year", str(p.get("year") or "")))
    if p.get("doi"):
        fields.append(("doi", p["doi"]))
    body = ",\n".join(f"  {k} = {{{v}}}" if not v.startswith("{") else f"  {k} = {{{v}}}" for k, v in fields if v)
    return f"@article{{{first}{p.get('year') or ''}{word},\n{body}\n}}"


def render_pub(p, t):
    authors = SELF_NAME.sub(lambda m: f"<strong>{m.group(0)}</strong>", esc(p.get("authors", "")))
    cites = p.get("citations")
    short = venue_short(p.get("venue", ""))
    meta = f'<span class="venue-tag">{esc(short)}</span>' if short else ""
    meta += f'<em>{esc(p.get("venue", ""))}</em>'
    if cites:
        meta += f'<span class="badge">{t["cited_by"].format(n=cites)}</span>'
    links = []
    if p.get("url"):
        links.append(f'<a class="pub-link" href="{esc(p["url"])}" target="_blank" rel="noopener">{t["paper"]}</a>')
    if p.get("doi"):
        links.append(f'<a class="pub-link" href="https://doi.org/{esc(p["doi"])}" target="_blank" rel="noopener">DOI</a>')
    if p.get("scholar_url"):
        links.append(f'<a class="pub-link" href="{esc(p["scholar_url"])}" target="_blank" rel="noopener">Scholar</a>')
    cite = ""
    if p.get("status") != "under_review":
        cite = (
            f'<details class="cite"><summary class="pub-link">{t["cite"]}</summary>'
            f'<div class="cite-box"><pre><code>{esc(bibtex(p))}</code></pre>'
            f'<button type="button" class="copy-btn" data-copied="{t["copied"]}">{t["copy"]}</button></div></details>'
        )
    title_html = esc(p["title"])
    if p.get("url"):
        title_html = f'<a class="pub-title" href="{esc(p["url"])}" target="_blank" rel="noopener" lang="en">{title_html}</a>'
    else:
        title_html = f'<span class="pub-title" lang="en">{title_html}</span>'
    return (
        '<li class="pub">' + title_html
        + (f'<p class="pub-authors">{authors}</p>' if authors else "")
        + f'<p class="pub-venue">{meta}</p>'
        + (f'<div class="pub-links">{"".join(links)}{cite}</div>' if links or cite else "")
        + "</li>"
    )


def render_publications(pubs, t):
    pending = [p for p in pubs if p.get("status")]
    published = sorted((p for p in pubs if not p.get("status")), key=lambda p: (p.get("year") or 0), reverse=True)
    groups = []
    if pending:
        groups.append((t["under_review"], pending))
    for p in published:
        label = p.get("year") or t["other"]
        if not groups or groups[-1][0] != label:
            groups.append((label, []))
        groups[-1][1].append(p)
    out = []
    for label, items in groups:
        out.append(f'<h3 class="pub-year">{esc(label)}</h3><ol class="pub-list">')
        out += [render_pub(p, t) for p in items]
        out.append("</ol>")
    return "\n".join(out)


def render_stats(store, t):
    m = store.get("metrics")
    values = [sum(1 for p in store["publications"] if not p.get("status"))]
    if m:
        values += [m["citations"], m["h_index"], m["i10_index"]]
    return "\n".join(
        f'<div class="stat"><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>' for k, v in zip(t["stats"], values)
    )


def render_updated(store, t):
    if not store.get("last_synced"):
        return t["synced_auto"]
    d = dt.date.fromisoformat(store["last_synced"])
    return t["synced_on"].format(date=t["date"].format(d=d.day, m=t["months"][d.month - 1], y=d.year))


def render_head(site_url, lang, path, t, store):
    url = site_url + path
    image = site_url + "assets/img/profile.jpg"
    person = {
        "@context": "https://schema.org",
        "@type": "ProfilePage",
        "url": url,
        "inLanguage": lang,
        "mainEntity": {
            "@type": "Person",
            "@id": site_url + "#person",
            "name": "Amir Reza Naderi Yaghouti",
            "alternateName": ["Amir Naderi", "Amir Reza Naderi", "A. R. Naderi Yaghouti", "Amirreza Naderi"],
            "givenName": "Amir Reza",
            "familyName": "Naderi Yaghouti",
            "url": site_url,
            "image": image,
            "email": "mailto:naderi.bme@gmail.com",
            "jobTitle": t["job"],
            "affiliation": [
                {"@type": "CollegeOrUniversity", "name": "Otto-von-Guericke University Magdeburg", "url": "https://www.ovgu.de/"},
                {"@type": "CollegeOrUniversity", "name": "University of Groningen", "url": "https://www.rug.nl/"},
            ],
            "alumniOf": {"@type": "CollegeOrUniversity", "name": "Islamic Azad University, Science and Research Branch"},
            "knowsAbout": ["Deep learning", "Computer vision", "Medical image analysis", "Glaucoma",
                           "Visual neuroscience", "Biomedical engineering", "Explainable AI"],
            "sameAs": [
                "https://www.linkedin.com/in/amirnaderiy",
                "https://scholar.google.com/citations?user=WmUoRTsAAAAJ",
                "https://www.researchgate.net/profile/Amir-Reza-Naderi-Yaghouti",
                "https://github.com/Amirnaderiy",
                "https://www.kaggle.com/amirnaderiy",
                "https://orcid.org/0000-0002-9269-8084",
            ],
            "identifier": {"@type": "PropertyValue", "propertyID": "ORCID", "value": "0000-0002-9269-8084"},
            "award": "Marie Skłodowska-Curie Doctoral Fellowship (Horizon Europe grant agreement No 101072435)",
        },
    }
    works = [
        {"@type": "ScholarlyArticle", "headline": p["title"], "datePublished": str(p.get("year") or ""),
         "url": p.get("url") or "", "author": {"@id": site_url + "#person"}}
        for p in store["publications"] if not p.get("status")
    ]
    person["mainEntity"]["subjectOf"] = works
    title = "Amir Reza Naderi Yaghouti (Amir Naderi)"
    lines = [
        f'  <link rel="canonical" href="{esc(url)}">',
        f'  <link rel="alternate" hreflang="en" href="{esc(site_url)}">',
        f'  <link rel="alternate" hreflang="de" href="{esc(site_url)}de/">',
        f'  <link rel="alternate" hreflang="x-default" href="{esc(site_url)}">',
        '  <meta property="og:type" content="profile">',
        '  <meta property="og:site_name" content="Amir Reza Naderi Yaghouti">',
        f'  <meta property="og:title" content="{esc(title)}">',
        f'  <meta property="og:description" content="{esc(t["og_desc"])}">',
        f'  <meta property="og:url" content="{esc(url)}">',
        f'  <meta property="og:image" content="{esc(image)}">',
        '  <meta property="og:image:alt" content="Portrait of Amir Reza Naderi Yaghouti">',
        f'  <meta property="og:locale" content="{t["og_locale"]}">',
        f'  <meta property="og:locale:alternate" content="{"de_DE" if lang == "en" else "en_US"}">',
        '  <meta property="profile:first_name" content="Amir Reza">',
        '  <meta property="profile:last_name" content="Naderi Yaghouti">',
        '  <meta name="twitter:card" content="summary_large_image">',
        f'  <meta name="twitter:title" content="{esc(title)}">',
        f'  <meta name="twitter:description" content="{esc(t["og_desc"])}">',
        f'  <meta name="twitter:image" content="{esc(image)}">',
        '  <script type="application/ld+json">',
        json.dumps(person, indent=2, ensure_ascii=False).replace("</", "<\\/"),
        "  </script>",
    ]
    return "\n".join(lines)


def write_sitemap_and_robots(site_url):
    today = dt.date.today().isoformat()
    alts = (
        f'    <xhtml:link rel="alternate" hreflang="en" href="{site_url}"/>\n'
        f'    <xhtml:link rel="alternate" hreflang="de" href="{site_url}de/"/>\n'
        f'    <xhtml:link rel="alternate" hreflang="x-default" href="{site_url}"/>\n'
    )
    urls = "".join(
        f"  <url>\n    <loc>{site_url}{path}</loc>\n    <lastmod>{today}</lastmod>\n{alts}  </url>\n"
        for _, _, path in PAGES
    )
    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n' + urls + "</urlset>\n"
    )
    old = (ROOT / "sitemap.xml").read_text(encoding="utf-8") if (ROOT / "sitemap.xml").exists() else ""
    # Only touch lastmod when something other than the date changed.
    if re.sub(r"<lastmod>.*?</lastmod>", "", old) != re.sub(r"<lastmod>.*?</lastmod>", "", sitemap):
        (ROOT / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    # Crawlers only read robots.txt at the domain root, so rules carry the site's path prefix.
    base = urllib.parse.urlparse(site_url).path
    (ROOT / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nDisallow: {base}.claude/\nDisallow: {base}scripts/\n\n"
        f"Sitemap: {site_url}sitemap.xml\n", encoding="utf-8")


def replace_block(page, name, content, file):
    pattern = re.compile(rf"(<!-- AUTO:{name} -->)(.*?)(<!-- /AUTO:{name} -->)", re.S)
    if not pattern.search(page):
        raise SystemExit(f"Marker AUTO:{name} not found in {file}")
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

    site_url = json.loads(SITE_FILE.read_text(encoding="utf-8"))["url"].rstrip("/") + "/"
    for file, lang, path in PAGES:
        t = STRINGS[lang]
        target = ROOT / file
        page = target.read_text(encoding="utf-8")
        page = replace_block(page, "HEAD", render_head(site_url, lang, path, t, store), file)
        page = replace_block(page, "STATS", render_stats(store, t), file)
        page = replace_block(page, "NEWS", render_news(news, t, lang), file)
        page = replace_block(page, "PUBLICATIONS", render_publications(store["publications"], t), file)
        page = replace_block(page, "UPDATED", render_updated(store, t), file)
        target.write_text(page, encoding="utf-8")
    write_sitemap_and_robots(site_url)

if __name__ == "__main__":
    main()
