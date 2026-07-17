# AGENTS.md

## Cursor Cloud specific instructions

- This repo contains utility scrapers that write Dupli1-shaped `info.json` plus
  product images:
  - `prada/main.py` — Prada fashion PDPs
  - `miumiu/main.py` — Miu Miu fashion PDPs (bags-focused defaults)
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

### Dupli1

- `info.json` matches Dupli1’s parent + variants model (`product`, `variants[]`
  with `color` / `size` / `price` / `images`). Import into
  [elug3/dupli1](https://github.com/elug3/dupli1).
