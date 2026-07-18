# gallery

Luxury product scrapers that write Dupli1-shaped `info.json` next to original
product images. Most scrapers use **only the Python standard library**;
Balenciaga / YSL optionally use Chrome CDP (`websocket-client`) for
Akamai-blocked sites.

## Brands

| Brand | Command | Notes |
|-------|---------|-------|
| Prada | `python3 prada/main.py` | urllib |
| Miu Miu | `python3 miumiu/main.py` | urllib (Prada Group AEM) |
| Loewe | `python3 loewe/main.py` | urllib |
| Hermès | `python3 hermes/main.py` | urllib + desktop UA |
| Balenciaga | `python3 balenciaga/main.py` | Chrome CDP, or `--from-html-dir` offline |
| Saint Laurent | `python3 ysl/main.py` | Chrome CDP, or `--from-html-dir` offline |

Default bag scrapes write under `images/<brand>/` (gitignored) with per-SKU
folders and `catalog.json`.

```bash
# Example: scrape ~18 Loewe / Hermès bags
python3 loewe/main.py --limit 18
python3 hermes/main.py --limit 18

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

## Prada

```bash
python3 prada/main.py -o images <prada-product-url>
```

See [docs/prada-info-json.md](docs/prada-info-json.md).

## Miu Miu

```bash
# Curated top-15 bag URLs
python3 miumiu/main.py -o images/miumiu

# List bag PDPs from the US bags PLP
python3 miumiu/main.py --discover
```

See [docs/miumiu-info-json.md](docs/miumiu-info-json.md).

```bash
# Import scraped bags into manage.dupli1.com
DUPLI1_EMAIL=agent@dupli1.com DUPLI1_PASSWORD='…' \
  python3 miumiu/import_dupli1.py
```
