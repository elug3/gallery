#!/usr/bin/env python3
"""Extract original (full-resolution) product images from a Prada product page.

Prada serves its product imagery from an Adobe AEM DAM. Each image is exposed
at a base URL such as::

    https://www.prada.com/content/dam/pradabkg_products/.../<CODE>.jpg

and the page references downscaled "renditions" of it under::

    .../<CODE>.jpg/_jcr_content/renditions/cq5dam.web.hebebed.<W>.<H>.jpg

Requesting the base URL (the part up to and including the first ``.jpg``)
returns the original, highest-resolution asset. This script scrapes the page,
collects every unique base image URL, downloads each one, and writes a Dupli1-
compatible ``info.json`` (parent product + variants) in the output directory.

See ``docs/prada-info-json.md`` for the schema and Dupli1 import mapping.

Usage::

    python prada/main.py
    python prada/main.py <product-url> [<product-url> ...]
    python prada/main.py -o images <product-url>

Only the Python standard library is used, so no extra dependencies are needed.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = (
    "https://www.prada.com/ww/en/p/small-re-nylon-backpack/"
    "1BZ677_RV44_F0002_V_OOO"
)

BAGS_PLP = "https://www.prada.com/ww/en/womens/bags/c/10062EU"
PRODUCT_SITEMAP = "https://www.prada.com/sitemap_product_US_en_0.xml"
DEFAULT_LIMIT = 20

# Curated iconic / high-visibility bag styles (filled from PLP + sitemap).
DEFAULT_TOP_URLS = [
    "https://www.prada.com/us/en/p/prada-re-edition-1978-small-re-nylon-backpack/1BZ677_RV44_F0Y8C_V_OOM",
    "https://www.prada.com/us/en/p/prada-galleria-medium-saffiano-leather-bag/1BA457_NZV_F0032_V_EOM",
    "https://www.prada.com/us/en/p/prada-galleria-large-saffiano-leather-bag/1BA274_NZV_F0009_V_DOO",
    "https://www.prada.com/us/en/p/prada-re-edition-2005-re-nylon-and-saffiano-leather-bag-with-charm/1BH204_R064_F0NIV_V_WRA",
    "https://www.prada.com/us/en/p/prada-re-edition-mini-saffiano-leather-bag/1BC204_NZV_F0MUH_V_QOM",
    "https://www.prada.com/us/en/p/prada-buckle-small-leather-bag-with-belt/1BA502_2CY9_F0002_V_OBO",
    "https://www.prada.com/us/en/p/prada-bonnie-medium-leather-bag/1BA426_2CYR_F05VJ_V_MOM",
    "https://www.prada.com/us/en/p/prada-bonnie-large-leather-handbag/1BA433_2CYR_F05VJ_V_MOM",
    "https://www.prada.com/us/en/p/prada-bonnie-leather-mini-bag/1BA486_2CYR_F05VJ_V_OOM",
    "https://www.prada.com/us/en/p/prada-buckle-large-leather-handbag-with-belt-/1BA416_2CY9_F0002_V_DBO",
    "https://www.prada.com/us/en/p/large-linen-blend-and-leather-tote-bag/1BG659_2DLI_F0N67_V_OOO",
    "https://www.prada.com/us/en/p/prada-jardiniere-small-cotton-canvas-bag/1BG464_RCYA_F0018_V_8OK",
    "https://www.prada.com/us/en/p/prada-jardiniere-large-cotton-canvas-handbag/1BG554_RCYA_F0018_V_8OK",
    "https://www.prada.com/us/en/p/prada-re-edition-1978-medium-re-nylon-and-saffiano-leather-tote-bag/1BG555_R064_F0134_V_OOO",
    "https://www.prada.com/us/en/p/prada-re-edition-1978-large-re-nylon-and-saffiano-leather-tote-bag/1BG527_R064_F0002_V_OOO",
    "https://www.prada.com/us/en/p/re-nylon-backpack/1BZ039_RV44_F0002_V_DMM",
    "https://www.prada.com/us/en/p/small-re-nylon-backpack/1BZ081_RV44_F0632_V_OOO",
    "https://www.prada.com/us/en/p/prada-galleria-mini-saffiano-leather-bag/1BA916_NZV_F0K74_V_EOO",
    "https://www.prada.com/us/en/p/prada-buckle-small-leather-handbag-with-double-belt/1BA418_2CYS_F0002_V_OOO",
    "https://www.prada.com/us/en/p/prada-bonnie-extra-large-leather-bag/1BA439_2CYR_F05VJ_V_OOM",
]

# A desktop browser User-Agent; the site returns a stripped-down response to
# unknown clients.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Matches a Prada DAM image URL up to (and including) the first ".jpg". The
# non-greedy quantifier stops at the base asset rather than the rendition path
# that follows it (".../foo.jpg/_jcr_content/renditions/...jpg").
IMAGE_URL_RE = re.compile(
    r"https://www\.prada\.com/content/dam/pradabkg_products/[^\s\"'<>\\,]+?\.jpg",
    re.IGNORECASE,
)

JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

DETAILS_BLOCK_RE = re.compile(
    r"<ul[^>]*>(.*?)<li>\s*Imported\s*</li>\s*</ul>"
    r".*?Dimensions</p>\s*<ul[^>]*>(.*?)</ul>",
    re.DOTALL | re.IGNORECASE,
)

LIST_ITEM_RE = re.compile(r"<li>(.*?)</li>", re.DOTALL | re.IGNORECASE)

MAIN_MATERIAL_RE = re.compile(
    r"Main material:\s*([^<]+)",
    re.IGNORECASE,
)

# Prada uses "TU" (taglia unica) for one-size bags; Dupli1 bags use "".
ONE_SIZE_VALUES = frozenset({"tu", "one size", "onesize", "os", "u"})

DEFAULT_CATEGORY = "bags"
DEFAULT_STATUS = "draft"
INFO_FILENAME = "info.json"


def fetch(url: str, timeout: int = 30) -> bytes:
    """Fetch *url* with a browser-like User-Agent and return the raw bytes."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    without_tags = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_lib.unescape(without_tags)).strip()


def extract_original_image_urls(html: str) -> list[str]:
    """Return the unique, original-resolution image URLs found in *html*.

    Order of first appearance is preserved so the result is deterministic.
    """
    seen: set[str] = set()
    urls: list[str] = []
    for match in IMAGE_URL_RE.findall(html):
        if match not in seen:
            seen.add(match)
            urls.append(match)
    return urls


def filename_for(url: str) -> str:
    """Derive a sensible local filename from an image *url*."""
    return url.rsplit("/", 1)[-1]


def parse_json_ld(html: str) -> dict | None:
    """Return the first Product / ProductGroup JSON-LD object, if any."""
    for raw in JSON_LD_RE.findall(html):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_type = node.get("@type", "")
            types = node_type if isinstance(node_type, list) else [node_type]
            if any(t in {"Product", "ProductGroup"} for t in types):
                return node
    return None


def matching_ld_variant(group: dict, page_url: str) -> dict | None:
    """Pick the JSON-LD variant whose URL/SKU matches *page_url*."""
    variants = group.get("hasVariant")
    if not isinstance(variants, list):
        return None
    page_url = page_url.rstrip("/")
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        variant_url = str(variant.get("url", "")).rstrip("/")
        sku = str(variant.get("sku", ""))
        if variant_url == page_url or (sku and sku in page_url):
            return variant
    for variant in variants:
        if isinstance(variant, dict):
            return variant
    return None


def normalize_price(raw: object) -> float | None:
    """Normalize schema.org / catalog price strings to a float dollars amount."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace(",", "").replace("$", "")
    if re.fullmatch(r"\d+\.\d{3}", text):
        # European thousands separator, e.g. "2.850" -> 2850
        text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def parse_dimensions(items: list[str]) -> dict[str, str]:
    """Turn ``['Height: 12 cm', ...]`` into ``{'height': '12 cm', ...}``."""
    dimensions: dict[str, str] = {}
    for item in items:
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key and value:
            dimensions[key] = value
    return dimensions


def capacity_from_dimensions(dimensions: dict[str, str]) -> str:
    """Format dimensions as a Dupli1 ``capacity`` string."""
    order = ("height", "width", "length", "depth")
    parts = [dimensions[key] for key in order if key in dimensions]
    if not parts:
        parts = list(dimensions.values())
    return " × ".join(parts)


def normalize_size(raw: str) -> str:
    """Map Prada one-size labels (``TU``) to Dupli1 empty size."""
    value = (raw or "").strip()
    if value.lower() in ONE_SIZE_VALUES:
        return ""
    return value


def extract_page_details(html: str) -> tuple[list[str], dict[str, str], str]:
    """Return (details bullets, dimensions, main material) from page HTML."""
    details: list[str] = []
    dimensions: dict[str, str] = {}
    material = ""

    match = DETAILS_BLOCK_RE.search(html)
    if match:
        details = [strip_html(item) for item in LIST_ITEM_RE.findall(match.group(1))]
        details = [item for item in details if item]
        dim_items = [strip_html(item) for item in LIST_ITEM_RE.findall(match.group(2))]
        dimensions = parse_dimensions([item for item in dim_items if item])

    material_match = MAIN_MATERIAL_RE.search(html)
    if material_match:
        material = strip_html(material_match.group(1))

    return details, dimensions, material


def extract_json_array(key: str, text: str) -> list | None:
    """Extract a JSON array value for *key* from *text* via bracket matching."""
    match = re.search(rf'"{re.escape(key)}"\s*:\s*\[', text)
    if not match:
        return None
    start = match.end() - 1
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return data if isinstance(data, list) else None
    return None


def decode_catalog_window(html: str) -> str:
    """Return a URL-decoded window around the catalog ``colorVariants`` blob."""
    marker = "colorVariants%22"
    index = html.find(marker)
    if index < 0:
        index = html.find("colorVariants")
    if index < 0:
        return ""
    start = max(0, index - 30_000)
    end = min(len(html), index + 100_000)
    return urllib.parse.unquote(html[start:end])


def absolute_prada_url(path_or_url: str, page_url: str) -> str:
    """Resolve a Prada ``urlPath`` against the scraped page origin."""
    value = (path_or_url or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value
    parsed = urllib.parse.urlparse(page_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if not value.startswith("/"):
        value = "/" + value
    return origin + value


def extract_catalog_variants(html: str, page_url: str) -> tuple[list[dict], list[dict]]:
    """Return (colorVariants, sizeCodes) from the embedded catalog payload."""
    window = decode_catalog_window(html)
    if not window:
        return [], []
    colors = extract_json_array("colorVariants", window) or []
    sizes = extract_json_array("sizeCodes", window) or []
    colors = [item for item in colors if isinstance(item, dict)]
    sizes = [item for item in sizes if isinstance(item, dict)]
    for color in colors:
        path = str(color.get("urlPath") or "")
        if path:
            color["url"] = absolute_prada_url(path, page_url)
    return colors, sizes


def build_variants(
    *,
    colors: list[dict],
    sizes: list[dict],
    selected_sku: str,
    selected_color: str,
    price: float | None,
    image_files: list[str],
    image_urls: list[str],
    page_url: str,
) -> list[dict]:
    """Build Dupli1-shaped variant rows from Prada color/size options.

    Full ``images`` are attached only to the scraped (selected) color. Sibling
    colors are emitted as stubs so an importer knows which URLs to scrape next.
    """
    size_rows = sizes or [{"value": "", "partNumber": selected_sku or ""}]
    variants: list[dict] = []

    if colors:
        color_rows = colors
    else:
        color_rows = [
            {
                "color": selected_color,
                "partNumber": selected_sku,
                "isSelected": True,
                "isAvailable": True,
                "url": page_url,
                "hexCode": "",
            }
        ]

    for color in color_rows:
        color_name = strip_html(str(color.get("colorLabelName") or color.get("color") or ""))
        color_sku = str(color.get("partNumber") or color.get("uniqueID") or "")
        selected = bool(color.get("isSelected")) or (
            selected_sku and color_sku == selected_sku
        ) or (color_name and color_name == selected_color and len(color_rows) == 1)
        available = color.get("isAvailable")
        if available is None:
            available = str(color.get("available", "True")).lower() in {"true", "1", "yes"}

        for size in size_rows if selected else [{"value": "", "partNumber": color_sku}]:
            size_value = normalize_size(str(size.get("value") or ""))
            if selected and size.get("partNumber"):
                sku = str(size.get("partNumber"))
            else:
                sku = color_sku
            # Prefer the color-level SKU for bags (Dupli1 cart key); keep size
            # SKU only when it encodes a real size option.
            if selected and size_value == "" and color_sku:
                sku = color_sku

            inventory = str(size.get("inventoryStatus") or "")
            size_available = True
            if inventory:
                size_available = inventory.lower() == "available"

            variant: dict = {
                "sku": sku,
                "color": color_name,
                "size": size_value,
                "price": price,
                "status": "active" if (available and size_available) else "draft",
                "images": list(image_files) if selected else [],
                "imageUrls": list(image_urls) if selected else [],
                "selected": bool(selected),
                "available": bool(available and size_available),
                "sourceUrl": str(color.get("url") or (page_url if selected else "")),
            }
            hex_code = str(color.get("hexCode") or color.get("colorValue") or "")
            if hex_code:
                variant["hex"] = hex_code
            thumbnail = str(color.get("thumbnail") or color.get("fullImage") or "")
            if thumbnail and not selected:
                variant["thumbnail"] = thumbnail
            variants.append(variant)

    return variants


def extract_product_info(html: str, page_url: str, image_urls: list[str]) -> dict:
    """Build a Dupli1-oriented info.json payload from the product page."""
    group = parse_json_ld(html) or {}
    ld_variant = matching_ld_variant(group, page_url) or {}
    if not ld_variant and group.get("@type") == "Product":
        ld_variant = group

    offers = (
        ld_variant.get("offers") if isinstance(ld_variant.get("offers"), dict) else {}
    )
    details, dimensions, page_material = extract_page_details(html)
    colors, sizes = extract_catalog_variants(html, page_url)

    brand = group.get("brand") or ld_variant.get("brand") or {}
    brand_name = brand.get("name", "") if isinstance(brand, dict) else str(brand or "")

    selected_sku = str(ld_variant.get("sku") or group.get("productGroupID") or "")
    if not selected_sku:
        for item in details:
            if item.lower().startswith("product code:"):
                selected_sku = item.split(":", 1)[1].strip()
                break
    if not selected_sku:
        selected_sku = page_url.rstrip("/").rsplit("/", 1)[-1]

    selected_color = strip_html(str(ld_variant.get("color") or ""))
    for color in colors:
        if color.get("isSelected"):
            selected_color = strip_html(
                str(color.get("colorLabelName") or color.get("color") or selected_color)
            )
            selected_sku = str(color.get("partNumber") or selected_sku)
            break

    price = normalize_price(offers.get("price"))
    material = strip_html(str(ld_variant.get("material") or page_material or ""))
    image_files = [filename_for(url) for url in image_urls]
    variants = build_variants(
        colors=colors,
        sizes=sizes,
        selected_sku=selected_sku,
        selected_color=selected_color,
        price=price,
        image_files=image_files,
        image_urls=image_urls,
        page_url=page_url,
    )

    available_colors: list[str] = []
    for variant in variants:
        color = variant.get("color") or ""
        if color and color not in available_colors:
            available_colors.append(color)

    available_sizes: list[str] = []
    for variant in variants:
        if not variant.get("selected"):
            continue
        size = variant.get("size") or ""
        if size and size not in available_sizes:
            available_sizes.append(size)

    name = strip_html(str(group.get("name") or ld_variant.get("name") or ""))
    description = strip_html(str(group.get("description") or ""))
    capacity = capacity_from_dimensions(dimensions) if dimensions else ""
    product_group_id = str(group.get("productGroupID") or "")

    tags = ["prada"]
    if DEFAULT_CATEGORY:
        tags.append(DEFAULT_CATEGORY)

    info = {
        "product": {
            "name": name,
            "description": description,
            "brand": brand_name or "Prada",
            "material": material,
            "category": DEFAULT_CATEGORY,
            "capacity": capacity,
            "status": DEFAULT_STATUS,
            "tags": tags,
            "sourceUrl": page_url,
        },
        "variants": variants,
        "availableColors": available_colors,
        "availableSizes": available_sizes,
        "details": details,
        "dimensions": dimensions,
        "currency": str(offers.get("priceCurrency") or ""),
        "productGroupId": product_group_id,
        "selectedSku": selected_sku,
    }
    return info


def write_info_json(info: dict, output_dir: str) -> str:
    """Write *info* as ``info.json`` under *output_dir* and return its path."""
    os.makedirs(output_dir, exist_ok=True)
    dest = os.path.join(output_dir, INFO_FILENAME)
    with open(dest, "w", encoding="utf-8") as handle:
        json.dump(info, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return dest


def download_images(urls: list[str], output_dir: str) -> list[str]:
    """Download each URL into *output_dir*, returning the saved file paths."""
    os.makedirs(output_dir, exist_ok=True)
    saved: list[str] = []
    for index, url in enumerate(urls, start=1):
        name = filename_for(url)
        dest = os.path.join(output_dir, name)
        try:
            data = fetch(url)
        except (urllib.error.URLError, urllib.error.HTTPError) as error:
            print(f"  [{index}/{len(urls)}] FAILED {url} ({error})", file=sys.stderr)
            continue
        with open(dest, "wb") as handle:
            handle.write(data)
        print(f"  [{index}/{len(urls)}] saved {dest} ({len(data):,} bytes)")
        saved.append(dest)
    return saved


def output_dir_for(url: str, base_dir: str, multi: bool) -> str:
    """Return a per-SKU subdirectory when scraping multiple URLs."""
    if not multi:
        return base_dir
    sku = url.rstrip("/").rsplit("/", 1)[-1]
    return os.path.join(base_dir, sku)


def extract_from_page(url: str, output_dir: str) -> list[str]:
    """Scrape *url*, write info.json, and download original product images."""
    print(f"Fetching product page: {url}")
    html = fetch(url).decode("utf-8", errors="replace")
    image_urls = extract_original_image_urls(html)
    print(f"Found {len(image_urls)} original image(s):")
    for image_url in image_urls:
        print(f"  - {image_url}")

    info = extract_product_info(html, url, image_urls)
    info_path = write_info_json(info, output_dir)
    product = info.get("product") or {}
    print(f"Wrote product info: {info_path}")
    if product.get("name"):
        print(f"  name: {product['name']}")
    if info.get("selectedSku"):
        print(f"  sku:  {info['selectedSku']}")
    variants = info.get("variants") or []
    selected = next((item for item in variants if item.get("selected")), None)
    if selected and selected.get("price") is not None:
        currency = info.get("currency") or ""
        label = f"{currency} {selected['price']}".strip()
        print(f"  price: {label}")
    print(
        f"  variants: {len(variants)} "
        f"(colors={info.get('availableColors')}, sizes={info.get('availableSizes')})"
    )

    if not image_urls:
        return []
    print(f"Downloading into: {output_dir}")
    return download_images(image_urls, output_dir)


def discover_urls(limit: int = DEFAULT_LIMIT) -> list[str]:
    """Return unique bag PDP URLs from the bags PLP, then sitemap fill."""
    urls: list[str] = []
    seen_style: set[str] = set()

    def add(url: str) -> None:
        url = url.split("?")[0].replace("/ww/en/", "/us/en/")
        if "/p/" not in url:
            return
        low = url.lower()
        if any(
            bad in low
            for bad in (
                "crocodile",
                "ostrich",
                "python",
                "cap/",
                "hat",
                "pants",
                "shoe",
                "belt/",
            )
        ):
            return
        sku = url.rstrip("/").rsplit("/", 1)[-1]
        style = sku.split("_")[0]
        if style in seen_style:
            return
        seen_style.add(style)
        urls.append(url)

    try:
        html = fetch(BAGS_PLP).decode("utf-8", errors="replace")
        for href in re.findall(
            r'https://www\.prada\.com/(?:ww|us)/en/p/[^"\']+', html
        ):
            add(href)
            if len(urls) >= limit:
                return urls
    except Exception as error:  # noqa: BLE001
        print(f"PLP discover failed: {error}", file=sys.stderr)

    for url in DEFAULT_TOP_URLS:
        add(url)
        if len(urls) >= limit:
            return urls

    try:
        xml = fetch(PRODUCT_SITEMAP, timeout=90).decode("utf-8", errors="replace")
        bag_kw = (
            "bag",
            "backpack",
            "tote",
            "handbag",
            "hobo",
            "clutch",
            "galleria",
            "re-edition",
            "cleo",
        )
        for loc in re.findall(r"<loc>([^<]+)</loc>", xml):
            low = loc.lower()
            if not any(k in low for k in bag_kw):
                continue
            add(loc)
            if len(urls) >= limit:
                break
    except Exception as error:  # noqa: BLE001
        print(f"Sitemap discover failed: {error}", file=sys.stderr)

    return urls[:limit]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract original product images and Dupli1-shaped info.json "
            "from a Prada product page."
        ),
    )
    parser.add_argument(
        "urls",
        nargs="*",
        help="Product page URL(s) to scrape. Default: curated top bags.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="images/prada",
        help="Directory to save downloaded images and info.json (default: ./images/prada).",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Only print the original image URLs; do not download anything.",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Print discovered bag PDP URLs and exit.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Max products for default/discover runs (default: {DEFAULT_LIMIT}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.discover:
        for url in discover_urls(args.limit):
            print(url)
        return 0

    urls = args.urls or discover_urls(args.limit) or DEFAULT_TOP_URLS[: args.limit]
    total_saved = 0
    catalog = []
    multi = len(urls) > 1
    for url in urls:
        if args.list_only:
            html = fetch(url).decode("utf-8", errors="replace")
            for image_url in extract_original_image_urls(html):
                print(image_url)
            continue
        target_dir = output_dir_for(url, args.output_dir, multi)
        try:
            saved = extract_from_page(url, target_dir)
            total_saved += len(saved)
            sku = url.rstrip("/").rsplit("/", 1)[-1]
            info_path = os.path.join(target_dir, INFO_FILENAME)
            name = ""
            if os.path.exists(info_path):
                info = json.load(open(info_path, encoding="utf-8"))
                name = (info.get("product") or {}).get("name") or ""
            catalog.append(
                {
                    "sku": sku,
                    "name": name,
                    "url": url,
                    "images": len(saved),
                    "dir": target_dir,
                }
            )
        except Exception as error:  # noqa: BLE001
            print(f"FAIL {url}: {error}", file=sys.stderr)
    if not args.list_only:
        os.makedirs(args.output_dir, exist_ok=True)
        with open(
            os.path.join(args.output_dir, "catalog.json"), "w", encoding="utf-8"
        ) as handle:
            json.dump({"brand": "Prada", "top": catalog}, handle, indent=2)
            handle.write("\n")
        print(f"Done. {len(catalog)}/{len(urls)} products, {total_saved} image(s).")
    return 0 if (args.list_only or catalog) else 2


if __name__ == "__main__":
    raise SystemExit(main())
