# gallery

Luxury product scrapers that write Dupli1-shaped `info.json` next to original
product images. All scrapers use **only the Python standard library** plus an
optional Chrome CDP helper for Akamai-blocked sites (`websocket-client`).

## Brands

| Brand | Command | Notes |
|-------|---------|-------|
| Prada | `python3 prada/main.py` | urllib |
| Loewe | `python3 loewe/main.py` | urllib |
| Hermès | `python3 hermes/main.py` | urllib + desktop UA |
| Balenciaga | `python3 balenciaga/main.py` | Chrome CDP (`:9222`) |
| Saint Laurent | `python3 ysl/main.py` | Chrome CDP (`:9222`) |

Default bag scrapes write under `images/<brand>/` (gitignored) with per-SKU
folders and `catalog.json`.

```bash
# Example: scrape ~18 Loewe bags
python3 loewe/main.py --limit 18

# Akamai brands need Chrome:
google-chrome --headless=new --disable-gpu --no-sandbox \
  --remote-debugging-port=9222 --remote-allow-origins=* about:blank &
python3 balenciaga/main.py --limit 18
python3 ysl/main.py --limit 18
```
