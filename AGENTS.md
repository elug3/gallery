# AGENTS.md

## Cursor Cloud specific instructions

- Scrapers write Dupli1-shaped `info.json` + product images under `images/`
  (gitignored).
- Standard library only for Prada / Loewe / Hermès / Chanel (`urllib`, `re`,
  `json`, `argparse`). Balenciaga, YSL, and Bottega Veneta need Chrome CDP on
  port `9222` and the `websocket-client` package when scraping live pages.
- When Akamai returns Access Denied:
  - Balenciaga/YSL/Bottega support `--from-html-dir <dir>` to parse saved
    listing/PDP HTML and download Kering DAM JPEGs via urllib (DAM is not
    Akamai-blocked).
  - Bottega also supports `--from-wayback` (Wayback PLP snapshots) and
    `--from-catalog bottega/catalog_top20.json` for the curated 10 popular +
    10 latest bag set.
- Useful commands:
  - `python3 loewe/main.py --limit 18`
  - `python3 hermes/main.py --limit 18`
  - `python3 balenciaga/main.py --limit 18` (CDP) or `--from-html-dir …`
  - `python3 ysl/main.py --limit 18` (CDP) or `--from-html-dir …`
  - `python3 bottega/main.py --from-catalog bottega/catalog_top20.json`
  - `python3 bottega/main.py --from-wayback --limit 20`
- Shared helpers: `shared/http_util.py`, `shared/cdp.py`.
- `info.json` matches Dupli1 parent + variants (`product`, `variants[]` with
  `color` / `size` / `price` / `images`).
