# AGENTS.md

## Cursor Cloud specific instructions

- Scrapers write Dupli1-shaped `info.json` + product images under `images/`
  (gitignored).
- Standard library only for Prada / Miu Miu / Loewe / Hermès (`urllib`, `re`,
  `json`, `argparse`). Balenciaga and YSL need Chrome CDP on port `9222` and the
  `websocket-client` package when scraping live pages.
- When Akamai returns Access Denied, Balenciaga/YSL support
  `--from-html-dir <dir>` to parse saved listing/PDP HTML and download
  Kering DAM `eCom` JPEGs via urllib (DAM is not Akamai-blocked).
- Useful commands:
  - `python3 prada/main.py`
  - `python3 miumiu/main.py -o images/miumiu`
  - `python3 loewe/main.py --limit 18`
  - `python3 hermes/main.py --limit 18`
  - `python3 balenciaga/main.py --limit 18` (CDP) or `--from-html-dir …`
  - `python3 ysl/main.py --limit 18` (CDP) or `--from-html-dir …`
- Shared helpers: `shared/http_util.py`, `shared/cdp.py`.
- `info.json` matches Dupli1 parent + variants (`product`, `variants[]` with
  `color` / `size` / `price` / `images`).

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

### Miu Miu

- Site: `https://www.miumiu.com`. Same Prada Group AEM catalog pattern as Prada
  (`colorVariants`, `sizeCodes`, DAM originals under `miumiubkg_products`).
- Useful invocations:
  - `python3 miumiu/main.py` — curated top-15 bags → `./images/miumiu` +
    `catalog.json`
  - `python3 miumiu/main.py --discover` — list bag PDPs from the US bags PLP
  - `python3 miumiu/main.py --list-only <url>`
  - `python3 miumiu/main.py -o <dir> <product-url> [...]`
- Original asset trick: strip AEM rendition suffixes; filter DAM URLs by SKU so
  sibling color thumbs on the same PDP are skipped.
- Schema: [docs/miumiu-info-json.md](docs/miumiu-info-json.md)
- Import: `python3 miumiu/import_dupli1.py` (set `DUPLI1_EMAIL` /
  `DUPLI1_PASSWORD`).

### Dupli1

- `info.json` matches Dupli1’s parent + variants model (`product`, `variants[]`
  with `color` / `size` / `price` / `images`). Import into
  [elug3/dupli1](https://github.com/elug3/dupli1).
