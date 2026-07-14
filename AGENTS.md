# AGENTS.md

## Cursor Cloud specific instructions

- This repo currently contains a single utility script, `prada/main.py`, which scrapes
  original (full-resolution) product images and product metadata from a Prada product page.
- It uses **only the Python standard library** (`urllib`, `re`, `argparse`, `json`). There are no
  third-party dependencies, no lockfile, and no package manifest, so there is nothing to
  install — run it directly with `python3 prada/main.py`.
- It requires **outbound internet access** to reach `https://www.prada.com`. The site only
  returns the full image markup when a desktop browser `User-Agent` is sent (the script
  already does this); requests without it get a stripped-down response.
- Useful invocations:
  - `python3 prada/main.py` — download the default backpack product's images into `./images`
    and write `./images/info.json`.
  - `python3 prada/main.py --list-only` — print the original image URLs without downloading.
  - `python3 prada/main.py -o <dir> <product-url> [...]` — scrape one or more arbitrary
    Prada product URLs into `<dir>` (writes images + `info.json`; multi-URL runs use a
    per-SKU subdirectory).
- `info.json` is built from schema.org JSON-LD plus page details (name, description, sku,
  color, material, price/currency, dimensions, detail bullets, and image filenames/URLs).
- Original asset trick: Prada's AEM DAM serves the full-res JPEG at the base URL
  (`.../<CODE>.jpg`); the page only references downscaled renditions under
  `.../<CODE>.jpg/_jcr_content/renditions/...`. The script strips the rendition suffix to
  fetch the originals (2400x3000 for this product).
