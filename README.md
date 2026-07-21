# gallery

Luxury product scrapers that write Dupli1-shaped `info.json` next to original
product images. All scrapers use **only the Python standard library** plus an
optional Chrome CDP helper for Akamai-blocked sites (`websocket-client`).

## Brands

| Brand | Command | Notes |
|-------|---------|-------|
| Prada | `python3 prada/main.py --limit 20` | urllib + bags PLP/sitemap discovery |
| Hermès | `python3 hermes/main.py --limit 20` | bags PLP `hermes-state` (PDPs often 403) |
| Louis Vuitton | `python3 louisvuitton/main.py --limit 20` | Wayback PDP + CN image CDN |
| Loewe | `python3 loewe/main.py` | urllib |
| Balenciaga | `python3 balenciaga/main.py` | Chrome CDP, or `--from-html-dir` offline |
| Saint Laurent | `python3 ysl/main.py` | Chrome CDP, or `--from-html-dir` offline |

Default bag scrapes write under `images/<brand>/` (gitignored) with per-SKU
folders and `catalog.json`.

```bash
# Top-20 bags
python3 prada/main.py --limit 20
python3 hermes/main.py --limit 20
python3 louisvuitton/main.py --limit 20

# Other brands (~18)
python3 loewe/main.py --limit 18

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
