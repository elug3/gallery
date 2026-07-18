#!/usr/bin/env python3
"""Scrape Saint Laurent / YSL US bag PDPs via Chrome CDP into info.json + images."""

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


def discover_urls(limit: int = DEFAULT_LIMIT) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for page in (1, 2, 3):
        listing = LISTING_URL if page == 1 else f"{LISTING_URL}?page={page}"
        try:
            html = fetch_html(listing, settle=3.5)
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


def sku_from_url(url: str) -> str:
    leaf = url.rstrip("/").rsplit("/", 1)[-1].replace(".html", "")
    match = re.search(r"([A-Z0-9]{8,})$", leaf)
    return match.group(1) if match else leaf


def parse_dimensions(short_description: list) -> dict[str, str]:
    dimensions: dict[str, str] = {}
    for item in short_description or []:
        text = strip_html(str(item))
        if text.lower().startswith("dimensions:"):
            dimensions["raw"] = text.split(":", 1)[1].strip()
    return dimensions


def scrape_one(url: str, output_dir: str) -> dict:
    print(f"Fetching {url}")
    html = fetch_html(url, settle=3.0)
    data = parse_next_data(html)
    page_props = (data.get("props") or {}).get("pageProps") or {}
    product = page_props.get("product") or {}
    price_obj = page_props.get("price") or {}

    sku = str(product.get("id") or product.get("smcId") or sku_from_url(url))
    name = strip_html(str(product.get("name") or ""))
    color = strip_html(
        str(product.get("macroColor") or product.get("microColor") or product.get("color") or "")
    )
    price = normalize_price(
        price_obj.get("salePriceValue")
        or price_obj.get("salePrice")
        or price_obj.get("listPriceValue")
    )
    material = strip_html(
        str(
            product.get("compositionDetailsDisplay")
            or product.get("compositions")
            or ""
        )
    )
    description = strip_html(str(product.get("description") or ""))
    short = product.get("shortDescription") or []
    dimensions = parse_dimensions(short if isinstance(short, list) else [])
    details = [strip_html(str(item)) for item in short if item]
    if product.get("madeIn"):
        details.append(f"Made in: {strip_html(str(product['madeIn']))}")
    details.append(f"Product code: {sku}")

    image_urls: list[str] = []
    seen: set[str] = set()
    for item in product.get("images") or []:
        src = ""
        if isinstance(item, dict):
            src = str(item.get("src") or "")
        elif isinstance(item, str):
            src = item
        if not src:
            continue
        src = ecom_image_url(src.split("?")[0])
        if src not in seen:
            seen.add(src)
            image_urls.append(src)

    os.makedirs(output_dir, exist_ok=True)
    image_files: list[str] = []
    saved_urls: list[str] = []
    for index, image_url in enumerate(image_urls, start=1):
        dest = download_image(image_url, os.path.join(output_dir, f"image_{index:02d}"))
        if not dest:
            print(f"  [{index}/{len(image_urls)}] FAILED {image_url}", file=sys.stderr)
            continue
        image_files.append(os.path.basename(dest))
        saved_urls.append(image_url)
        print(f"  [{index}/{len(image_urls)}] saved {dest}")

    variant = {
        "sku": sku,
        "color": color,
        "size": normalize_size(""),
        "price": price,
        "status": "active",
        "images": image_files,
        "imageUrls": saved_urls,
        "selected": True,
        "available": True,
        "sourceUrl": url,
    }
    info = build_info(
        name=name,
        description=description,
        brand=BRAND,
        material=material,
        capacity=dimensions.get("raw", ""),
        source_url=url,
        tags=["ysl", "saint-laurent", "bags"],
        variants=[variant],
        details=details,
        dimensions=dimensions,
        currency=str(price_obj.get("currencyCode") or "USD"),
        product_group_id=str(product.get("masterId") or sku[:12]),
        selected_sku=sku,
    )
    write_info_json(info, output_dir)
    print(f"  wrote info.json name={name!r} sku={sku} price={price} images={len(image_files)}")
    return {"sku": sku, "name": name, "url": url, "images": len(image_files), "dir": output_dir}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape YSL / Saint Laurent bag PDPs")
    parser.add_argument("urls", nargs="*")
    parser.add_argument("-o", "--output-dir", default="images/ysl")
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args(argv)

    if args.discover:
        for url in discover_urls(args.limit):
            print(url)
        return 0

    urls = args.urls or discover_urls(args.limit)
    catalog = []
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
    print(f"Done. {len(catalog)}/{len(urls)} products -> {args.output_dir}")
    return 0 if catalog else 2


if __name__ == "__main__":
    raise SystemExit(main())
