# Prada `info.json` → Dupli1 product mapping

`prada/main.py` writes `info.json` next to scraped original images. The file is
shaped for import into [elug3/dupli1](https://github.com/elug3/dupli1)’s product
service: **one parent style** + **sellable variants (SKUs)**.

Dupli1 reference:

- Domain: `product/pkg/domain/products.go` (`Product`, `Variant`)
- Plan: `docs/product-variants-plan.md`
- Frontend migration: `docs/frontend-product-variants-migration.md`
- API: `POST /api/v1/products`, `POST /api/v1/products/{id}/variants`,
  `POST /api/v1/products/{id}/variants/{sku}/images`

## Schema

```json
{
  "product": {
    "name": "Prada Bonnie small printed linen and leather handbag",
    "description": "...",
    "brand": "Prada",
    "material": "Linen",
    "category": "bags",
    "capacity": "12 cm × 23.5 cm × 9 cm",
    "status": "draft",
    "tags": ["prada", "bags"],
    "sourceUrl": "https://www.prada.com/us/en/p/.../1BA486_2FPT_F0009_V_OFO"
  },
  "variants": [
    {
      "sku": "1BA486_2FPT_F0009_V_OFO",
      "color": "White",
      "size": "",
      "price": 2850.0,
      "status": "active",
      "images": ["1BA486_2FPT_F0009_V_OFO_SLF.jpg"],
      "imageUrls": ["https://www.prada.com/content/dam/.../SLF.jpg"],
      "selected": true,
      "available": true,
      "sourceUrl": "https://www.prada.com/us/en/p/.../1BA486_2FPT_F0009_V_OFO",
      "hex": "#FFFFFF"
    }
  ],
  "availableColors": ["White"],
  "availableSizes": [],
  "details": ["Leather handles, drop 10 cm", "..."],
  "dimensions": { "height": "12 cm", "width": "23.5 cm", "length": "9 cm" },
  "currency": "USD",
  "productGroupId": "1BA486_2FPT",
  "selectedSku": "1BA486_2FPT_F0009_V_OFO"
}
```

### Field mapping

| `info.json` | Dupli1 | Source on Prada |
|-------------|--------|-----------------|
| `product.name` | parent `name` | JSON-LD `ProductGroup.name` |
| `product.description` | parent `description` | JSON-LD description |
| `product.brand` | parent `brand` | JSON-LD brand (default `Prada`) |
| `product.material` | parent `material` | JSON-LD / “Main material” |
| `product.category` | parent `category` | Fixed `bags` (override on import if needed) |
| `product.capacity` | parent `capacity` | Formatted from page dimensions |
| `product.status` | parent `status` | Default `draft` |
| `product.tags` | parent `tags` | `["prada", "bags"]` |
| `product.sourceUrl` | *(scraper only)* | Scraped PDP URL |
| `variants[].sku` | variant `sku` | Color `partNumber` (bags) or size SKU |
| `variants[].color` | variant `color` | `colorVariants[].color` |
| `variants[].size` | variant `size` | `sizeCodes[].value`; `TU` → `""` |
| `variants[].price` | variant `price` | Offer price as number |
| `variants[].status` | variant `status` | `active` if available else `draft` |
| `variants[].images` | upload files → `imageUrls` | Local filenames (selected color only) |
| `variants[].imageUrls` | optional remote refs | Original DAM URLs (selected color only) |
| `availableColors` | derived on PDP | Distinct variant colors |
| `availableSizes` | derived on PDP | Non-empty sizes on selected color |
| `details` | append to description / ignore | Page detail bullets |
| `dimensions` | feeds `capacity` | Height / width / length |

## Color and size rules

Prada exposes:

- **`colorVariants`** — sibling color products (each has its own URL / part number).
- **`sizeCodes`** — sizes for the **currently selected** color only.

This scraper:

1. Attaches full `images` / `imageUrls` only to the **selected** color’s variant(s).
2. Emits **stub** variants for sibling colors (`images: []`, plus `thumbnail` /
   `sourceUrl`) so an importer knows which PDPs to scrape next.
3. Maps Prada one-size `TU` to Dupli1 empty `size` (`""`).
4. For one-size bags, uses the **color-level** SKU (e.g. `1BA486_2FPT_F0009_V_OFO`),
   not the inventory size id (`..._1501`).

Multi-color example (selected Black, siblings Blue / Beige):

```text
variants:
  - Black  size=""  images=[...]  selected=true
  - Aviation Blue  size=""  images=[]  thumbnail=...  sourceUrl=...
  - Desert Beige   size=""  images=[]  thumbnail=...  sourceUrl=...
```

To fill sibling images, scrape each stub’s `sourceUrl` (or pass all color URLs
to `prada/main.py` so each gets its own output dir + `info.json`).

## Dupli1 import flow

1. `POST /api/v1/products` with `info.product` (omit scraper-only `sourceUrl` if
   the API rejects unknown fields — fold it into `description` or `tags` instead).
2. For each `variants[]` entry that should be sold:
   - `POST /api/v1/products/{id}/variants` with `sku`, `color`, `size`, `price`,
     `status`.
3. For each local file in `variants[].images`:
   - `POST /api/v1/products/{id}/variants/{sku}/images` (multipart `image`).
4. `PUT /api/v1/inventory/{sku}` to set stock.
5. Optionally re-scrape sibling `sourceUrl`s and repeat steps 2–4.

Prefer Dupli1 auto-generated parent ids (`PRA-001`) unless you intentionally
reuse Prada codes as parent ids.

## Generate

```bash
python3 prada/main.py -o images \
  "https://www.prada.com/us/en/p/prada-bonnie-small-printed-linen-and-leather-handbag/1BA486_2FPT_F0009_V_OFO"
```

Produces `images/*.jpg` (2400×3000 originals) and `images/info.json`.
