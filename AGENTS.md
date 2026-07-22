# AGENTS.md

## Cursor Cloud specific instructions

- Scrapers write Dupli1-shaped `info.json` + product images under `images/`
  (gitignored).
- Standard library only for Prada / Loewe / Hermès / Louis Vuitton (`urllib`,
  `re`, `json`, `argparse`). Balenciaga and YSL need Chrome CDP on port `9222`
  and the `websocket-client` package when scraping live pages.
- When Akamai returns Access Denied, Balenciaga/YSL support
  `--from-html-dir <dir>` to parse saved listing/PDP HTML and download
  Kering DAM `eCom` JPEGs via urllib (DAM is not Akamai-blocked).
- Louis Vuitton US PDPs/images are Akamai-blocked; the scraper uses Wayback
  Machine snapshots for product JSON-LD and downloads packshots from
  `www.louisvuitton.cn` (same Scene7 asset paths).
- Hermès PDPs are often Cloudflare 403; default mode scrapes the bags PLP
  `hermes-state` listing (title/price/assets) instead.
- Useful commands:
  - `python3 prada/main.py --limit 20`
  - `python3 hermes/main.py --limit 20`
  - `python3 louisvuitton/main.py --limit 20`
  - `python3 loewe/main.py --limit 18`
  - `python3 balenciaga/main.py --limit 18` (CDP) or `--from-html-dir …`
  - `python3 ysl/main.py --limit 18` (CDP) or `--from-html-dir …`
- Shared helpers: `shared/http_util.py`, `shared/cdp.py`.
- `info.json` matches Dupli1 parent + variants (`product`, `variants[]` with
  `color` / `size` / `price` / `images`).
