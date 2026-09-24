# Amir Reza Naderi Yaghouti: academic CV website

A static, single-page CV site. It uses plain HTML, CSS and a little JavaScript, has no build step, and works on GitHub Pages.

## Structure

| Path | What it is |
|------|------------|
| `index.html` | The page. Edit About, Experience, Education, Skills and Contact here directly. |
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

`date` can be `YYYY-MM` or `YYYY`, and `link` is optional. When you push, the Action re-renders the page. You can also run it locally:

```bash
python3 scripts/update_site.py          # re-render only
python3 scripts/update_site.py --fetch  # fetch Scholar, then re-render
```

## Publishing on GitHub Pages

1. Merge this branch into `main`.
2. Go to **Settings → Pages → Build and deployment**, choose **Deploy from a branch**, then **main** and **/ (root)**.
3. Your site will be at `https://amirnaderiy.github.io/websites-/`.

## Local preview

```bash
python3 -m http.server 8000   # then open http://localhost:8000
```
