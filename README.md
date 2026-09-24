# Amir Reza Naderi Yaghouti: academic CV website

A static, single-page CV site. It uses plain HTML, CSS and a little JavaScript, has no build step, and works on GitHub Pages.

## Structure

| Path | What it is |
|------|------------|
| `index.html` | English page. Edit About, Experience, Education, Skills and Contact here directly. |
| `de/index.html` | German page (same structure). When you change `index.html`, make the same change here. |
| `data/site.json` | The site's public address, used for Google (canonical links, sitemap). |
| `sitemap.xml`, `robots.txt` | Generated for search engines. Don't edit by hand. |
| `assets/img/profile.jpg` | **Your photo.** Portrait orientation, about 800×1000 px. Until it exists, the page shows your initials. |
| `data/publications.json` | Publications list, synced from Google Scholar. |
| `data/news.json` | News items. Add your own entries at the top; paper announcements are added automatically. |
| `scripts/update_site.py` | Fetches Google Scholar and renders the publications, news and metrics into `index.html`. |
| `.github/workflows/update-publications.yml` | Runs the script every Monday, and when you click **Run workflow**. |

The sections between `<!-- AUTO:... -->` markers in `index.html` are generated. Don't edit them by hand.

## How automatic publications work

1. Every Monday a GitHub Action opens your Google Scholar profile (`WmUoRTsAAAAJ`).
2. Any paper that isn't already in `data/publications.json` is added. A **"New paper"** item is also added to the top of `data/news.json`.
3. Citation count, h-index and i10-index are refreshed.
4. The action commits the changes, and GitHub Pages republishes the site.

The first sync imports your existing papers without creating News items, so older papers don't flood the News section.

Google Scholar sometimes blocks automated requests from GitHub's servers. If the Action log shows `could not fetch Google Scholar`, the site keeps its current data. To make syncing reliable:

1. Create a free account at [serpapi.com](https://serpapi.com) (100 searches/month is plenty).
2. In the repo, go to **Settings → Secrets and variables → Actions** and add a secret named `SERPAPI_KEY`.

## Adding a news item by hand

Add an entry to the top of `data/news.json`:

```json
{ "date": "2026-09", "text": "Presented my work at ARVO 2026.", "link": "https://..." }
```

`date` can be `YYYY-MM` or `YYYY`, and `link` is optional. Add `"text_de"` for the German page; without it, the English text is shown there too. When you push, the Action re-renders the page. You can also run it locally:

```bash
python3 scripts/update_site.py          # re-render only
python3 scripts/update_site.py --fetch  # fetch Scholar, then re-render
```

## Publishing on GitHub Pages

1. Go to **Settings → Pages → Build and deployment**.
2. Under **Source**, choose **Deploy from a branch**. Pick the repository's default branch and **/ (root)**, then save.
3. After about a minute the site is live at `https://amirnaderiy.github.io/websites-/`.

## Languages

The English page is at `/` and the German page at `/de/`. The **DE / EN** button in the header switches between them and remembers the choice, so returning visitors land on their language. Each language is a real page, so Google indexes both and shows German searchers the German version (`hreflang` tags).

## Google (SEO)

Each page includes:
- a title and description with both **Amir Reza Naderi Yaghouti** and **Amir Naderi**
- `schema.org` Person data (name variants, affiliations, profiles, papers), which Google uses to understand who the page is about
- canonical and `hreflang` links, Open Graph and Twitter preview tags, plus `sitemap.xml` and `robots.txt`

These steps help most after the site is live:
1. **Google Search Console:** open https://search.google.com/search-console, add the site URL as a *URL prefix* property, and verify it with the *HTML tag* method. Put that tag in the `<head>` of `index.html`, or ask Claude to. Then submit `sitemap.xml` under **Sitemaps** and click **Request indexing**.
2. **Link to the site from your profiles.** Add it to your LinkedIn contact info, GitHub profile, Google Scholar homepage field and ResearchGate. Links from these high-authority sites are the biggest ranking signal for a personal name.
3. **Optional:** rename the repo to `amirnaderiy.github.io` so the address becomes `https://amirnaderiy.github.io/`, or use a custom domain. Then update `data/site.json` and run `python3 scripts/update_site.py`.

## Local preview

```bash
python3 -m http.server 8000   # then open http://localhost:8000
```
