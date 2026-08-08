#!/usr/bin/env python3
"""Scrape Bottega Veneta US bag PDPs into Dupli1-shaped info.json + images.

Live pages are often Akamai-blocked. Prefer:
  - Chrome CDP when a desktop Chrome is on ``--remote-debugging-port=9222``
  - ``--from-html-dir`` / ``--from-catalog`` for offline HTML or a prepared JSON
  - Wayback Machine PLP snapshots (``--from-wayback``) as a datacenter fallback

DAM packshots on ``bottega-veneta.dam.kering.com`` stay reachable via urllib.
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

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from shared.http_util import (  # noqa: E402
    build_info,
    download_image,
    normalize_price,
    normalize_size,
    strip_html,
    write_info_json,
)

BRAND = "Bottega Veneta"
REFERER = "https://www.bottegaveneta.com/"
DEFAULT_LIMIT = 20
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

NEW_BAGS_PLP = "https://www.bottegaveneta.com/en-us/women/women-bags/women-new-bags"
POPULAR_PLPS = [
    "https://www.bottegaveneta.com/en-us/women-collection-us/women-bags/jodie",
    "https://www.bottegaveneta.com/en-us/women/women-bags/women-cassette",
    "https://www.bottegaveneta.com/en-us/women/women-bags/women-andiamo",
    "https://www.bottegaveneta.com/en-us/women/women-bags/hop",
    "https://www.bottegaveneta.com/en-us/new/highlights/hop-highlights",
]

# Curated classics used when discovery is thin.
DEFAULT_POPULAR_URLS = [
    "https://www.bottegaveneta.com/en-us/mini-jodie-black-651876VCPP58803.html",
    "https://www.bottegaveneta.com/en-us/cassette-parakeet-578004VMAY13724.html",
    "https://www.bottegaveneta.com/en-us/padded-cassette-fondant-591970VCQR12132.html",
    "https://www.bottegaveneta.com/en-us/andiamo-fondant-766016V1QE62272.html",
    "https://www.bottegaveneta.com/en-us/small-andiamo-graphite-766014V1QE61041.html",
    "https://www.bottegaveneta.com/en-us/east-west-andiamo-black-766010VCPP11139.html",
    "https://www.bottegaveneta.com/en-us/hop-lunar-796262V1QE31441.html",
    "https://www.bottegaveneta.com/en-us/large-hop-fondant-763970V3IV12190.html",
]

DAM_M_RE = re.compile(
    r"https://bottega-veneta\.dam\.kering\.com/m/[a-f0-9]+/"
    r"(?:Small_thumbnail|Thumbnail|Small|Medium|Large|eCom)-"
    r"([A-Z0-9]+)_([A-Z])\.jpg",
    re.I,
)
DAM_ASSET_RE = re.compile(
    r"https://bottega-veneta\.dam\.kering\.com/asset/[a-f0-9\-]+/"
    r"(?:Small_thumbnail|Thumbnail|Small|Medium|Large|eCom)/"
    r"([A-Z0-9]+)_([A-Z])\.jpg",
    re.I,
)


def ecom_image_url(url: str) -> str:
    url = html_lib.unescape(url).split("?")[0]
    url = re.sub(
        r"/(?:Small_thumbnail|Thumbnail|Small|Medium|Large|eCom)-",
        "/eCom-",
        url,
    )
    for old in (
        "/Small_thumbnail/",
        "/Thumbnail/",
        "/Small/",
        "/Medium/",
        "/Large/",
    ):
        url = url.replace(old, "/eCom/")
    return url


def sku_from_url(url: str) -> str:
    leaf = url.rstrip("/").rsplit("/", 1)[-1].replace(".html", "")
    match = re.search(r"([A-Z0-9]{10,})$", leaf, re.I)
    return match.group(1).upper() if match else leaf.upper()


def http_get(url: str, *, timeout: int = 60) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": REFERER,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def images_for_sku(html: str, sku: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    patterns = (
        rf"https://bottega-veneta\.dam\.kering\.com/m/[a-f0-9]+/"
        rf"(?:Small_thumbnail|Thumbnail|Small|Medium|Large|eCom)-"
        rf"{re.escape(sku)}_[A-Z]\.jpg",
        rf"https://bottega-veneta\.dam\.kering\.com/asset/[a-f0-9\-]+/"
        rf"(?:Small_thumbnail|Thumbnail|Small|Medium|Large|eCom)/"
        rf"{re.escape(sku)}_[A-Z]\.jpg",
    )
    for pattern in patterns:
        for match in re.findall(pattern, html, re.I):
            url = ecom_image_url(match)
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def parse_listing_products(html: str) -> list[dict]:
    products: list[dict] = []
    seen: set[str] = set()
    for match in re.finditer(r'data-gtmproduct="([^"]+)"', html):
        try:
            gtm = json.loads(html_lib.unescape(match.group(1)))
        except json.JSONDecodeError:
            continue
        if not isinstance(gtm, dict):
            continue
        sku = str(gtm.get("productSMC") or gtm.get("id") or "").upper()
        if not sku or sku in seen:
            continue
        window = html[max(0, match.start() - 800) : match.end() + 14000]
        href_match = re.search(r'href="(/en-us/[^"]+\.html)"', window)
        path = href_match.group(1) if href_match else f"/en-us/product-{sku}.html"
        images = images_for_sku(window, sku) or images_for_sku(html, sku)
        seen.add(sku)
        products.append(
            {
                "sku": sku,
                "name": strip_html(str(gtm.get("name") or "")).title(),
                "color": strip_html(str(gtm.get("color") or "")).title(),
                "price": normalize_price(
                    gtm.get("discountPrice")
                    if gtm.get("discountPrice") not in (None, "")
                    else gtm.get("price")
                ),
                "material": strip_html(str(gtm.get("material") or "")),
                "url": urllib.parse.urljoin("https://www.bottegaveneta.com", path),
                "images": images,
                "description": "",
                "category": str(gtm.get("subCategory") or gtm.get("category") or ""),
            }
        )
    return products


def pick_unique(products: list[dict], limit: int) -> list[dict]:
    ranked = sorted(
        products,
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
        key = re.sub(r"\s+", " ", (product.get("name") or "").lower()).strip()
        if key in seen_names:
            continue
        seen_names.add(key)
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


def cdp_fetch_html(url: str, settle: float = 4.0) -> str:
    from shared.cdp import fetch_html

    return fetch_html(url, settle=settle)


def wayback_snapshot(url: str) -> str | None:
    query = (
        "https://web.archive.org/cdx/search/cdx?url="
        + urllib.parse.quote(url, safe="")
        + "&output=json&fl=timestamp,statuscode&filter=statuscode:200&limit=8&from=20240101"
    )
    try:
        payload = json.loads(http_get(query, timeout=45))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None
    stamps = [row[0] for row in payload[1:]]
    return stamps[-1] if stamps else None


def fetch_wayback_html(url: str) -> str:
    stamp = wayback_snapshot(url)
    if not stamp:
        raise RuntimeError(f"no Wayback snapshot for {url}")
    raw = f"https://web.archive.org/web/{stamp}id_/{url}"
    print(f"wayback {stamp} {url}")
    return http_get(raw, timeout=90).decode("utf-8", errors="replace")


def load_products_from_html_dir(html_dir: str) -> list[dict]:
    by_sku: dict[str, dict] = {}
    for name in sorted(os.listdir(html_dir)):
        if not name.endswith(".html"):
            continue
        path = os.path.join(html_dir, name)
        html = open(path, encoding="utf-8", errors="ignore").read()
        for product in parse_listing_products(html):
            existing = by_sku.get(product["sku"])
            if not existing or len(product.get("images") or []) > len(
                existing.get("images") or []
            ):
                by_sku[product["sku"]] = product
    return list(by_sku.values())


def load_catalog_json(path: str) -> list[dict]:
    data = json.load(open(path, encoding="utf-8"))
    rows: list[dict] = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        for key in ("popular", "latest", "top", "products"):
            chunk = data.get(key)
            if isinstance(chunk, list):
                for item in chunk:
                    if isinstance(item, dict):
                        item = dict(item)
                        item.setdefault("list", key if key in {"popular", "latest"} else "")
                        rows.append(item)
    normalized: list[dict] = []
    for row in rows:
        sku = str(row.get("sku") or sku_from_url(str(row.get("url") or ""))).upper()
        if not sku:
            continue
        images = [ecom_image_url(str(u)) for u in (row.get("images") or []) if u]
        normalized.append(
            {
                "sku": sku,
                "name": str(row.get("name") or sku),
                "color": str(row.get("color") or ""),
                "price": normalize_price(row.get("price")),
                "material": str(row.get("material") or ""),
                "description": str(row.get("description") or ""),
                "url": str(row.get("url") or ""),
                "images": images,
                "list": str(row.get("list") or ""),
            }
        )
    return normalized


def discover_split(limit_each: int = 10, *, use_wayback: bool = False) -> dict[str, list[dict]]:
    """Return ``{"popular": [...], "latest": [...]}`` with unique model names."""
    fetch_html = fetch_wayback_html if use_wayback else cdp_fetch_html

    latest_all: list[dict] = []
    try:
        html = fetch_html(NEW_BAGS_PLP)
        if "Access Denied" in html[:800]:
            raise RuntimeError("Access Denied on new-bags PLP")
        latest_all = parse_listing_products(html)
    except Exception as error:  # noqa: BLE001
        print(f"latest PLP failed: {error}", file=sys.stderr)
        if not use_wayback:
            print("retrying latest via Wayback…", file=sys.stderr)
            try:
                latest_all = parse_listing_products(fetch_wayback_html(NEW_BAGS_PLP))
            except Exception as nested:  # noqa: BLE001
                print(f"wayback latest failed: {nested}", file=sys.stderr)

    popular_all: list[dict] = []
    for plp in POPULAR_PLPS:
        try:
            html = fetch_html(plp)
            if "Access Denied" in html[:800]:
                raise RuntimeError("Access Denied")
            popular_all.extend(parse_listing_products(html))
            print(f"popular PLP ok ({len(popular_all)}): {plp}")
        except Exception as error:  # noqa: BLE001
            print(f"popular PLP skip {plp}: {error}", file=sys.stderr)
            if not use_wayback:
                try:
                    popular_all.extend(parse_listing_products(fetch_wayback_html(plp)))
                    print(f"popular PLP via wayback ({len(popular_all)}): {plp}")
                except Exception as nested:  # noqa: BLE001
                    print(f"wayback skip {plp}: {nested}", file=sys.stderr)

    latest = pick_unique(latest_all, limit_each)
    # Prefer iconic names for the popular half when available.
    preferred = [
        "Mini Jodie",
        "Jodie",
        "Cassette",
        "Padded Cassette",
        "Andiamo",
        "Small Andiamo",
        "East-West Andiamo",
        "Hop",
        "Large Hop",
        "Andiamo Bucket",
    ]
    by_sku = {
        p["sku"]: p
        for p in popular_all
        if p.get("images")
    }
    popular: list[dict] = []
    used: set[str] = set()
    for want in preferred:
        for product in sorted(
            by_sku.values(),
            key=lambda item: (-len(item.get("images") or []), item.get("sku") or ""),
        ):
            if product["sku"] in used:
                continue
            if (product.get("name") or "").lower() == want.lower():
                popular.append(product)
                used.add(product["sku"])
                break
        if len(popular) >= limit_each:
            break
    if len(popular) < limit_each:
        for product in pick_unique(list(by_sku.values()), limit_each * 2):
            if product["sku"] in used:
                continue
            popular.append(product)
            used.add(product["sku"])
            if len(popular) >= limit_each:
                break

    for product in popular:
        product["list"] = "popular"
    for product in latest:
        product["list"] = "latest"
    return {"popular": popular[:limit_each], "latest": latest[:limit_each]}


def write_product(product: dict, output_dir: str) -> dict:
    sku = product["sku"]
    image_urls = list(dict.fromkeys(product.get("images") or []))
    os.makedirs(output_dir, exist_ok=True)
    image_files: list[str] = []
    saved_urls: list[str] = []
    for index, image_url in enumerate(image_urls, start=1):
        candidates = [image_url]
        if "/eCom-" in image_url:
            candidates.append(image_url.replace("/eCom-", "/Large-"))
        if "/eCom/" in image_url:
            candidates.append(image_url.replace("/eCom/", "/Large/"))
        dest = None
        used = image_url
        for candidate in candidates:
            dest = download_image(
                candidate,
                os.path.join(output_dir, f"image_{index:02d}"),
                referer=REFERER,
            )
            if dest:
                used = candidate
                break
        if not dest:
            print(f"  [{index}/{len(image_urls)}] FAILED {image_url}", file=sys.stderr)
            continue
        image_files.append(os.path.basename(dest))
        saved_urls.append(used)
        print(f"  [{index}/{len(image_urls)}] saved {dest}")

    details: list[str] = []
    if product.get("description"):
        details.append(str(product["description"]))
    if product.get("material"):
        details.append(f"Material: {product['material']}")
    if product.get("list"):
        details.append(f"Selection: {product['list']}")
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
        capacity="",
        source_url=product.get("url") or "",
        tags=["bottega-veneta", "bottega", "bags", product.get("list") or "bags"],
        variants=[variant],
        details=details,
        dimensions={},
        currency="USD",
        product_group_id=sku[:12],
        selected_sku=sku,
    )
    write_info_json(info, output_dir)
    print(
        f"  wrote info.json name={product.get('name')!r} sku={sku} "
        f"price={product.get('price')} images={len(image_files)} list={product.get('list')}"
    )
    return {
        "sku": sku,
        "name": product.get("name"),
        "url": product.get("url"),
        "images": len(image_files),
        "list": product.get("list"),
        "dir": output_dir,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape Bottega Veneta bag PDPs")
    parser.add_argument("urls", nargs="*")
    parser.add_argument("-o", "--output-dir", default="images/bottega")
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Total bags (split 50/50 popular/latest when discovering)")
    parser.add_argument("--from-html-dir", help="Parse saved listing/PDP HTML")
    parser.add_argument(
        "--from-catalog",
        help="JSON with popular/latest product rows (sku, name, color, price, images, url)",
    )
    parser.add_argument(
        "--from-wayback",
        action="store_true",
        help="Discover via Wayback Machine PLP snapshots (Akamai bypass)",
    )
    args = parser.parse_args(argv)

    half = max(1, args.limit // 2)

    if args.discover:
        split = discover_split(half, use_wayback=args.from_wayback)
        for section in ("popular", "latest"):
            print(f"# {section}")
            for product in split[section]:
                print(product.get("url") or product.get("sku"))
        return 0

    products: list[dict] = []
    if args.from_catalog:
        products = load_catalog_json(args.from_catalog)
        print(f"Catalog: {len(products)} products from {args.from_catalog}")
    elif args.from_html_dir:
        products = pick_unique(load_products_from_html_dir(args.from_html_dir), args.limit)
        print(f"Offline HTML: {len(products)} products from {args.from_html_dir}")
    elif args.urls:
        # URL-only mode still needs HTML (CDP or wayback) for images/meta.
        fetch_html = fetch_wayback_html if args.from_wayback else cdp_fetch_html
        for url in args.urls:
            try:
                html = fetch_html(url)
                parsed = parse_listing_products(html)
                product = parsed[0] if parsed else {
                    "sku": sku_from_url(url),
                    "name": "",
                    "color": "",
                    "price": None,
                    "url": url,
                    "images": images_for_sku(html, sku_from_url(url)),
                }
                product["url"] = url
                products.append(product)
            except Exception as error:  # noqa: BLE001
                print(f"FAIL {url}: {error}", file=sys.stderr)
    else:
        split = discover_split(half, use_wayback=args.from_wayback)
        products = split["popular"] + split["latest"]
        if len(products) < args.limit:
            print(
                f"Discovery returned {len(products)}; curated defaults may be incomplete.",
                file=sys.stderr,
            )

    catalog: list[dict] = []
    multi = len(products) > 1
    for product in products:
        sku = product.get("sku") or sku_from_url(product.get("url") or "")
        target = os.path.join(args.output_dir, sku) if multi else args.output_dir
        try:
            print(
                f"Scraping {sku} ({product.get('name')}) list={product.get('list') or '-'}"
            )
            catalog.append(write_product(product, target))
        except Exception as error:  # noqa: BLE001
            print(f"FAIL {sku}: {error}", file=sys.stderr)

    os.makedirs(args.output_dir, exist_ok=True)
    json.dump(
        {
            "brand": BRAND,
            "popular": [row for row in catalog if row.get("list") == "popular"],
            "latest": [row for row in catalog if row.get("list") == "latest"],
            "top": catalog,
        },
        open(os.path.join(args.output_dir, "catalog.json"), "w", encoding="utf-8"),
        indent=2,
        ensure_ascii=False,
    )
    print(f"Done. {len(catalog)} products -> {args.output_dir}")
    return 0 if len(catalog) >= min(args.limit, 1) else 2


if __name__ == "__main__":
    raise SystemExit(main())
