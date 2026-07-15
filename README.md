# gallery

Luxury product scrapers that write Dupli1-shaped `info.json` next to original
product images.

## Prada

```bash
python3 prada/main.py -o images <prada-product-url>
```

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
