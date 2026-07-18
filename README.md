# gallery

Luxury product scrapers that write Dupli1-shaped `info.json` next to original
<<<<<<< HEAD
product images.

## Prada
=======
product images. All scrapers use **only the Python standard library** plus an
optional Chrome CDP helper for Akamai-blocked sites (`websocket-client`).

## Brands

| Brand | Command | Notes |
|-------|---------|-------|
| Prada | `python3 prada/main.py` | urllib |
| Loewe | `python3 loewe/main.py` | urllib |
| Hermès | `python3 hermes/main.py` | urllib + desktop UA |
| Balenciaga | `python3 balenciaga/main.py` | Chrome CDP, or `--from-html-dir` offline |
| Saint Laurent | `python3 ysl/main.py` | Chrome CDP, or `--from-html-dir` offline |

Default bag scrapes write under `images/<brand>/` (gitignored) with per-SKU
folders and `catalog.json`.
>>>>>>> origin/main

```bash
# Example: scrape ~18 Loewe / Hermès bags
python3 loewe/main.py --limit 18
python3 hermes/main.py --limit 18

<<<<<<< HEAD
See [docs/prada-info-json.md](docs/prada-info-json.md).

## Chanel

```bash
# Curated top-20 bag URLs (may be blocked by Akamai from some networks)
python3 chanel/main.py -o images/chanel

# List bag PDPs from the US sitemap
python3 chanel/main.py --discover

# Parse a browser-saved PDP or the included fixture
python3 chanel/main.py --from-html chanel/fixtures/small-classic-handbag.html -o /tmp/chanel-out
```

See [docs/chanel-info-json.md](docs/chanel-info-json.md) for the schema, Dupli1
import steps, and Akamai access notes.

Both scrapers use **only the Python standard library**.
=======
# Akamai brands need Chrome when live:
google-chrome --headless=new --disable-gpu --no-sandbox \
  --user-data-dir=/tmp/chrome-cdp-profile \
  --remote-debugging-port=9222 --remote-allow-origins=* about:blank &
python3 balenciaga/main.py --limit 18
python3 ysl/main.py --limit 18

# Offline fallback (saved listing/PDP HTML + DAM image download):
python3 balenciaga/main.py --from-html-dir /path/to/html -o images/balenciaga --limit 18
python3 ysl/main.py --from-html-dir /path/to/html -o images/ysl --limit 18
```
>>>>>>> origin/main
