# AGENTS.md

## Cursor Cloud specific instructions

- This repo contains stdlib-only product scrapers under `prada/main.py` and
  `louisvuitton/main.py`.
- Both use **only the Python standard library** (`urllib`, `re`, `argparse`).
  There are no third-party dependencies, no lockfile, and no package manifest,
  so there is nothing to install — run scripts directly with `python3`.
- Both require **outbound internet access** and send Chrome-on-Windows request
  headers (User-Agent, Client Hints, Sec-Fetch-*).

### Prada (`prada/main.py`)

- Scrapes original (full-resolution) product images from a Prada product page.
- Useful invocations:
  - `python3 prada/main.py` — download the default backpack product's images into `./images`.
  - `python3 prada/main.py --list-only` — print the original image URLs without downloading.
  - `python3 prada/main.py -o <dir> <product-url> [...]` — scrape one or more arbitrary
    Prada product URLs into `<dir>`.
- Original asset trick: Prada's AEM DAM serves the full-res JPEG at the base URL
  (`.../<CODE>.jpg`); the page only references downscaled renditions under
  `.../<CODE>.jpg/_jcr_content/renditions/...`. The script strips the rendition suffix to
  fetch the originals (2400x3000 for this product).

### Louis Vuitton (`louisvuitton/main.py`)

- Extracts product metadata (name, description, price, material, color, size,
  image) from Louis Vuitton product URLs and prints JSON lines to stdout.
- Useful invocations:
  - `python3 louisvuitton/main.py <product-url> [...]`
  - `python3 louisvuitton/main.py --pretty <product-url>` — pretty-printed JSON array.
- Louis Vuitton may return an **Access Denied** page or block the catalog API from
  some networks (VPN, datacenter/cloud, and certain ISPs). Disable VPN and use a
  residential connection if scraping fails.
