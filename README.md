# gallery

Luxury product scrapers that write Dupli1-shaped `info.json` next to original
product images. Scrapers use the Python standard library plus an optional
Chrome CDP helper for Akamai-blocked sites (`websocket-client`).

## Brands

| Brand | Command | Notes |
|-------|---------|-------|
| Prada | `python3 prada/main.py` | urllib |
| Chanel | `python3 chanel/main.py` | urllib / Chrome / Wayback |
| Loewe | `python3 loewe/main.py` | urllib |
| Hermès | `python3 hermes/main.py` | urllib + desktop UA |
| Balenciaga | `python3 balenciaga/main.py` | Chrome CDP, or `--from-html-dir` |
| Saint Laurent | `python3 ysl/main.py` | Chrome CDP, or `--from-html-dir` |
| Bottega Veneta | `python3 bottega/main.py` | Chrome CDP, Wayback, or curated catalog |

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

# Bottega Veneta — curated 10 popular + 10 latest (DAM images via urllib):
python3 bottega/main.py --from-catalog bottega/catalog_top20.json -o images/bottega

# Or discover via Wayback when Akamai blocks datacenter IPs:
python3 bottega/main.py --from-wayback --limit 20

# Offline fallback (saved listing/PDP HTML + DAM image download):
python3 balenciaga/main.py --from-html-dir /path/to/html -o images/balenciaga --limit 18
python3 ysl/main.py --from-html-dir /path/to/html -o images/ysl --limit 18
python3 bottega/main.py --from-html-dir /path/to/html -o images/bottega --limit 20
```

Upload scraped folders to Dupli1:

```bash
DUPLI1_PASSWORD=… python3 shared/import_dupli1.py bottega
```
