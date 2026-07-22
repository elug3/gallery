#!/usr/bin/env python3
"""Scrape Louis Vuitton handbag PDPs into Dupli1-shaped info.json + images.

Live us.louisvuitton.com PDPs/images are Akamai-blocked from many datacenter
IPs. This scraper:

1. Discovers iconic handbag PDP URLs via the Wayback CDX API (or uses a
   curated default list).
2. Fetches product HTML from Wayback ``id_`` snapshots (JSON-LD Product).
3. Downloads packshots from ``www.louisvuitton.cn`` Scene7 paths (same
   ``/images/is/image/lv/...`` asset IDs; CN host is reachable).

Usage::

    python3 louisvuitton/main.py --limit 20
    python3 louisvuitton/main.py --discover --limit 20
    python3 louisvuitton/main.py <product-url> [...]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from shared.http_util import (  # noqa: E402
    build_info,
    download_image,
    fetch,
    normalize_price,
    strip_html,
    write_info_json,
)

BRAND = "Louis Vuitton"
DEFAULT_LIMIT = 20
IMAGE_HOST = "https://www.louisvuitton.cn"

# One SKU per iconic family (Wayback-proven US PDP URLs).
DEFAULT_URLS = [
    "https://us.louisvuitton.com/eng-us/products/neverfull-bandouliere-inside-out-bb-autres-toiles-monogram-nvprod7020004v/M26315",
    "https://us.louisvuitton.com/eng-us/products/speedy-18-bandouliere-monogram-other-nvprod7310082v/M27557",
    "https://us.louisvuitton.com/eng-us/products/onthego-bb-monogram-empreinte-nvprod5190124v/M46993",
    "https://us.louisvuitton.com/eng-us/products/alma-25-other-leathers-nvprod4900047v/M24520",
    "https://us.louisvuitton.com/eng-us/products/carryall-bb-monogram-nvprod5960041v/M13014",
    "https://us.louisvuitton.com/eng-us/products/capucines-mm-capucines-nvprod5210011v/M24745",
    "https://us.louisvuitton.com/eng-us/products/graceful-mm-damier-ebene-nvprod620263v/N44045",
    "https://us.louisvuitton.com/eng-us/products/onthego-bb-monogram-reverse-canvas-nvprod5370118v/M46839",
    "https://us.louisvuitton.com/eng-us/products/neverfull-bandouliere-inside-out-bb-h33-nvprod5770174v/M12099",
    "https://us.louisvuitton.com/eng-us/products/speedy-18-bandouliere-damier-other-nvprod6250182v/N00208",
    "https://us.louisvuitton.com/eng-us/products/carryall-cargo-pm-h27-nvprod5190092v/M24861",
    "https://us.louisvuitton.com/eng-us/products/alma-backpack-monogram-nvprod5190089v/M47132",
    "https://us.louisvuitton.com/eng-us/products/onthego-bb-bicolor-monogram-empreinte-leather-nvprod5190125v/M47054",
    "https://us.louisvuitton.com/eng-us/products/speedy-18-bandouliere-d16-nvprod7540337v/M29050",
    "https://us.louisvuitton.com/eng-us/products/neverfull-bandouliere-inside-out-bb-other-leathers-nvprod5770174v/M12109",
    "https://us.louisvuitton.com/eng-us/products/carryall-dark-mm-h27-nvprod5190093v/M25143",
    "https://us.louisvuitton.com/eng-us/products/alma-103-h27-nvprod5190102v/M25142",
    "https://us.louisvuitton.com/eng-us/products/onthego-cat-monogram-nvprod6750013v/M15142",
    "https://us.louisvuitton.com/eng-us/products/neverfull-bandouliere-inside-out-bb-monogram-eclipse-nvprod5770174v/M15210",
    "https://us.louisvuitton.com/eng-us/products/speedy-18-autruche-nvprod6060061v/N87525",
]

CDX_QUERIES = [
    "us.louisvuitton.com/eng-us/products/neverfull*",
    "us.louisvuitton.com/eng-us/products/speedy*",
    "us.louisvuitton.com/eng-us/products/capucines*",
    "us.louisvuitton.com/eng-us/products/onthego*",
    "us.louisvuitton.com/eng-us/products/alma*",
    "us.louisvuitton.com/eng-us/products/carryall*",
    "us.louisvuitton.com/eng-us/products/twist*",
    "us.louisvuitton.com/eng-us/products/coussin*",
    "us.louisvuitton.com/eng-us/products/diane*",
    "us.louisvuitton.com/eng-us/products/graceful*",
    "us.louisvuitton.com/eng-us/products/multi-pochette*",
    "us.louisvuitton.com/eng-us/products/side-trunk*",
    "us.louisvuitton.com/eng-us/products/keepall*",
    "us.louisvuitton.com/eng-us/products/bumbag*",
]

SKU_RE = re.compile(r"/(M|N)[0-9A-Z]{4,}/?$", re.I)
JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def sku_from_url(url: str) -> str:
    leaf = url.rstrip("/").rsplit("/", 1)[-1]
    return leaf.split("?")[0]


def cdx_latest(url_pattern: str, limit: int = 30) -> list[tuple[str, str]]:
    api = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(
        {
            "url": url_pattern,
            "output": "json",
            "filter": "statuscode:200",
            "limit": str(limit),
            "fl": "original,timestamp",
        }
    )
    raw = fetch(api, timeout=90)
    data = json.loads(raw)
    if not data:
        return []
    rows = data[1:] if data[0][0] == "original" else data
    out: list[tuple[str, str]] = []
    for original, timestamp in rows:
        if not SKU_RE.search(original):
            continue
        out.append((original.split("?")[0], timestamp))
    return out


def discover_urls(limit: int = DEFAULT_LIMIT) -> list[tuple[str, str]]:
    """Return ``(product_url, wayback_timestamp)`` pairs, one SKU each."""
    seen: set[str] = set()
    found: list[tuple[str, str]] = []
    for query in CDX_QUERIES:
        try:
            rows = cdx_latest(query, limit=25)
        except Exception as error:  # noqa: BLE001
            print(f"CDX fail {query}: {error}", file=sys.stderr)
            continue
        # Prefer newest snapshot per SKU within this family, then move on.
        family_added = 0
        for original, timestamp in sorted(rows, key=lambda item: item[1], reverse=True):
            sku = sku_from_url(original)
            if sku in seen:
                continue
            seen.add(sku)
            found.append((original, timestamp))
            family_added += 1
            if family_added >= 2:  # up to 2 colorways per family from CDX
                break
        if len(found) >= limit:
            break
        time.sleep(0.4)
    return found[:limit]


def wayback_id_url(original: str, timestamp: str) -> str:
    return f"https://web.archive.org/web/{timestamp}id_/{original}"


def resolve_snapshot(url: str) -> tuple[str, str]:
    """Return ``(html, source_url)`` for a live or Wayback-backed PDP."""
    try:
        html = fetch(url, referer="https://us.louisvuitton.com/").decode(
            "utf-8", errors="replace"
        )
        if "Access Denied" not in html and "schema.org" in html:
            return html, url
    except Exception:  # noqa: BLE001
        pass

    sku = sku_from_url(url)
    pattern = f"us.louisvuitton.com/eng-us/products/*/{sku}"
    rows = cdx_latest(pattern, limit=5)
    if not rows:
        # fall back to exact URL CDX
        rows = cdx_latest(url, limit=5)
    if not rows:
        raise RuntimeError(f"no Wayback snapshot for {sku}")
    original, timestamp = sorted(rows, key=lambda item: item[1], reverse=True)[0]
    wb = wayback_id_url(original, timestamp)
    html = fetch(wb, timeout=90).decode("utf-8", errors="replace")
    return html, original


def parse_product_ld(html: str) -> dict:
    for match in JSON_LD_RE.finditer(html):
        raw = match.group(1).strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "Product":
                return item
    raise RuntimeError("Product JSON-LD not found")


def cn_image_url(url: str) -> str:
    """Rewrite LV Scene7 URLs onto the reachable CN host and bump width."""
    parsed = urllib.parse.urlsplit(url.strip())
    # Encode spaces in the path (LV asset names like ``Front view.jpg``).
    path = urllib.parse.quote(urllib.parse.unquote(parsed.path), safe="/%")
    query = urllib.parse.urlencode({"wid": "2000"})
    return urllib.parse.urlunsplit(
        ("https", "www.louisvuitton.cn", path, query, "")
    )


def extract_image_urls(product: dict, html: str, limit: int = 8) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        if not raw or not isinstance(raw, str):
            return
        if "/images/is/image/lv/" not in raw:
            return
        # Drop srcset descriptors: take the first URL token only when comma-separated
        # widths are present, but keep spaces that belong to the filename.
        candidate = raw.strip()
        if " 490w" in candidate or " 600w" in candidate:
            candidate = candidate.split(",")[0].strip()
            candidate = re.sub(r"\s+\d+w$", "", candidate).strip()
        cleaned = candidate.split("?")[0]
        if "flags/" in cleaned:
            return
        if not re.search(r"\.(jpe?g|png|webp)$", cleaned, re.I):
            return
        key = cleaned
        if key in seen:
            return
        seen.add(key)
        urls.append(cn_image_url(cleaned))

    for image in product.get("image") or []:
        if isinstance(image, dict):
            add(str(image.get("url") or ""))
        else:
            add(str(image))

    for match in re.findall(
        r"https://(?:us\.|www\.)?louisvuitton\.com/images/is/image/lv/[^\"'<>]+",
        html,
    ):
        add(match)
        if len(urls) >= limit * 2:
            break

    urls.sort(key=lambda u: (0 if "Front" in u else 1, u))
    return urls[:limit]


def offer_price(product: dict) -> float | None:
    offers = product.get("offers")
    if isinstance(offers, dict):
        return normalize_price(offers.get("price") or offers.get("lowPrice"))
    if isinstance(offers, list) and offers:
        first = offers[0]
        if isinstance(first, dict):
            return normalize_price(first.get("price"))
    # fallback: bare "price" fields in HTML JSON
    return None


def scrape_one(url: str, output_dir: str) -> dict:
    print(f"Fetching {url}")
    html, source_url = resolve_snapshot(url)
    product = parse_product_ld(html)
    sku = str(product.get("sku") or sku_from_url(source_url))
    name = strip_html(str(product.get("name") or ""))
    material = strip_html(str(product.get("material") or ""))
    description = strip_html(str(product.get("description") or ""))
    color = strip_html(str(product.get("color") or ""))
    price = offer_price(product)
    if price is None:
        prices = re.findall(r'"price"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)"?', html)
        price = normalize_price(prices[0]) if prices else None

    image_urls = extract_image_urls(product, html)
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
        "size": "",
        "price": price,
        "status": "active",
        "images": image_files,
        "imageUrls": saved_urls,
        "selected": True,
        "available": True,
        "sourceUrl": source_url,
    }
    info = build_info(
        name=name,
        description=description,
        brand=BRAND,
        material=material,
        capacity="",
        source_url=source_url,
        tags=["louis-vuitton", "lv", "bags"],
        variants=[variant],
        details=[part for part in [description, material, f"Product code: {sku}"] if part],
        dimensions={},
        currency="USD",
        product_group_id=sku,
        selected_sku=sku,
    )
    write_info_json(info, output_dir)
    print(f"  wrote info.json name={name!r} sku={sku} price={price} images={len(image_files)}")
    return {
        "sku": sku,
        "name": name,
        "url": source_url,
        "images": len(image_files),
        "dir": output_dir,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape Louis Vuitton handbag PDPs")
    parser.add_argument("urls", nargs="*")
    parser.add_argument("-o", "--output-dir", default="images/louisvuitton")
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args(argv)

    if args.discover:
        for original, timestamp in discover_urls(args.limit):
            print(f"{original}\t{timestamp}")
        return 0

    pairs: list[tuple[str, str | None]]
    if args.urls:
        pairs = [(url, None) for url in args.urls]
    else:
        pairs = [(url, None) for url in DEFAULT_URLS[: args.limit]]
        # Fill from CDX if defaults are short
        if len(pairs) < args.limit:
            for original, _ts in discover_urls(args.limit):
                if any(original == url for url, _ in pairs):
                    continue
                pairs.append((original, None))
                if len(pairs) >= args.limit:
                    break

    catalog = []
    multi = len(pairs) > 1
    for url, _ts in pairs:
        sku = sku_from_url(url)
        target = os.path.join(args.output_dir, sku) if multi else args.output_dir
        try:
            catalog.append(scrape_one(url, target))
        except Exception as error:  # noqa: BLE001
            print(f"FAIL {url}: {error}", file=sys.stderr)
        time.sleep(0.5)

    os.makedirs(args.output_dir, exist_ok=True)
    json.dump(
        {"brand": BRAND, "top": catalog},
        open(os.path.join(args.output_dir, "catalog.json"), "w", encoding="utf-8"),
        indent=2,
    )
    print(f"Done. {len(catalog)}/{len(pairs)} products -> {args.output_dir}")
    return 0 if catalog else 2


if __name__ == "__main__":
    raise SystemExit(main())
