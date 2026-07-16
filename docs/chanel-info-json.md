# Chanel `info.json` → Dupli1 product mapping

`chanel/main.py` writes `info.json` next to scraped product images. The file is
shaped for import into [elug3/dupli1](https://github.com/elug3/dupli1)’s product
service: **one parent style** + **sellable variants (SKUs)** — the same shape as
[`docs/prada-info-json.md`](prada-info-json.md).

## Schema

```json
{
  "product": {
    "name": "Small Classic Handbag",
    "description": "Quilted grained calfskin classic flap handbag with gold-tone metal. 5.5 x 8.7 x 2.6 in",
    "brand": "Chanel",
    "material": "Grained calfskin",
    "category": "bags",
    "capacity": "5.5 in × 8.7 in × 2.6 in",
    "status": "draft",
    "tags": ["chanel", "bags", "classic"],
    "sourceUrl": "https://www.chanel.com/us/fashion/p/A01113Y01864C3906/small-classic-handbag-grained-calfskin-gold-tone-metal/"
  },
  "variants": [
    {
      "sku": "A01113Y01864C3906",
      "color": "Black",
      "size": "Small",
      "price": 10900.0,
      "status": "active",
      "images": ["image_01_9559951147038.jpg"],
      "imageUrls": ["https://www.chanel.com/images/.../w_3200/-9559951147038.jpg"],
      "selected": true,
      "available": true,
      "sourceUrl": "https://www.chanel.com/us/fashion/p/A01113Y01864C3906/..."
    }
  ],
  "availableColors": ["Black", "Beige"],
  "availableSizes": ["Small"],
  "details": ["Reference: A01113-Y01864-C3906", "Collection: Classic"],
  "dimensions": { "height": "5.5 in", "width": "8.7 in", "depth": "2.6 in" },
  "currency": "USD",
  "productGroupId": "A01113",
  "selectedSku": "A01113Y01864C3906"
}
```

### Field mapping

| `info.json` | Dupli1 | Source on Chanel |
|-------------|--------|------------------|
| `product.name` | parent `name` | `__NEXT_DATA__` `title` / JSON-LD / `og:title` |
| `product.description` | parent `description` | `briefDescription` / JSON-LD / meta |
| `product.brand` | parent `brand` | Fixed `Chanel` |
| `product.material` | parent `material` | `details.fabrics` / `materials[].label` |
| `product.category` | parent `category` | Fixed `bags` |
| `product.capacity` | parent `capacity` | Parsed `H x W x D` from description / page |
| `product.status` | parent `status` | Default `draft` |
| `product.tags` | parent `tags` | `["chanel", "bags", …]` |
| `product.sourceUrl` | *(scraper only)* | Scraped PDP URL |
| `variants[].sku` | variant `sku` | Product `sku` / `id` (e.g. `A01113Y01864C3906`) |
| `variants[].color` | variant `color` | `details.color` / variation siblings |
| `variants[].size` | variant `size` | `sizeLabel`; one-size / UNI → `""` |
| `variants[].price` | variant `price` | `price.priceAmount` / JSON-LD offer |
| `variants[].images` | upload files → `imageUrls` | Local filenames (selected color only) |
| `variants[].imageUrls` | optional remote refs | Upgraded packshot CDN URLs |
| `availableColors` | derived on PDP | Distinct variant colors |
| `details` | append / ignore | Reference, collection, hardware |
| `dimensions` | feeds `capacity` | Parsed height / width / depth |

## Color and size rules

Chanel fashion PDPs expose:

- **`variations[].products[]`** — sibling color (or finish) products, each with
  its own URL / SKU.
- **`sizeLabel`** — often a named size (`Small`, `Medium`) rather than apparel
  lettering; one-size / `UNI` maps to `""`.

This scraper:

1. Attaches full `images` / `imageUrls` only to the **selected** SKU.
2. Emits **stub** variants for sibling colors (`images: []`, plus `thumbnail` /
   `sourceUrl`) so an importer knows which PDPs to scrape next.
3. Upgrades image delivery URLs toward `w_3200` packshots when Cloudinary-style
   transforms are present.

## Akamai / access notes

Chanel’s Akamai edge frequently returns **HTTP 403 Access Denied** for
automated GET requests to:

- Product pages: `/us/fashion/p/{sku}/…`
- Commerce PLPs: `/us/fashion/…/c/…`

from datacenter IPs. The US **sitemap** and many editorial handbag story pages
remain reachable. Workarounds:

```bash
# List bag PDPs from the sitemap (works without PDP access)
python3 chanel/main.py --discover

# Parse a PDP you saved from a browser
python3 chanel/main.py --from-html chanel/fixtures/small-classic-handbag.html -o /tmp/chanel-out

# Offline fixture (no network)
python3 chanel/main.py --from-html chanel/fixtures/small-classic-handbag.html -o /tmp/chanel-out
```

Live scrape when your network can open Chanel PDPs:

```bash
python3 chanel/main.py -o images/chanel \
  "https://www.chanel.com/us/fashion/p/A01113Y01864C3906/small-classic-handbag-grained-calfskin-gold-tone-metal/"
```

Default with no URLs is the curated **top-20 bag** list embedded in
`chanel/main.py`.

## Dupli1 import flow

Same as Prada — see [prada-info-json.md](prada-info-json.md#dupli1-import-flow):

1. `POST /api/v1/products` with `info.product`
2. `POST /api/v1/products/{id}/variants` per sellable row
3. Upload each file in `variants[].images`
4. Optionally re-scrape stub `sourceUrl`s for sibling colors

Batch helper (requires `DUPLI1_PASSWORD`):

```bash
DUPLI1_EMAIL=agent@dupli1.com DUPLI1_PASSWORD='…' \
  python3 chanel/import_dupli1.py
```

Creates Chanel catalog styles (`CH/<SKU[:12]>`), variants, and uploads local
source images to `manage.dupli1.com`.
