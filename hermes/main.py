#!/usr/bin/env python3
"""Scrape Hermès US bag PDPs into Dupli1-shaped info.json + source images."""

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

from shared.http_util import (  # noqa: E402
    build_info,
    download_image,
    fetch,
    normalize_price,
    normalize_size,
    strip_html,
    write_info_json,
)

BRAND = "Hermès"
LISTING_URL = (
    "https://www.hermes.com/us/en/category/leather-goods/bags-and-clutches/"
)
DEFAULT_LIMIT = 18

# Known-good US PDPs (listing discovery can include 403 rows).
DEFAULT_URLS = [
    "https://www.hermes.com/us/en/product/hac-a-dos-pm-backpack-H083589CK10/",
    "https://www.hermes.com/us/en/product/hermes-videpoches-bag-H087987CK10/",
    "https://www.hermes.com/us/en/product/hermes-in-the-loop-18-bag-H084274CCBW/",
    "https://www.hermes.com/us/en/product/p-tit-arcon-bag-H085871CKAO/",
    "https://www.hermes.com/us/en/product/hermes-videpoches-bag-H087901CKAE/",
    "https://www.hermes.com/us/en/product/bolide-a-dos-backpack-H085758CKAA/",
    "https://www.hermes.com/us/en/product/sac-a-depeches-light-1-36-briefcase-H085721CKV8/",
    "https://www.hermes.com/us/en/product/herbag-messenger-39-bag-H084623CKAF/",
    "https://www.hermes.com/us/en/product/etriviere-50-bag-H086683CK37/",
    "https://www.hermes.com/us/en/product/hac-a-dos-pm-backpack-H085960CKAB/",
    "https://www.hermes.com/us/en/product/herbag-messenger-39-bag-H084489CKAC/",
    "https://www.hermes.com/us/en/product/kelly-depeches-25-pouch-H082312CK89/",
    "https://www.hermes.com/us/en/product/tablier-sellier-bag-H086583CKAC/",
    "https://www.hermes.com/us/en/product/kelly-messenger-bag-H085671CK89/",
    "https://www.hermes.com/us/en/product/garden-party-pockets-vertical-bag-H084260CKAC/",
    "https://www.hermes.com/us/en/product/jige-elan-29-clutch-H048490CA7U/",
    "https://www.hermes.com/us/en/product/jypsiere-mini-bag-H083982CCBR/",
    "https://www.hermes.com/us/en/product/picotin-lock-micro-bag-H084238CKI2/",
]

STATE_RE = re.compile(
    r'<script id="hermes-state" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def parse_state(html: str) -> dict:
    match = STATE_RE.search(html)
    if not match:
        raise RuntimeError("hermes-state missing")
    return json.loads(match.group(1))


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk(value)


def listing_items(state: dict) -> list[dict]:
    for node in walk(state):
        products = ((node.get("b") or {}).get("products") or {})
        items = products.get("items")
        if isinstance(items, list) and items and isinstance(items[0], dict) and "sku" in items[0]:
            return [item for item in items if isinstance(item, dict)]
    return []


def find_product(state: dict, sku: str) -> dict | None:
    for node in walk(state):
        if (
            node.get("sku") == sku
            and isinstance(node.get("assets"), list)
            and isinstance(node.get("simpleAttributes"), dict)
        ):
            return node
    return None


def discover_urls(limit: int = DEFAULT_LIMIT) -> list[str]:
    html = fetch(
        LISTING_URL,
        referer="https://www.hermes.com/us/en/",
    ).decode("utf-8", errors="replace")
    items = listing_items(parse_state(html))
    urls: list[str] = []
    seen: set[str] = set()
    for item in items:
        path = str(item.get("url") or "")
        if not path:
            continue
        if path.startswith("http"):
            full = path
        elif path.startswith("/us/en/"):
            full = "https://www.hermes.com" + path
        elif path.startswith("/product/"):
            full = "https://www.hermes.com/us/en" + path
        else:
            full = urllib.parse.urljoin("https://www.hermes.com/us/en/", path.lstrip("/"))
        full = full.split("?")[0]
        if full in seen:
            continue
        seen.add(full)
        urls.append(full)
        if len(urls) >= limit:
            break
    return urls


def asset_url(raw: str) -> str:
    url = raw.strip()
    if url.startswith("//"):
        url = "https:" + url
    if "?" not in url:
        url = url + "?wid=3000"
    return url


def scrape_one(url: str, output_dir: str) -> dict:
    print(f"Fetching {url}")
    html = fetch(url, referer=LISTING_URL).decode("utf-8", errors="replace")
    sku_guess = url.rstrip("/").rsplit("-", 1)[-1].rstrip("/")
    if "/" in sku_guess:
        sku_guess = sku_guess.rstrip("/").rsplit("/", 1)[-1]
    # URL ends with ...-H084238CKI2/
    match = re.search(r"(H[0-9A-Z]+)/?$", url.rstrip("/"))
    sku = match.group(1) if match else sku_guess
    product = find_product(parse_state(html), sku)
    if not product:
        raise RuntimeError(f"product record not found for {sku}")

    attrs = product.get("simpleAttributes") or {}
    name = strip_html(str(product.get("title") or ""))
    price = normalize_price(product.get("price"))
    color = strip_html(str(attrs.get("colorHermes") or ""))
    if not color:
        raw_color = product.get("color")
        if isinstance(raw_color, list) and raw_color:
            color = strip_html(str(raw_color[0]))
        else:
            color = strip_html(str(raw_color or ""))
    material_raw = product.get("material")
    if isinstance(material_raw, list) and material_raw:
        material = strip_html(str(material_raw[0]))
    else:
        material = strip_html(str(material_raw or ""))
    description = strip_html(str(attrs.get("description") or ""))
    if description and not material:
        # e.g. "Bag in Swift calfskin ..."
        material = description.split(",")[0].strip()
    dims_raw = strip_html(str(attrs.get("dimensions") or ""))
    dimensions: dict[str, str] = {}
    if dims_raw:
        dimensions["raw"] = dims_raw
    details = [part for part in [description, dims_raw, attrs.get("madeIn"), attrs.get("finish")] if part]
    details = [strip_html(str(part)) for part in details]
    details.append(f"Product code: {sku}")

    assets = sorted(
        [a for a in (product.get("assets") or []) if isinstance(a, dict)],
        key=lambda item: item.get("position") or 0,
    )
    image_urls = [asset_url(str(a["url"])) for a in assets if a.get("url")]

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
        "size": normalize_size(str(attrs.get("modelSize") or "")),
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
        capacity=dims_raw,
        source_url=url,
        tags=["hermes", "bags"],
        variants=[variant],
        details=details,
        dimensions=dimensions,
        currency="USD",
        product_group_id=str(product.get("productCode") or sku[:8]),
        selected_sku=sku,
    )
    write_info_json(info, output_dir)
    print(f"  wrote info.json name={name!r} sku={sku} price={price} images={len(image_files)}")
    return {"sku": sku, "name": name, "url": url, "images": len(image_files), "dir": output_dir}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape Hermès bag PDPs")
    parser.add_argument("urls", nargs="*")
    parser.add_argument("-o", "--output-dir", default="images/hermes")
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args(argv)

    if args.discover:
        for url in discover_urls(args.limit):
            print(url)
        return 0

    urls = args.urls or DEFAULT_URLS[: args.limit]
    if not args.urls:
        discovered = discover_urls(args.limit)
        # Prefer known-good defaults; fill with discovered if needed.
        merged: list[str] = []
        seen: set[str] = set()
        for url in list(DEFAULT_URLS) + discovered:
            if url in seen:
                continue
            seen.add(url)
            merged.append(url)
            if len(merged) >= args.limit:
                break
        urls = merged
    catalog = []
    multi = len(urls) > 1
    for url in urls:
        match = re.search(r"(H[0-9A-Z]+)/?$", url.rstrip("/"))
        sku = match.group(1) if match else url.rstrip("/").rsplit("/", 1)[-1]
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
