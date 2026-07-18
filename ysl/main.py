#!/usr/bin/env python3
"""Scrape Saint Laurent / YSL US bag PDPs into info.json + images.

Live pages are often Akamai-blocked; prefer Chrome CDP when available.
Offline fallback: ``--from-html-dir`` parses saved listing/PDP ``__NEXT_DATA__``
and downloads Kering DAM eCom JPEGs (reachable via urllib).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from shared.cdp import fetch_html  # noqa: E402
from shared.http_util import (  # noqa: E402
    build_info,
    download_image,
    normalize_price,
    normalize_size,
    strip_html,
    write_info_json,
)

BRAND = "Saint Laurent"
REFERER = "https://www.ysl.com/"
LISTING_URL = "https://www.ysl.com/en-us/shop-women/handbags"
DEFAULT_LIMIT = 18

DEFAULT_URLS = [
    "https://www.ysl.com/en-us/pr/mombasa-small-in-leather-851432AAGWJ1000.html",
    "https://www.ysl.com/en-us/pr/mombasa-medium-in-leather-862029AAGWJ1000.html",
    "https://www.ysl.com/en-us/pr/mombasa-large-in-leather-A0011MAAGWK2050.html",
    "https://www.ysl.com/en-us/pr/icarino-in-quilted-nappa-851689AAANG1000.html",
    "https://www.ysl.com/en-us/pr/icare-hobo-in-quilted-nappa-858160AAANG1000.html",
    "https://www.ysl.com/en-us/pr/icare-medium-in-quilted-nappa-871494AAANG1000.html",
    "https://www.ysl.com/en-us/pr/icare-hobo-in-raffia-868740GAAGT2791.html",
    "https://www.ysl.com/en-us/pr/icare-medium-in-raffia-868748GAAGV2791.html",
]


def parse_next_data(html: str) -> dict:
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.S,
    )
    if not match:
        raise RuntimeError("__NEXT_DATA__ missing")
    return json.loads(match.group(1))


def ecom_image_url(url: str) -> str:
    url = url.split("?")[0]
    for old, new in (
        ("/Small_thumbnail/", "/eCom/"),
        ("/Thumbnail/", "/eCom/"),
        ("/Small/", "/eCom/"),
        ("/Medium2/", "/eCom/"),
        ("/Medium/", "/eCom/"),
        ("/Large/", "/eCom/"),
    ):
        url = url.replace(old, new)
    return url


def sku_from_url(url: str) -> str:
    leaf = url.rstrip("/").rsplit("/", 1)[-1].replace(".html", "")
    match = re.search(r"([A-Z0-9]{8,})$", leaf, re.I)
    return match.group(1) if match else leaf


def parse_dimensions(short_description: list) -> dict[str, str]:
    dimensions: dict[str, str] = {}
    for item in short_description or []:
        text = strip_html(str(item))
        if text.lower().startswith("dimensions:"):
            dimensions["raw"] = text.split(":", 1)[1].strip()
    return dimensions


def product_images(product: dict) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for item in product.get("images") or []:
        src = ""
        if isinstance(item, dict):
            src = str(item.get("src") or "")
        elif isinstance(item, str):
            src = item
        if not src:
            continue
        src = ecom_image_url(src)
        # Skip video poster placeholders (often 404 as still JPEGs).
        if re.search(r'_Y\.jpg$', src, re.I):
            continue
        if src not in seen:
            seen.add(src)
            urls.append(src)
    return urls


def normalize_listing_product(product: dict, price: dict | None = None) -> dict:
    sku = str(product.get("id") or product.get("smcId") or "")
    path = str(product.get("url") or product.get("smcUrl") or "")
    url = urllib.parse.urljoin("https://www.ysl.com", path) if path else ""
    price = price or {}
    short = product.get("shortDescription") or []
    if not isinstance(short, list):
        short = []
    details = [strip_html(str(item)) for item in short if item]
    if product.get("madeIn"):
        details.append(f"Made in: {strip_html(str(product['madeIn']))}")
    if sku:
        details.append(f"Product code: {sku}")
    return {
        "sku": sku,
        "name": strip_html(str(product.get("name") or "")),
        "color": strip_html(
            str(
                product.get("macroColor")
                or product.get("microColor")
                or product.get("color")
                or ""
            )
        ),
        "price": normalize_price(
            price.get("salePriceValue")
            or price.get("salePrice")
            or price.get("listPriceValue")
        ),
        "currency": str(price.get("currencyCode") or "USD"),
        "material": strip_html(
            str(
                product.get("compositionDetailsDisplay")
                or product.get("compositions")
                or ""
            )
        ),
        "description": strip_html(str(product.get("description") or "")),
        "dimensions": parse_dimensions(short),
        "details": details,
        "url": url,
        "images": product_images(product),
        "master_id": str(product.get("masterId") or sku[:12]),
    }


def load_products_from_html_dir(html_dir: str, limit: int) -> list[dict]:
    by_sku: dict[str, dict] = {}
    for name in sorted(os.listdir(html_dir)):
        if not name.endswith(".html"):
            continue
        path = os.path.join(html_dir, name)
        html = open(path, encoding="utf-8", errors="ignore").read()
        try:
            data = parse_next_data(html)
        except RuntimeError:
            continue
        page_props = (data.get("props") or {}).get("pageProps") or {}
        results = page_props.get("results") or {}
        products = results.get("products") or []
        prices = {
            str(item.get("id") or item.get("smcId")): item
            for item in (results.get("prices") or [])
            if isinstance(item, dict)
        }
        if page_props.get("product"):
            pdp = normalize_listing_product(
                page_props["product"],
                page_props.get("price") if isinstance(page_props.get("price"), dict) else None,
            )
            if pdp["sku"]:
                existing = by_sku.get(pdp["sku"])
                # Prefer PDP (richer description / dimensions / material)
                if not existing or (
                    len(pdp.get("description") or "")
                    > len(existing.get("description") or "")
                ):
                    by_sku[pdp["sku"]] = pdp
        for product in products:
            if not isinstance(product, dict):
                continue
            sku = str(product.get("id") or product.get("smcId") or "")
            if not sku:
                continue
            normalized = normalize_listing_product(product, prices.get(sku))
            existing = by_sku.get(sku)
            if not existing:
                by_sku[sku] = normalized
            else:
                # Fill missing price/images from listing if PDP lacked them
                if not existing.get("price") and normalized.get("price"):
                    existing["price"] = normalized["price"]
                if not existing.get("images") and normalized.get("images"):
                    existing["images"] = normalized["images"]
                if not existing.get("url") and normalized.get("url"):
                    existing["url"] = normalized["url"]

    ranked = sorted(
        by_sku.values(),
        key=lambda item: (
            0 if item.get("images") else 1,
            0 if item.get("price") is not None else 1,
            item.get("name") or "",
            item.get("sku") or "",
        ),
    )
    selected: list[dict] = []
    seen_names: set[str] = set()
    for product in ranked:
        if not product.get("images"):
            continue
        key = re.sub(r"\s+", " ", (product.get("name") or "").lower())
        # Prefer one colorway per model name first for variety
        base = re.sub(r"\s+in\s+.*$", "", key).strip()
        if base in seen_names and len(selected) < max(8, limit // 2):
            continue
        seen_names.add(base)
        selected.append(product)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for product in ranked:
            if product in selected or not product.get("images"):
                continue
            selected.append(product)
            if len(selected) >= limit:
                break
    return selected[:limit]


def discover_urls(limit: int = DEFAULT_LIMIT) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for page in (1, 2, 3):
        listing = LISTING_URL if page == 1 else f"{LISTING_URL}?page={page}"
        try:
            html = fetch_html(listing, settle=3.5)
            if "Access Denied" in html:
                print(f"discover page={page} Access Denied", file=sys.stderr)
                continue
            data = parse_next_data(html)
        except Exception as error:  # noqa: BLE001
            print(f"discover page={page} failed: {error}", file=sys.stderr)
            continue
        products = (
            ((data.get("props") or {}).get("pageProps") or {}).get("results") or {}
        ).get("products") or []
        for product in products:
            if not isinstance(product, dict):
                continue
            path = str(product.get("url") or "")
            if not path:
                continue
            full = urllib.parse.urljoin("https://www.ysl.com", path).split("?")[0]
            if full in seen:
                continue
            seen.add(full)
            urls.append(full)
            if len(urls) >= limit:
                return urls
    return urls or list(DEFAULT_URLS)[:limit]


def write_product(product: dict, output_dir: str) -> dict:
    sku = product["sku"]
    image_urls = list(dict.fromkeys(product.get("images") or []))
    os.makedirs(output_dir, exist_ok=True)
    image_files: list[str] = []
    saved_urls: list[str] = []
    for index, image_url in enumerate(image_urls, start=1):
        dest = download_image(
            image_url,
            os.path.join(output_dir, f"image_{index:02d}"),
            referer=REFERER,
        )
        if not dest:
            print(f"  [{index}/{len(image_urls)}] FAILED {image_url}", file=sys.stderr)
            continue
        image_files.append(os.path.basename(dest))
        saved_urls.append(image_url)
        print(f"  [{index}/{len(image_urls)}] saved {dest}")

    details = list(product.get("details") or [])
    if sku and f"Product code: {sku}" not in details:
        details.append(f"Product code: {sku}")
    variant = {
        "sku": sku,
        "color": product.get("color") or "",
        "size": normalize_size(""),
        "price": product.get("price"),
        "status": "active",
        "images": image_files,
        "imageUrls": saved_urls,
        "selected": True,
        "available": True,
        "sourceUrl": product.get("url") or "",
    }
    info = build_info(
        name=product.get("name") or sku,
        description=product.get("description") or "",
        brand=BRAND,
        material=product.get("material") or "",
        capacity=(product.get("dimensions") or {}).get("raw", ""),
        source_url=product.get("url") or "",
        tags=["ysl", "saint-laurent", "bags"],
        variants=[variant],
        details=details,
        dimensions=product.get("dimensions") or {},
        currency=str(product.get("currency") or "USD"),
        product_group_id=str(product.get("master_id") or sku[:12]),
        selected_sku=sku,
    )
    write_info_json(info, output_dir)
    print(
        f"  wrote info.json name={product.get('name')!r} sku={sku} "
        f"price={product.get('price')} images={len(image_files)}"
    )
    return {
        "sku": sku,
        "name": product.get("name"),
        "url": product.get("url"),
        "images": len(image_files),
        "dir": output_dir,
    }


def scrape_one(url: str, output_dir: str) -> dict:
    print(f"Fetching {url}")
    html = fetch_html(url, settle=3.0)
    if "Access Denied" in html:
        raise RuntimeError("Access Denied (Akamai)")
    data = parse_next_data(html)
    page_props = (data.get("props") or {}).get("pageProps") or {}
    product = normalize_listing_product(
        page_props.get("product") or {},
        page_props.get("price") if isinstance(page_props.get("price"), dict) else None,
    )
    if not product.get("sku"):
        product["sku"] = sku_from_url(url)
    if not product.get("url"):
        product["url"] = url
    return write_product(product, output_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape YSL / Saint Laurent bag PDPs")
    parser.add_argument("urls", nargs="*")
    parser.add_argument("-o", "--output-dir", default="images/ysl")
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument(
        "--from-html-dir",
        help="Parse saved listing/PDP HTML instead of live CDP fetches",
    )
    args = parser.parse_args(argv)

    if args.discover:
        for url in discover_urls(args.limit):
            print(url)
        return 0

    catalog = []
    if args.from_html_dir:
        products = load_products_from_html_dir(args.from_html_dir, args.limit)
        print(f"Offline: {len(products)} products from {args.from_html_dir}")
        multi = len(products) > 1
        for product in products:
            target = (
                os.path.join(args.output_dir, product["sku"])
                if multi
                else args.output_dir
            )
            try:
                print(f"Scraping {product['sku']} ({product.get('name')})")
                catalog.append(write_product(product, target))
            except Exception as error:  # noqa: BLE001
                print(f"FAIL {product['sku']}: {error}", file=sys.stderr)
    else:
        urls = args.urls or discover_urls(args.limit)
        multi = len(urls) > 1
        for url in urls:
            sku = sku_from_url(url)
            target = os.path.join(args.output_dir, sku) if multi else args.output_dir
            try:
                catalog.append(scrape_one(url, target))
            except Exception as error:  # noqa: BLE001
                print(f"FAIL {url}: {error}", file=sys.stderr)

    os.makedirs(args.output_dir, exist_ok=True)
    json.dump(
        {"brand": BRAND, "top": catalog},
        open(os.path.join(args.output_dir, "catalog.json"), "w", encoding="utf-8"),
        indent=2,
    )
    print(f"Done. {len(catalog)} products -> {args.output_dir}")
    return 0 if catalog else 2


if __name__ == "__main__":
    raise SystemExit(main())
