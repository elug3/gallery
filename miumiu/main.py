#!/usr/bin/env python3
"""Extract original (full-resolution) product images from a Miu Miu product page.

Miu Miu (Prada Group) serves product imagery from an Adobe AEM DAM. Each image
is exposed at a base URL such as::

    https://www.miumiu.com/content/dam/miumiubkg_products/.../<CODE>.jpg

and the page references downscaled "renditions" under::

    .../<CODE>.jpg/_jcr_content/renditions/...

Requesting the base URL returns the original asset. This script scrapes the
page, collects unique base image URLs for the selected SKU, downloads each one,
and writes a Dupli1-compatible ``info.json`` (parent product + variants).

See ``docs/miumiu-info-json.md`` for the schema and Dupli1 import mapping.

Usage::

    python3 miumiu/main.py
    python3 miumiu/main.py --discover
    python3 miumiu/main.py <product-url> [<product-url> ...]
    python3 miumiu/main.py -o images/miumiu <product-url>

Only the Python standard library is used.
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

# Curated top-15 bags from the US bags PLP / new arrivals (diverse lines).
DEFAULT_URLS = [
    "https://www.miumiu.com/us/en/p/spirit-nappa-leather-bag/5BC216_QJB_F0002_V_OOO",
    "https://www.miumiu.com/us/en/p/spirit-nappa-leather-bag/5BC217_QJB_F0BW5_V_OOO",
    "https://www.miumiu.com/us/en/p/vivant-nappa-leather-bag/5BB199_2BBL_F0D57_V_OOO",
    "https://www.miumiu.com/us/en/p/vivant-nappa-leather-bag/5BB200_2BBL_F0638_V_OOO",
    "https://www.miumiu.com/us/en/p/vivant-leather-bag/5BB196_2IER_F0002_V_OOO",
    "https://www.miumiu.com/us/en/p/ivy-nappa-leather-shopping-bag/5BG342_2BBL_F0316_V_OOO",
    "https://www.miumiu.com/us/en/p/ivy-leather-bag/5BG231_2CRW_F0046_V_MLN",
    "https://www.miumiu.com/us/en/p/ivy-raffia-handbag/5BA281_2IH6_F0B67_V_OR5",
    "https://www.miumiu.com/us/en/p/arcadie-matelasse-nappa-leather-bag/5BB142_AN88_F0D57_V_OON",
    "https://www.miumiu.com/us/en/p/wander-matelasse-nappa-leather-hobo-bag/5BC125_AN88_F0D57_V_OOY",
    "https://www.miumiu.com/us/en/p/aventure-nappa-leather-bag/5BC215_2BBL_F0NBL_V_OOO",
    "https://www.miumiu.com/us/en/p/aventure-nappa-leather-bag/5BC214_2BBL_F0316_V_OOO",
    "https://www.miumiu.com/us/en/p/vivant-leather-bag/5BB195_2IER_F0046_V_OOO",
    "https://www.miumiu.com/us/en/p/ivy-leather-bag/5BG311_2IEO_F0BW5_V_OON",
    "https://www.miumiu.com/us/en/p/raffia-effect-woven-tote-bag/5BG248_2DO3_F05DN_V_ORO",
]

BAGS_PLP_URL = "https://www.miumiu.com/us/en/bags/c/10268US"
NEW_ARRIVALS_BAGS_URL = "https://www.miumiu.com/us/en/new-arrivals/bags/c/10201US"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Product DAM assets only (skip menu / editorial imagery under /content/dam/miumiu/).
IMAGE_URL_RE = re.compile(
    r"https://www\.miumiu\.com/content/dam/miumiubkg_products/[^\s\"'<>\\,]+?\.jpg",
    re.IGNORECASE,
)

PDP_HREF_RE = re.compile(
    r"https://www\.miumiu\.com/us/en/p/([^/\"\s]+)/([0-9A-Z_]+)",
    re.IGNORECASE,
)

JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

META_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

ONE_SIZE_VALUES = frozenset({"tu", "one size", "onesize", "os", "u"})

DEFAULT_CATEGORY = "bags"
DEFAULT_STATUS = "draft"
INFO_FILENAME = "info.json"
BRAND_DEFAULT = "Miu Miu"


def fetch(url: str, timeout: int = 60) -> bytes:
    """Fetch *url* with a browser-like User-Agent and return the raw bytes."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    without_tags = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_lib.unescape(without_tags)).strip()


def filename_for(url: str) -> str:
    """Derive a sensible local filename from an image *url*."""
    return url.rsplit("/", 1)[-1]


def sku_from_url(url: str) -> str:
    """Return the trailing SKU segment of a Miu Miu PDP URL."""
    return url.rstrip("/").rsplit("/", 1)[-1]


def extract_original_image_urls(html: str, selected_sku: str = "") -> list[str]:
    """Return unique original-resolution product image URLs.

    When *selected_sku* is set, only assets whose filename contains that SKU
    are kept (sibling color thumbs are often embedded on the same PDP).
    """
    seen: set[str] = set()
    urls: list[str] = []
    sku_token = (selected_sku or "").upper()
    for match in IMAGE_URL_RE.findall(html):
        base = match.split("/_jcr_content", 1)[0]
        if sku_token and sku_token not in base.upper():
            continue
        if base not in seen:
            seen.add(base)
            urls.append(base)
    return urls


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
    """Normalize catalog price strings to a float dollars amount."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace(",", "").replace("$", "")
    if re.fullmatch(r"\d+\.\d{3}", text):
        text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def normalize_size(raw: str) -> str:
    """Map one-size labels (``TU``) to Dupli1 empty size."""
    value = (raw or "").strip()
    if value.lower() in ONE_SIZE_VALUES:
        return ""
    return value


def capacity_from_dimensions(dimensions: dict[str, str]) -> str:
    """Format dimensions as a Dupli1 ``capacity`` string."""
    order = ("height", "width", "length", "depth")
    parts = [dimensions[key] for key in order if key in dimensions]
    if not parts:
        parts = list(dimensions.values())
    return " × ".join(parts)


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


def catalog_window(html: str) -> str:
    """Return a text window around the embedded catalog ``colorVariants`` blob."""
    marker = '"colorVariants"'
    index = html.find(marker)
    if index < 0:
        index = html.find("colorVariants")
    if index < 0:
        return ""
    start = max(0, index - 40_000)
    end = min(len(html), index + 120_000)
    return html[start:end]


def absolute_miumiu_url(path_or_url: str, page_url: str) -> str:
    """Resolve a Miu Miu ``urlPath`` against the scraped page origin."""
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


def attr_value(attributes: list, *names: str) -> str:
    """Return the first attribute value matching any of *names*."""
    wanted = {name.lower() for name in names}
    for item in attributes:
        if not isinstance(item, dict):
            continue
        ident = str(item.get("identifier") or item.get("name") or "").lower()
        if ident not in wanted:
            continue
        values = item.get("values")
        if not isinstance(values, list) or not values:
            continue
        first = values[0]
        if isinstance(first, dict):
            return str(first.get("value") or first.get("identifier") or "")
        return str(first)
    return ""


def extract_catalog_payload(html: str, page_url: str) -> dict:
    """Pull color/size/details/dimensions from the embedded catalog JSON."""
    window = catalog_window(html)
    if not window:
        return {
            "colors": [],
            "sizes": [],
            "details": [],
            "dimensions": {},
            "material": "",
            "name": "",
            "short_description": "",
            "price": None,
        }

    colors = extract_json_array("colorVariants", window) or []
    sizes = extract_json_array("sizeCodes", window) or []
    attributes = extract_json_array("attributes", window) or []
    colors = [item for item in colors if isinstance(item, dict)]
    sizes = [item for item in sizes if isinstance(item, dict)]
    attributes = [item for item in attributes if isinstance(item, dict)]

    for color in colors:
        path = str(color.get("urlPath") or "")
        if path:
            color["url"] = absolute_miumiu_url(path, page_url)

    short_description = ""
    match = re.search(
        r'"shortDescription"\s*:\s*"((?:\\.|[^"\\])*)"',
        window,
    )
    if match:
        short_description = bytes(match.group(1), "utf-8").decode("unicode_escape")

    details = [
        part.strip()
        for part in short_description.split("---")
        if part.strip()
    ]

    dimensions: dict[str, str] = {}
    for key in ("height", "width", "length", "depth"):
        raw = attr_value(attributes, key)
        if raw:
            # Catalog stores bare centimeters for bags.
            dimensions[key] = f"{raw} cm" if re.fullmatch(r"\d+(?:\.\d+)?", raw) else raw

    material = attr_value(attributes, "MaterialGroup", "CLBDG")
    name_match = re.search(r'"name"\s*:\s*"((?:\\.|[^"\\])*)"', window)
    # Prefer the product name near shortDescription (avoid nav "Home").
    name = ""
    name_near = re.search(
        r'"name"\s*:\s*"((?:\\.|[^"\\])*)"\s*,\s*"onSale"',
        window,
    )
    if name_near:
        name = bytes(name_near.group(1), "utf-8").decode("unicode_escape")
    elif name_match:
        name = bytes(name_match.group(1), "utf-8").decode("unicode_escape")

    price = None
    price_match = re.search(r'"price"\s*:\s*(-?\d+(?:\.\d+)?)', window)
    if price_match:
        price = normalize_price(price_match.group(1))

    return {
        "colors": colors,
        "sizes": sizes,
        "details": details,
        "dimensions": dimensions,
        "material": material,
        "name": name,
        "short_description": short_description,
        "price": price,
    }


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
    """Build Dupli1-shaped variant rows from Miu Miu color/size options."""
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
        color_name = strip_html(
            str(color.get("colorLabelName") or color.get("color") or "")
        )
        color_sku = str(color.get("partNumber") or color.get("uniqueID") or "")
        selected = bool(color.get("isSelected")) or (
            selected_sku and color_sku == selected_sku
        ) or (
            color_name
            and color_name == selected_color
            and len(color_rows) == 1
        )
        available = color.get("isAvailable")
        if available is None:
            available = str(color.get("available", "True")).lower() in {
                "true",
                "1",
                "yes",
            }

        for size in size_rows if selected else [{"value": "", "partNumber": color_sku}]:
            size_value = normalize_size(str(size.get("value") or ""))
            if selected and size.get("partNumber"):
                sku = str(size.get("partNumber"))
            else:
                sku = color_sku
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
    catalog = extract_catalog_payload(html, page_url)
    colors = catalog["colors"]
    sizes = catalog["sizes"]

    brand = group.get("brand") or ld_variant.get("brand") or {}
    if isinstance(brand, dict):
        brand_name = brand.get("name", "") or ""
    else:
        brand_name = str(brand or "")

    selected_sku = str(
        ld_variant.get("sku")
        or ld_variant.get("mpn")
        or group.get("productGroupID")
        or sku_from_url(page_url)
    )

    selected_color = strip_html(str(ld_variant.get("color") or ""))
    for color in colors:
        if color.get("isSelected"):
            selected_color = strip_html(
                str(color.get("colorLabelName") or color.get("color") or selected_color)
            )
            selected_sku = str(color.get("partNumber") or selected_sku)
            break

    price = catalog["price"]
    if price is None:
        price = normalize_price(offers.get("price"))

    material = strip_html(
        str(ld_variant.get("material") or catalog["material"] or "")
    )
    # Prefer material cues from the product name when catalog only says "Leather".
    name = strip_html(
        str(group.get("name") or ld_variant.get("name") or catalog["name"] or "")
    )
    if material.lower() == "leather" and "nappa" in name.lower():
        material = "Nappa leather"
    elif material.lower() == "leather" and "suede" in name.lower():
        material = "Suede"
    elif not material and "raffia" in name.lower():
        material = "Raffia"

    meta = META_DESC_RE.search(html)
    description = strip_html(meta.group(1)) if meta else ""
    if not description:
        description = strip_html(str(group.get("description") or ""))

    dimensions = catalog["dimensions"]
    details = list(catalog["details"])
    if selected_sku:
        code_line = f"Product code: {selected_sku}"
        if code_line not in details:
            details.append(code_line)

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

    capacity = capacity_from_dimensions(dimensions) if dimensions else ""
    product_group_id = str(group.get("productGroupID") or "")
    if not product_group_id and selected_sku:
        # Style + material segment, e.g. 5BC216_QJB
        parts = selected_sku.split("_")
        if len(parts) >= 2:
            product_group_id = "_".join(parts[:2])

    tags = ["miumiu"]
    if DEFAULT_CATEGORY:
        tags.append(DEFAULT_CATEGORY)
    # Bag line from slug (spirit, ivy, arcadie, …).
    slug = ""
    path = urllib.parse.urlparse(page_url).path
    match = re.search(r"/p/([^/]+)/", path)
    if match:
        slug = match.group(1)
        line = slug.split("-")[0].lower()
        if line and line not in tags:
            tags.append(line)

    info = {
        "product": {
            "name": name,
            "description": description,
            "brand": brand_name or BRAND_DEFAULT,
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
        "currency": str(offers.get("priceCurrency") or "USD"),
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
    return os.path.join(base_dir, sku_from_url(url))


def extract_from_page(url: str, output_dir: str) -> list[str]:
    """Scrape *url*, write info.json, and download original product images."""
    print(f"Fetching product page: {url}")
    html = fetch(url).decode("utf-8", errors="replace")
    selected_sku = sku_from_url(url)
    image_urls = extract_original_image_urls(html, selected_sku=selected_sku)
    print(f"Found {len(image_urls)} original image(s):")
    for image_url in image_urls:
        print(f"  - {image_url}")

    info = extract_product_info(html, url, image_urls)
    # Re-filter images using the catalog-selected SKU when it differs.
    selected = info.get("selectedSku") or selected_sku
    if selected != selected_sku:
        image_urls = extract_original_image_urls(html, selected_sku=selected)
        info = extract_product_info(html, url, image_urls)

    info_path = write_info_json(info, output_dir)
    product = info.get("product") or {}
    print(f"Wrote product info: {info_path}")
    if product.get("name"):
        print(f"  name: {product['name']}")
    if info.get("selectedSku"):
        print(f"  sku:  {info['selectedSku']}")
    variants = info.get("variants") or []
    selected_variant = next((item for item in variants if item.get("selected")), None)
    if selected_variant and selected_variant.get("price") is not None:
        currency = info.get("currency") or ""
        label = f"{currency} {selected_variant['price']}".strip()
        print(f"  price: {label}")
    print(
        f"  variants: {len(variants)} "
        f"(colors={info.get('availableColors')}, sizes={info.get('availableSizes')})"
    )

    if not image_urls:
        return []
    print(f"Downloading into: {output_dir}")
    return download_images(image_urls, output_dir)


def discover_bag_urls(limit: int = 15) -> list[str]:
    """Collect bag PDP URLs from US bags PLPs (unique style codes, PLP order)."""
    pages = [BAGS_PLP_URL, NEW_ARRIVALS_BAGS_URL]
    seen_style: set[str] = set()
    urls: list[str] = []
    for page in pages:
        try:
            html = fetch(page).decode("utf-8", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError) as error:
            print(f"discover: failed {page} ({error})", file=sys.stderr)
            continue
        for slug, sku in PDP_HREF_RE.findall(html):
            style = "_".join(sku.split("_")[:2]) if "_" in sku else sku
            if style in seen_style:
                continue
            seen_style.add(style)
            urls.append(f"https://www.miumiu.com/us/en/p/{slug}/{sku}")
            if len(urls) >= limit:
                return urls
    return urls


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract original product images and Dupli1-shaped info.json "
            "from a Miu Miu product page."
        ),
    )
    parser.add_argument(
        "urls",
        nargs="*",
        default=None,
        help="Product page URL(s) to scrape (default: curated top-15 bags).",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="images/miumiu",
        help="Directory to save images and info.json (default: ./images/miumiu).",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Only print the original image URLs; do not download anything.",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Print bag PDP URLs discovered from the US bags PLP (default 15).",
    )
    parser.add_argument(
        "--discover-limit",
        type=int,
        default=15,
        help="Max URLs for --discover (default: 15).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.discover:
        for url in discover_bag_urls(limit=args.discover_limit):
            print(url)
        return 0

    urls = args.urls if args.urls else list(DEFAULT_URLS)
    total_saved = 0
    multi = len(urls) > 1
    catalog_rows: list[dict] = []

    for url in urls:
        if args.list_only:
            html = fetch(url).decode("utf-8", errors="replace")
            for image_url in extract_original_image_urls(html, sku_from_url(url)):
                print(image_url)
            continue

        target_dir = output_dir_for(url, args.output_dir, multi)
        saved = extract_from_page(url, target_dir)
        total_saved += len(saved)
        info_path = os.path.join(target_dir, INFO_FILENAME)
        if os.path.exists(info_path):
            info = json.load(open(info_path, encoding="utf-8"))
            product = info.get("product") or {}
            catalog_rows.append(
                {
                    "sku": info.get("selectedSku") or sku_from_url(url),
                    "name": product.get("name") or "",
                    "url": url,
                    "images": len(saved),
                    "dir": target_dir,
                }
            )

    if not args.list_only:
        if multi and catalog_rows:
            catalog_path = os.path.join(args.output_dir, "catalog.json")
            os.makedirs(args.output_dir, exist_ok=True)
            json.dump(
                {"brand": BRAND_DEFAULT, "top15": catalog_rows},
                open(catalog_path, "w", encoding="utf-8"),
                indent=2,
            )
            print(f"Wrote catalog: {catalog_path}")
        print(f"Done. Downloaded {total_saved} image(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
