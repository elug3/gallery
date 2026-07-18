# Miu Miu `info.json` → Dupli1 product mapping

`miumiu/main.py` writes `info.json` next to scraped original images. The file is
shaped for import into [elug3/dupli1](https://github.com/elug3/dupli1)’s product
service: **one parent style** + **sellable variants (SKUs)** — the same shape as
[`docs/prada-info-json.md`](prada-info-json.md).

## Schema

```json
{
  "product": {
    "name": "Spirit nappa leather bag",
    "description": "The compact design of the Miu Miu pouch defines the lines…",
    "brand": "Miu Miu",
    "material": "Nappa leather",
    "category": "bags",
    "capacity": "16 cm × 22 cm × 11.5 cm",
    "status": "draft",
    "tags": ["miumiu", "bags", "spirit"],
    "sourceUrl": "https://www.miumiu.com/us/en/p/spirit-nappa-leather-bag/5BC216_QJB_F0002_V_OOO"
  },
  "variants": [
    {
      "sku": "5BC216_QJB_F0002_V_OOO",
      "color": "Black",
      "size": "",
      "price": 1950.0,
      "status": "active",
      "images": ["5BC216_QJB_F0002_V_OOO_SLF.jpg"],
      "imageUrls": ["https://www.miumiu.com/content/dam/miumiubkg_products/.../SLF.jpg"],
      "selected": true,
      "available": true,
      "sourceUrl": "https://www.miumiu.com/us/en/p/.../5BC216_QJB_F0002_V_OOO",
      "hex": "#000000"
    }
  ],
  "availableColors": ["Black", "Palisander"],
  "availableSizes": [],
  "details": [
    "Adjustable leather handle",
    "Antiqued gold-tone hardware",
    "Product code: 5BC216_QJB_F0002_V_OOO"
  ],
  "dimensions": { "height": "16 cm", "width": "22 cm", "length": "11.5 cm" },
  "currency": "USD",
  "productGroupId": "5BC216_QJB",
  "selectedSku": "5BC216_QJB_F0002_V_OOO"
}
```

### Field mapping

| `info.json` | Dupli1 | Source on Miu Miu |
|-------------|--------|-------------------|
| `product.name` | parent `name` | JSON-LD / catalog `name` |
| `product.description` | parent `description` | meta description |
| `product.brand` | parent `brand` | JSON-LD (default `Miu Miu`) |
| `product.material` | parent `material` | catalog `MaterialGroup` + name cues |
| `product.category` | parent `category` | Fixed `bags` |
| `product.capacity` | parent `capacity` | catalog Height / Width / Length |
| `product.status` | parent `status` | Default `draft` |
| `product.tags` | parent `tags` | `["miumiu", "bags", <line>]` |
| `product.sourceUrl` | *(scraper only)* | Scraped PDP URL |
| `variants[].sku` | variant `sku` | Color `partNumber` (bags) |
| `variants[].color` | variant `color` | `colorVariants[].color` |
| `variants[].size` | variant `size` | `sizeCodes[].value`; `TU` → `""` |
| `variants[].price` | variant `price` | Catalog / offer price |
| `variants[].images` | upload files → `imageUrls` | Local filenames (selected color only) |
| `variants[].imageUrls` | optional remote refs | Original DAM URLs (selected color only) |

## Color and size rules

Same as Prada:

1. Full `images` / `imageUrls` only on the **selected** color.
2. Sibling colors are stubs (`images: []`, plus `thumbnail` / `sourceUrl`).
3. One-size `TU` → Dupli1 empty `size` (`""`).
4. For one-size bags, use the **color-level** SKU, not `…_1501`.

## Images

- DAM path: `https://www.miumiu.com/content/dam/miumiubkg_products/.../<SKU>_SL*.jpg`
- Strip `/_jcr_content/renditions/...` to fetch originals (real JPEGs).
- Filter by selected SKU so sibling color thumbs on the same PDP are skipped.

## Usage

```bash
# Curated top-15 bags → images/miumiu/<SKU>/{info.json,*.jpg} + catalog.json
python3 miumiu/main.py

# Discover bag PDPs from the US bags PLP
python3 miumiu/main.py --discover

# Arbitrary PDPs
python3 miumiu/main.py -o images/miumiu <product-url> [...]

# List original image URLs only
python3 miumiu/main.py --list-only <product-url>
```

Default with no URLs is the curated **top-15 bag** list in `miumiu/main.py`.
Multi-URL runs write per-SKU subdirs under `-o` (default `./images/miumiu`).

## Dupli1 import

```bash
DUPLI1_EMAIL=agent@dupli1.com DUPLI1_PASSWORD='…' \
  python3 miumiu/import_dupli1.py
```

Creates brand `MM`, styles, variants, and uploads local JPEGs to
`manage.dupli1.com`.
