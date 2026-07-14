#!/usr/bin/env python3
"""Extract original (full-resolution) product images from a Prada product page.

Prada serves its product imagery from an Adobe AEM DAM. Each image is exposed
at a base URL such as::

    https://www.prada.com/content/dam/pradabkg_products/.../<CODE>.jpg

and the page references downscaled "renditions" of it under::

    .../<CODE>.jpg/_jcr_content/renditions/cq5dam.web.hebebed.<W>.<H>.jpg

Requesting the base URL (the part up to and including the first ``.jpg``)
returns the original, highest-resolution asset. This script scrapes the page,
collects every unique base image URL, downloads each one, and writes product
metadata to ``info.json`` in the output directory.

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
import urllib.request

DEFAULT_URL = (
    "https://www.prada.com/ww/en/p/small-re-nylon-backpack/"
    "1BZ677_RV44_F0002_V_OOO"
)

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


def matching_variant(group: dict, page_url: str) -> dict | None:
    """Pick the variant whose URL/SKU matches *page_url*, else the first one."""
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


def normalize_price(raw: object) -> str:
    """Normalize schema.org price strings such as ``2.850`` or ``2850``."""
    if raw is None or raw == "":
        return ""
    text = str(raw).strip()
    if re.fullmatch(r"\d+\.\d{3}", text):
        # European thousands separator, e.g. "2.850" -> "2850"
        return text.replace(".", "")
    if re.fullmatch(r"\d+[.,]\d{2}", text):
        return text.replace(",", ".")
    return text


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


def extract_product_info(html: str, page_url: str, image_urls: list[str]) -> dict:
    """Build a product info dict from JSON-LD and page markup."""
    group = parse_json_ld(html) or {}
    variant = matching_variant(group, page_url) or {}
    if not variant and group.get("@type") == "Product":
        variant = group

    offers = variant.get("offers") if isinstance(variant.get("offers"), dict) else {}
    details, dimensions, page_material = extract_page_details(html)

    brand = group.get("brand") or variant.get("brand") or {}
    brand_name = brand.get("name", "") if isinstance(brand, dict) else str(brand or "")

    sku = str(variant.get("sku") or group.get("productGroupID") or "")
    if not sku:
        for item in details:
            if item.lower().startswith("product code:"):
                sku = item.split(":", 1)[1].strip()
                break

    info = {
        "name": strip_html(str(group.get("name") or variant.get("name") or "")),
        "description": strip_html(str(group.get("description") or "")),
        "sku": sku,
        "url": page_url,
        "brand": brand_name,
        "color": strip_html(str(variant.get("color") or "")),
        "material": strip_html(str(variant.get("material") or page_material or "")),
        "price": normalize_price(offers.get("price")),
        "currency": str(offers.get("priceCurrency") or ""),
        "dimensions": dimensions,
        "details": details,
        "images": [filename_for(url) for url in image_urls],
        "image_urls": list(image_urls),
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
    print(f"Wrote product info: {info_path}")
    if info.get("name"):
        print(f"  name: {info['name']}")
    if info.get("sku"):
        print(f"  sku:  {info['sku']}")
    if info.get("price"):
        currency = info.get("currency") or ""
        print(f"  price: {currency} {info['price']}".strip())

    if not image_urls:
        return []
    print(f"Downloading into: {output_dir}")
    return download_images(image_urls, output_dir)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract original product images and info.json from a Prada "
            "product page."
        ),
    )
    parser.add_argument(
        "urls",
        nargs="*",
        default=[DEFAULT_URL],
        help=f"Product page URL(s) to scrape (default: {DEFAULT_URL}).",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="images",
        help="Directory to save downloaded images and info.json (default: ./images).",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Only print the original image URLs; do not download anything.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    total_saved = 0
    multi = len(args.urls) > 1
    for url in args.urls:
        if args.list_only:
            html = fetch(url).decode("utf-8", errors="replace")
            for image_url in extract_original_image_urls(html):
                print(image_url)
        else:
            target_dir = output_dir_for(url, args.output_dir, multi)
            saved = extract_from_page(url, target_dir)
            total_saved += len(saved)
    if not args.list_only:
        print(f"Done. Downloaded {total_saved} image(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
