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
import shutil
import sys
import unicodedata
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


def fetch_with_retries(user, attempts=3, waits=(45, 120)):
    """Fetch Scholar data, retrying a few times; return None if every attempt fails."""
    import time
    key = os.environ.get("SERPAPI_KEY")
    for i in range(attempts):
        try:
            result = fetch_scholar_serpapi(user, key) if key else fetch_scholar_direct(user)
            if os.environ.get("GITHUB_ACTIONS"):
                print(f"::notice title=Google Scholar::Fetched via {'SerpAPI' if key else 'direct request (no SERPAPI_KEY secret)'}")
            return result
        except Exception as exc:
            print(f"attempt {i + 1}/{attempts} failed: {exc}", file=sys.stderr)
            if i < attempts - 1:
                time.sleep(waits[min(i, len(waits) - 1)])
    msg = "Could not reach Google Scholar (it probably blocked the request); the site keeps its current numbers."
    if os.environ.get("GITHUB_ACTIONS"):
        # Shows as a yellow warning on the run in the Actions tab.
        print(f"::warning title=Google Scholar::{msg} Adding a SERPAPI_KEY secret makes updates reliable.")
    else:
        print("warning: " + msg, file=sys.stderr)
    return None


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
        "lang": "en",
        "months": MONTHS,
        "read_more": "Read more", "new_tab": " (opens in new tab)",
        "new_paper": "New paper", "earlier": "Show earlier news",
        "cited_by": "Cited by {n}", "other": "Other", "under_review": "Under review",
        "paper": "Paper", "cite": "Cite", "copy": "Copy BibTeX", "copied": "Copied", "details": "Details",
        "stats": ["Citations", "h-index", "i10-index", "Papers"],
        "pills": {"talk": "Talk", "poster": "Poster", "paper": "Paper", "milestone": "Milestone"},
        "citations": "{n} citations", "citation": "1 citation", "under_review_chip": "under review",
        "all": "All", "filter_label": "Filter publications by topic", "showing": "Showing {n} of {total}",
        "synced_auto": "Publications and citation counts are updated daily from Google Scholar.",
        "privacy": "Visits are counted with <a href=\"https://www.goatcounter.com/help/privacy\" target=\"_blank\" rel=\"noopener\">GoatCounter</a>, without cookies or personal data.",
        "date": "{d} {m} {y}",
        "og_locale": "en_US",
        "page_title": "Amir Reza Naderi Yaghouti – AI in Medicine Researcher",
        "job": "Researcher in AI in Medicine, Marie Skłodowska-Curie Doctoral Fellow",
        "og_desc": "Machine learning and deep learning for medical data: MRI, retinal and ultrasound images, clinical records and lab biomarkers.",
    },
    "de": {
        "lang": "de",
        "months": "Jan. Feb. März Apr. Mai Juni Juli Aug. Sept. Okt. Nov. Dez.".split(),
        "read_more": "Mehr lesen", "new_tab": " (öffnet in neuem Tab)",
        "new_paper": "Neue Publikation", "earlier": "Ältere News anzeigen",
        "cited_by": "{n}× zitiert", "other": "Sonstige", "under_review": "In Begutachtung",
        "paper": "Artikel", "cite": "Zitieren", "copy": "BibTeX kopieren", "copied": "Kopiert", "details": "Details",
        "stats": ["Zitationen", "h-Index", "i10-Index", "Artikel"],
        "pills": {"talk": "Vortrag", "poster": "Poster", "paper": "Publikation", "milestone": "Meilenstein"},
        "citations": "{n} Zitationen", "citation": "1 Zitation", "under_review_chip": "in Begutachtung",
        "all": "Alle", "filter_label": "Publikationen nach Thema filtern", "showing": "{n} von {total} angezeigt",
        "synced_auto": "Publikationen und Zitationszahlen werden täglich aus Google Scholar aktualisiert.",
        "privacy": "Besuche werden mit <a href=\"https://www.goatcounter.com/help/privacy\" target=\"_blank\" rel=\"noopener\">GoatCounter</a> gezählt, ohne Cookies und ohne personenbezogene Daten.",
        "date": "{d}. {m} {y}",
        "og_locale": "de_DE",
        "page_title": "Amir Reza Naderi Yaghouti – Forscher für KI in der Medizin",
        "job": "Forscher für KI in der Medizin, Marie-Skłodowska-Curie-Doktorand",
        "og_desc": "Maschinelles Lernen und Deep Learning für medizinische Daten: MRT-, Netzhaut- und Ultraschallbilder, klinische Daten und Laborbiomarker.",
    },
}

# (file, language, path of the page below the site root)
PAGES = [("index.html", "en", ""), ("de/index.html", "de", "de/")]


def esc(value):
    return html.escape(str(value), quote=True)


# Image shown when the site is shared (LinkedIn, X, WhatsApp) and in some Google results.
OG_IMAGE = "assets/img/og-image.png"  # 1200 x 630
SHARE_ALT = "Amir Reza Naderi Yaghouti – AI in Medicine Researcher"
PUB_DIR = "publications"
GENERATED = "<!-- generated by scripts/update_site.py; edit data/publications.json instead -->"


def published_pubs(store):
    """Papers that get their own page: everything except work still under review."""
    return [p for p in store["publications"] if not p.get("status") and p.get("title")]


def pub_slug(p):
    """Stable, readable URL part from the title (or the paper's own "slug" field)."""
    if p.get("slug"):
        return p["slug"]
    ascii_title = unicodedata.normalize("NFKD", p["title"]).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", ascii_title.lower())
    out = []
    for w in words:
        if len("-".join(out + [w])) > 72:
            break
        out.append(w)
    return "-".join(out) or "paper"


def pub_path(p):
    return f"{PUB_DIR}/{pub_slug(p)}/"


def doi_url(p):
    return f"https://doi.org/{p['doi']}" if p.get("doi") else ""


def venue_parts(venue):
    """'Scientific Reports 14, 2442' -> ('Scientific Reports', '14', '', '2442')."""
    journal = re.split(r"\s+\d", venue or "")[0].strip()
    m = re.search(r"\s(\d+)(?:\s*\((\d+)\))?,\s*([\d–-]+)", venue or "")
    if not m:
        return journal, "", "", ""
    return journal, m.group(1), m.group(2) or "", m.group(3)


def author_list(p):
    """Scholar style 'AR Naderi Yaghouti, H Zamanian, ...' -> [(display, 'Last, First'), ...]."""
    out = []
    for a in [x.strip() for x in p.get("authors", "").split(",") if x.strip()]:
        if a in ("...", "…"):
            continue
        if SELF_NAME.fullmatch(a):
            out.append((a, "Naderi Yaghouti, Amir Reza", True))
            continue
        initials, _, last = a.partition(" ")
        citation = f"{last}, {initials}" if last and initials.isupper() and len(initials) <= 3 else a
        out.append((a, citation, False))
    return out


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
                f' <a class="more-link" href="{esc(n["link"])}" target="_blank" rel="noopener">'
                f'{t["read_more"]}<span class="sr-only">{t["new_tab"]}</span></a>'
            )
        kind = "paper" if n.get("auto") else n.get("type")
        pill = f'<span class="pill">{t["pills"][kind]}</span>' if kind in t["pills"] else ""
        return (
            f'<li><time datetime="{esc(n["date"])}">{fmt_date(n["date"], t)}</time>'
            f'<p>{pill}{text}</p></li>'
        )

    out = ['<ol class="timeline">'] + [item(n) for n in news[:NEWS_VISIBLE]] + ["</ol>"]
    if len(news) > NEWS_VISIBLE:
        out += [f'<details class="more"><summary>{t["earlier"]}</summary><ol class="timeline">']
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


# Topic tags for the publication filter. Each paper is tagged from its title and venue;
# a paper can override this with a "topics" list in data/publications.json.
# Groups and tags only appear on the page when at least one paper has them.
TOPIC_GROUPS = [
    ("area", {"en": "Area", "de": "Bereich"}, [
        ("liver", "Liver disease", "Lebererkrankungen", r"liver|steatohepatitis|nafld|\bnash\b|hepat"),
        ("ovarian", "Ovarian cancer", "Eierstockkrebs", r"ovarian"),
        ("glaucoma", "Glaucoma", "Glaukom", r"glaucoma"),
    ]),
    ("task", {"en": "Task", "de": "Aufgabe"}, [
        ("classification", "Classification", "Klassifikation", r"classif|detection|discriminat|diagnos|grading"),
        ("segmentation", "Segmentation", "Segmentierung", r"segment"),
        ("review", "Review", "Übersichtsarbeit", r"\breview\b"),
    ]),
    ("method", {"en": "Method", "de": "Methode"}, [
        ("ml", "Machine learning", "Maschinelles Lernen", r"machine learning|random forest|texture|radiomic|svm|xgboost"),
        ("dl", "Deep learning", "Deep Learning", r"deep learning|convolutional|\bcnn|neural network|foundation model"),
        ("fm", "Foundation models", "Foundation Models", r"foundation model"),
    ]),
    ("data", {"en": "Data", "de": "Daten"}, [
        ("ultrasound", "Ultrasound", "Ultraschall", r"ultrasound|b-mode"),
        ("mri", "MRI", "MRT", r"\bmri\b|magnetic resonance"),
        ("fundus", "Retinal images", "Netzhautbilder", r"fundus|retina|\boct\b"),
        ("clinical", "Clinical & lab data", "Klinik- & Labordaten", r"clinical|blood|laborator|biomarker"),
        ("imaging", "Medical imaging", "Medizinische Bildgebung", r"medical imag|imaging|images|ultrasound|b-mode|\bmri\b|fundus|retina"),
    ]),
]
TOPICS = {key: (en, de, rx) for _, _, items in TOPIC_GROUPS for key, en, de, rx in items}


def topics_for(p):
    if p.get("topics"):
        return [k for k in p["topics"] if k in TOPICS]
    text = f'{p.get("title", "")} {p.get("venue", "")}'.lower()
    tags = [k for k, (_, _, rx) in TOPICS.items() if re.search(rx, text)]
    if "review" in tags and "classification" in tags:
        tags.remove("classification")  # a review about detection is not itself a classifier
    return tags


def topic_label(key, t):
    en, de, _ = TOPICS[key]
    return de if t["lang"] == "de" else en


def render_pub(p, t, max_cites):
    tags = topics_for(p)
    authors = SELF_NAME.sub(lambda m: f"<b>{m.group(0)}</b>", esc(p.get("authors", "")))
    review = p.get("status") == "under_review"
    short = venue_short(p.get("venue", "")) or p.get("venue", "")
    chip = esc(short) + (f' · {t["under_review_chip"]}' if review else "")
    top = f'<span class="venue{" review" if review else ""}">{chip}</span>'
    if p.get("year"):
        top += f'<span class="yr">{esc(p["year"])}</span>'
    title = esc(p["title"])
    if p.get("url"):
        title = f'<a href="{esc(p["url"])}" target="_blank" rel="noopener">{title}</a>'
    links = []
    if not review:
        links.append(f'<a class="mini" href="/{pub_path(p)}">{t["details"]}</a>')
    if p.get("url"):
        links.append(f'<a class="mini" href="{esc(p["url"])}" target="_blank" rel="noopener">{t["paper"]}</a>')
    if p.get("doi"):
        links.append(f'<a class="mini" href="https://doi.org/{esc(p["doi"])}" target="_blank" rel="noopener">DOI</a>')
    if p.get("scholar_url"):
        links.append(f'<a class="mini" href="{esc(p["scholar_url"])}" target="_blank" rel="noopener">Scholar</a>')
    if not review:
        links.append(
            f'<details class="cite"><summary class="mini">{t["cite"]}</summary>'
            f'<div class="cite-box"><pre><code>{esc(bibtex(p))}</code></pre>'
            f'<button type="button" class="copy-btn" data-copied="{t["copied"]}">{t["copy"]}</button></div></details>'
        )
    cites = p.get("citations") or 0
    meter = ""
    if cites:
        label = t["citation"] if cites == 1 else t["citations"].format(n=cites)
        meter = (f'<span class="cites"><span class="meter"><i data-w="{round(100 * cites / max_cites)}"></i></span>'
                 f'{label}</span>')
    venue_full = "" if review else f'<p class="venue-full">{esc(p.get("venue", ""))}</p>'
    return (
        f'<li class="pub reveal" data-tags="{" ".join(tags)}"><div class="pub-top">{top}</div>'
        f'<h3 lang="en">{title}</h3>'
        + (f'<p class="authors">{authors}</p>' if authors else "")
        + venue_full
        + (f'<ul class="ptags">{"".join(f"<li>{esc(topic_label(k, t))}</li>" for k in tags)}</ul>' if tags else "")
        + (f'<div class="pub-bottom">{"".join(links)}{meter}</div>' if links or meter else "")
        + "</li>"
    )


def render_publications(pubs, t):
    pending = [p for p in pubs if p.get("status")]
    published = sorted((p for p in pubs if not p.get("status")), key=lambda p: (p.get("year") or 0), reverse=True)
    ordered = pending + published
    max_cites = max([p.get("citations") or 0 for p in pubs] + [1])
    counts = {}
    for p in ordered:
        for k in topics_for(p):
            counts[k] = counts.get(k, 0) + 1
    total = len(ordered)
    groups = []
    for _, names, items in TOPIC_GROUPS:
        chips = "".join(
            f'<button type="button" class="fchip" data-filter="{k}" aria-pressed="false">{esc(de if t["lang"] == "de" else en)}'
            f'<span class="n">{counts[k]}</span></button>'
            for k, en, de, _ in items if counts.get(k))
        if chips:
            groups.append(f'<div class="fgroup"><span class="flabel">{names[t["lang"]]}</span>{chips}</div>')
    bar = (
        f'<div class="pub-filter" role="group" aria-label="{t["filter_label"]}">'
        f'<div class="fgroup fgroup-all"><button type="button" class="fchip active" data-filter="all" aria-pressed="true">{t["all"]}'
        f'<span class="n">{total}</span></button></div>' + "".join(groups) + "</div>"
        f'<p class="pub-count" aria-live="polite" data-template="{esc(t["showing"])}">{t["showing"].format(n=total, total=total)}</p>'
    )
    return bar + '\n<ol class="pubs">\n' + "\n".join(render_pub(p, t, max_cites) for p in ordered) + "\n</ol>"


def render_stats(store, t):
    m = store.get("metrics") or {}
    papers = sum(1 for p in store["publications"] if not p.get("status"))
    values = [m.get("citations"), m.get("h_index"), m.get("i10_index"), papers]
    return "\n".join(
        f'<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>' for k, v in zip(t["stats"], values) if v is not None
    )


def render_updated(store, t, counter=""):
    return t["synced_auto"] + (f"<br>{t['privacy']}" if counter else "")


def render_head(site_url, lang, path, t, store, counter=""):
    url = site_url + path
    image = site_url + "assets/img/profile.jpg"
    share = site_url + OG_IMAGE
    person = {
        "@context": "https://schema.org",
        "@type": "ProfilePage",
        "url": url,
        "name": t["page_title"],
        "inLanguage": lang,
        "primaryImageOfPage": {"@type": "ImageObject", "url": image, "width": 800, "height": 1000},
        "isPartOf": {"@id": site_url + "#website"},
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
            "knowsAbout": ["Artificial intelligence in medicine", "Machine learning", "Deep learning", "Medical image analysis",
                           "Foundation models", "Explainable AI", "Glaucoma", "Biomedical engineering"],
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
        {"@type": "ScholarlyArticle", "@id": f"{site_url}{pub_path(p)}#article", "name": p["title"],
         "datePublished": str(p.get("year") or ""), "url": site_url + pub_path(p),
         "sameAs": [u for u in [p.get("url"), doi_url(p)] if u], "author": {"@id": site_url + "#person"}}
        for p in published_pubs(store)
    ]
    person["mainEntity"]["subjectOf"] = works
    website = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "@id": site_url + "#website",
        "url": site_url,
        "name": "Amir Reza Naderi Yaghouti",
        "alternateName": ["Amir Naderi", "A. R. Naderi Yaghouti"],
        "inLanguage": ["en", "de"],
        "publisher": {"@id": site_url + "#person"},
    }
    title = t["page_title"]
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
        f'  <meta property="og:image" content="{esc(share)}">',
        '  <meta property="og:image:width" content="1200">',
        '  <meta property="og:image:height" content="630">',
        f'  <meta property="og:image:alt" content="{esc(SHARE_ALT)}">',
        f'  <meta property="og:locale" content="{t["og_locale"]}">',
        f'  <meta property="og:locale:alternate" content="{"de_DE" if lang == "en" else "en_US"}">',
        '  <meta property="profile:first_name" content="Amir Reza">',
        '  <meta property="profile:last_name" content="Naderi Yaghouti">',
        '  <meta name="twitter:card" content="summary_large_image">',
        f'  <meta name="twitter:title" content="{esc(title)}">',
        f'  <meta name="twitter:description" content="{esc(t["og_desc"])}">',
        f'  <meta name="twitter:image" content="{esc(share)}">',
        f'  <meta name="twitter:image:alt" content="{esc(SHARE_ALT)}">',
        '  <script type="application/ld+json">',
        json.dumps(website, indent=2, ensure_ascii=False).replace("</", "<\\/"),
        "  </script>",
        '  <script type="application/ld+json">',
        json.dumps(person, indent=2, ensure_ascii=False).replace("</", "<\\/"),
        "  </script>",
    ]
    if counter:
        # GoatCounter: cookie-free visitor counter (country, page, referrer); no personal data.
        lines.append(f'  <script data-goatcounter="https://{esc(counter)}.goatcounter.com/count" async src="https://gc.zgo.at/count.js"></script>')
    return "\n".join(lines)


def render_pub_page(p, site_url, counter=""):
    """A small page per paper: Google Scholar-style citation_* tags, schema.org data and a link home."""
    t = STRINGS["en"]
    url = site_url + pub_path(p)
    share = site_url + OG_IMAGE
    journal, volume, issue, pages = venue_parts(p.get("venue", ""))
    authors = author_list(p)
    year = str(p.get("year") or "")
    tags = [topic_label(k, t) for k in topics_for(p)]
    short_title = p["title"] if len(p["title"]) <= 90 else p["title"][:87].rsplit(" ", 1)[0] + "…"
    title = f"{short_title} | Amir Reza Naderi Yaghouti"
    others = [d for d, _, me in authors if not me]
    if len(others) > 3:
        others = others[:2] + ["colleagues"]
    with_ = f" with {', '.join(others[:-1]) + ' and ' if len(others) > 1 else ''}{others[-1]}" if others else ""
    desc = (f"Paper by Amir Reza Naderi Yaghouti (Amir Naderi){with_}"
            f"{', ' + journal if journal else ''}{', ' + year if year else ''}: {p['title']}.")
    cites = p.get("citations") or 0

    meta = [("citation_title", p["title"])] + [("citation_author", c) for _, c, _ in authors]
    meta += [(k, v) for k, v in [
        ("citation_publication_date", year), ("citation_journal_title", journal), ("citation_volume", volume),
        ("citation_issue", issue), ("citation_firstpage", re.split(r"[–-]", pages)[0] if pages else ""),
        ("citation_lastpage", re.split(r"[–-]", pages)[-1] if pages and re.search(r"[–-]", pages) else ""),
        ("citation_doi", p.get("doi", "")), ("citation_abstract_html_url", url),
    ] if v]

    person_id = site_url + "#person"
    article = {
        "@type": "ScholarlyArticle", "@id": url + "#article", "name": p["title"],
        "headline": short_title, "url": url, "mainEntityOfPage": url, "inLanguage": "en", "image": share,
        "author": [{"@id": person_id} if me else {"@type": "Person", "name": d} for d, _, me in authors],
        "datePublished": year,
        "sameAs": [u for u in [p.get("url"), doi_url(p)] if u],
    }
    if journal:
        article["isPartOf"] = {"@type": "Periodical", "name": journal}
    if p.get("doi"):
        article["identifier"] = {"@type": "PropertyValue", "propertyID": "DOI", "value": p["doi"]}
    if tags:
        article["keywords"] = ", ".join(tags)
    if p.get("abstract"):
        article["abstract"] = p["abstract"]
    graph = {"@context": "https://schema.org", "@graph": [
        article,
        {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Amir Reza Naderi Yaghouti", "item": site_url},
            {"@type": "ListItem", "position": 2, "name": "Publications", "item": site_url + "#publications"},
            {"@type": "ListItem", "position": 3, "name": short_title, "item": url},
        ]},
        {"@type": "Person", "@id": person_id, "name": "Amir Reza Naderi Yaghouti",
         "alternateName": "Amir Naderi", "url": site_url, "image": site_url + "assets/img/profile.jpg"},
    ]}

    names = ", ".join(f"<b>{esc(d)}</b>" if me else esc(d) for d, _, me in authors)
    if len(authors) < len([x for x in p.get("authors", "").split(",") if x.strip()]):
        names += ", et al."
    links = []
    if p.get("url"):
        links.append(f'<a class="btn btn-solid" href="{esc(p["url"])}" target="_blank" rel="noopener">Read the paper</a>')
    if p.get("doi"):
        links.append(f'<a class="btn" href="{esc(doi_url(p))}" target="_blank" rel="noopener">DOI: {esc(p["doi"])}</a>')
    if p.get("scholar_url"):
        links.append(f'<a class="btn" href="{esc(p["scholar_url"])}" target="_blank" rel="noopener">Google Scholar</a>')
    chip = esc(venue_short(p.get("venue", "")) or journal)
    abstract = (f'<h2>Abstract</h2><p class="abstract">{esc(p["abstract"])}</p>' if p.get("abstract") else "")
    cited = (f'<p class="paper-cites">Cited by {cites} {"paper" if cites == 1 else "papers"} '
             f'(Google Scholar, updated automatically).</p>' if cites else "")
    head_meta = "\n".join(f'  <meta name="{k}" content="{esc(v)}">' for k, v in meta)
    tag_list = f'<ul class="ptags">{"".join(f"<li>{esc(x)}</li>" for x in tags)}</ul>' if tags else ""
    ld = json.dumps(graph, indent=2, ensure_ascii=False).replace("</", "<\\/")
    gc = (f'\n  <script data-goatcounter="https://{esc(counter)}.goatcounter.com/count" async src="https://gc.zgo.at/count.js"></script>'
          if counter else "")
    return f"""<!doctype html>
{GENERATED}
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>{esc(title)}</title>
  <meta name="description" content="{esc(desc)}">
  <meta name="author" content="Amir Reza Naderi Yaghouti">
  <meta name="robots" content="index, follow, max-image-preview:large">
  <link rel="canonical" href="{esc(url)}">
{head_meta}
  <meta property="og:type" content="article">
  <meta property="og:site_name" content="Amir Reza Naderi Yaghouti">
  <meta property="og:title" content="{esc(p['title'])}">
  <meta property="og:description" content="{esc(desc)}">
  <meta property="og:url" content="{esc(url)}">
  <meta property="og:image" content="{esc(share)}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="og:image:alt" content="{esc(SHARE_ALT)}">
  <meta name="twitter:card" content="summary_large_image">
  <script type="application/ld+json">
{ld}
  </script>
  <meta name="theme-color" content="#0B1626">
  <link rel="icon" href="/favicon.ico" sizes="48x48">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">
  <link rel="apple-touch-icon" href="/apple-touch-icon.png">
  <link rel="manifest" href="/site.webmanifest">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;1,6..72,400&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
  <link rel="stylesheet" href="/assets/css/style.css">
  <script>try {{ var t = localStorage.getItem('theme'); if (t) document.documentElement.dataset.theme = t; }} catch (e) {{}}</script>{gc}
</head>
<body>
  <header class="nav">
    <div class="wrap">
      <a class="mark" href="/"><b>AN</b><span>Amir Reza Naderi Yaghouti</span></a>
      <a class="chip-btn" href="/#publications">All publications</a>
    </div>
  </header>
  <main id="main" class="paper">
    <div class="wrap">
      <article class="paper-body">
        <nav class="crumbs" aria-label="Breadcrumb"><a href="/">Home</a><span aria-hidden="true">/</span><a href="/#publications">Publications</a></nav>
        <div class="pub-top"><span class="venue">{chip}</span>{f'<span class="yr">{esc(year)}</span>' if year else ""}</div>
        <h1>{esc(p["title"])}</h1>
        <p class="authors">{names}</p>
        <p class="venue-full">{esc(p.get("venue", ""))}</p>
        {tag_list}
        <div class="paper-actions">{"".join(links)}</div>
        {cited}
        {abstract}
        <h2>Cite</h2>
        <div class="cite-box"><pre><code>{esc(bibtex(p))}</code></pre><button type="button" class="copy-btn">{t["copy"]}</button></div>
        <a class="author-card" href="/">
          <img src="/assets/img/profile.webp" alt="" width="64" height="64" loading="lazy">
          <span><b>Amir Reza Naderi Yaghouti</b><span>AI in medicine researcher, Marie Skłodowska-Curie Fellow at Otto-von-Guericke University Magdeburg and the University of Groningen. See all publications, research and CV →</span></span>
        </a>
      </article>
    </div>
  </main>
  <footer class="foot">
    <div class="wrap"><p>© {dt.date.today().year} Amir Reza Naderi Yaghouti</p><p><a href="/">amirnaderi.com</a></p></div>
  </footer>
  <script>
    document.querySelectorAll('.copy-btn').forEach(function (b) {{
      b.addEventListener('click', function () {{
        var code = b.parentNode.querySelector('code').textContent;
        (navigator.clipboard ? navigator.clipboard.writeText(code) : Promise.reject()).then(function () {{
          b.textContent = '{t["copied"]}'; setTimeout(function () {{ b.textContent = '{t["copy"]}'; }}, 1800);
        }}).catch(function () {{}});
      }});
    }});
  </script>
</body>
</html>
"""


def write_pub_pages(store, site_url, counter=""):
    """Write publications/<slug>/index.html for each paper and remove pages of papers that are gone."""
    base = ROOT / PUB_DIR
    keep = set()
    for p in published_pubs(store):
        target = base / pub_slug(p) / "index.html"
        keep.add(target.parent.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        page = render_pub_page(p, site_url, counter)
        old = target.read_text(encoding="utf-8") if target.exists() else ""
        # Ignore the copyright year so the page only changes when the paper does.
        if re.sub(r"© \d{4}", "", old) != re.sub(r"© \d{4}", "", page):
            target.write_text(page, encoding="utf-8")
    if base.exists():
        for d in base.iterdir():
            f = d / "index.html"
            if d.is_dir() and d.name not in keep and f.exists() and GENERATED in f.read_text(encoding="utf-8"):
                shutil.rmtree(d)


def write_sitemap_and_robots(site_url, store):
    today = dt.date.today().isoformat()
    alts = (
        f'    <xhtml:link rel="alternate" hreflang="en" href="{site_url}"/>\n'
        f'    <xhtml:link rel="alternate" hreflang="de" href="{site_url}de/"/>\n'
        f'    <xhtml:link rel="alternate" hreflang="x-default" href="{site_url}"/>\n'
    )
    photo = f"    <image:image><image:loc>{site_url}assets/img/profile.jpg</image:loc></image:image>\n"
    urls = "".join(
        f"  <url>\n    <loc>{site_url}{path}</loc>\n    <lastmod>{today}</lastmod>\n{alts}{photo}  </url>\n"
        for _, _, path in PAGES
    )
    urls += "".join(
        f"  <url>\n    <loc>{site_url}{pub_path(p)}</loc>\n    <lastmod>{today}</lastmod>\n  </url>\n"
        for p in published_pubs(store)
    )
    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml" '
        'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">\n' + urls + "</urlset>\n"
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
        result = fetch_with_retries(store["scholar_user"])
        if result:
            fetched, metrics = result
            before = json.dumps([{k: v for k, v in store.items() if k != "last_synced"}, news], sort_keys=True)
            if metrics:
                store["metrics"] = metrics
            added = merge(store, fetched, news, dt.date.today())
            after = json.dumps([{k: v for k, v in store.items() if k != "last_synced"}, news], sort_keys=True)
            print(f"Scholar: {len(fetched)} papers, {len(added)} new, metrics {store.get('metrics')}")
            # Only save when something besides the sync date changed, so daily runs
            # don't create a commit every day.
            if before != after:
                PUBS_FILE.write_text(json.dumps(store, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                NEWS_FILE.write_text(json.dumps(news, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            else:
                print("No changes on Google Scholar.")

    site_cfg = json.loads(SITE_FILE.read_text(encoding="utf-8"))
    site_url = site_cfg["url"].rstrip("/") + "/"
    counter = (site_cfg.get("goatcounter") or "").strip()
    for file, lang, path in PAGES:
        t = STRINGS[lang]
        target = ROOT / file
        page = target.read_text(encoding="utf-8")
        page = replace_block(page, "HEAD", render_head(site_url, lang, path, t, store, counter), file)
        page = replace_block(page, "STATS", render_stats(store, t), file)
        page = replace_block(page, "NEWS", render_news(news, t, lang), file)
        page = replace_block(page, "PUBLICATIONS", render_publications(store["publications"], t), file)
        page = replace_block(page, "UPDATED", render_updated(store, t, counter), file)
        target.write_text(page, encoding="utf-8")
    write_pub_pages(store, site_url, counter)
    write_sitemap_and_robots(site_url, store)

if __name__ == "__main__":
    main()
