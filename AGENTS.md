# AGENTS.md

## Cursor Cloud specific instructions

<<<<<<< HEAD
- This repo contains utility scrapers that write Dupli1-shaped `info.json` plus
  product images:
  - `prada/main.py` — Prada fashion PDPs
  - `chanel/main.py` — Chanel fashion PDPs (bags-focused defaults)
- Both use **only the Python standard library** (`urllib`, `re`, `argparse`,
  `json`). There are no third-party dependencies — run with `python3 …`.
- Outbound internet is required for live scrapes.

### Prada

- Site: `https://www.prada.com`. A desktop browser `User-Agent` is required
  (already set in the script).
- Useful invocations:
  - `python3 prada/main.py` — default backpack → `./images` + `info.json`
  - `python3 prada/main.py --list-only`
  - `python3 prada/main.py -o <dir> <product-url> [...]`
- Original asset trick: strip AEM rendition suffixes
  (`.../<CODE>.jpg/_jcr_content/renditions/...` → `.../<CODE>.jpg`).
- Schema: [docs/prada-info-json.md](docs/prada-info-json.md)

### Chanel

- Site: `https://www.chanel.com`. Product data comes from `__NEXT_DATA__` /
  JSON-LD; images from `https://www.chanel.com/images/...` (Cloudinary-style
  transforms; script bumps toward `w_3200` packshots).
- **Akamai** often returns HTTP 403 for automated GET on `/fashion/p/...` and
  `/c/...` from datacenter IPs. The US sitemap and many editorial pages still
  work. Prefer:
  - `python3 chanel/scrape_top20_chrome.py` — live PDPs via Chrome CDP (bypasses
    urllib 403 when a desktop Chrome with `--remote-debugging-port=9222` is up)
  - `python3 chanel/scrape_top20_wayback.py` — Wayback Machine fallback
  - `python3 chanel/main.py --discover` — list bag URLs from the sitemap
  - `python3 chanel/main.py --from-html <saved-pdp.html>` — offline / browser save
  - Fixture: `chanel/fixtures/small-classic-handbag.html`
- Image upgrades preserve `/images/as/…` delivery paths and fall back across CDN
  URL candidates when `w_3200` rebuilds 404.
- Default with no args is a curated top-20 bag URL list; multi-URL runs write
  per-SKU subdirs under `-o` (default `./images/chanel`).
- Schema: [docs/chanel-info-json.md](docs/chanel-info-json.md)

### Dupli1

- `info.json` matches Dupli1’s parent + variants model (`product`, `variants[]`
  with `color` / `size` / `price` / `images`). Import into
  [elug3/dupli1](https://github.com/elug3/dupli1).
=======
- Scrapers write Dupli1-shaped `info.json` + product images under `images/`
  (gitignored).
- Standard library only for Prada / Loewe / Hermès (`urllib`, `re`, `json`,
  `argparse`). Balenciaga and YSL need Chrome CDP on port `9222` and the
  `websocket-client` package when scraping live pages.
- When Akamai returns Access Denied, Balenciaga/YSL support
  `--from-html-dir <dir>` to parse saved listing/PDP HTML and download
  Kering DAM `eCom` JPEGs via urllib (DAM is not Akamai-blocked).
- Useful commands:
  - `python3 loewe/main.py --limit 18`
  - `python3 hermes/main.py --limit 18`
  - `python3 balenciaga/main.py --limit 18` (CDP) or `--from-html-dir …`
  - `python3 ysl/main.py --limit 18` (CDP) or `--from-html-dir …`
- Shared helpers: `shared/http_util.py`, `shared/cdp.py`.
- `info.json` matches Dupli1 parent + variants (`product`, `variants[]` with
  `color` / `size` / `price` / `images`).
>>>>>>> origin/main
