# gallery

Luxury product scrapers that write Dupli1-shaped `info.json` next to original
product images.

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

Both scrapers use **only the Python standard library**.
