#!/usr/bin/env python3
"""Scrape Balenciaga US bag PDPs into info.json + images.

Live pages are often Akamai-blocked; prefer Chrome CDP when available.
Offline fallback: ``--from-html-dir`` parses saved listing/PDP HTML and
downloads Kering DAM eCom JPEGs (which remain reachable via urllib).
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import sys
import urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from shared.cdp import fetch_html, fetch_json_in_page  # noqa: E402
from shared.http_util import (  # noqa: E402
    build_info,
    download_image,
    normalize_price,
    normalize_size,
    strip_html,
    write_info_json,
)

BRAND = "Balenciaga"
REFERER = "https://www.balenciaga.com/"
DEFAULT_LIMIT = 18

DEFAULT_URLS = [
    "https://www.balenciaga.com/en-us/le-city-backpack-mini-black-A006Q22ACRZ1000.html",
    "https://www.balenciaga.com/en-us/le-city-bag-medium-black-8657602ACFH1000.html",
    "https://www.balenciaga.com/en-us/rodeo-handbag-large-black-7897442AA4V1000.html",
    "https://www.balenciaga.com/en-us/rodeo-handbag-medium-black-7897722AA4V1000.html",
    "https://www.balenciaga.com/en-us/le-city-hobo-bag-large-black-8732002ABEK1000.html",
    "https://www.balenciaga.com/en-us/le-city-hobo-bag-medium-black-8731992ABEK1000.html",
    "https://www.balenciaga.com/en-us/le-city-bucket-bag-small-black-8767572ABEK1000.html",
    "https://www.balenciaga.com/en-us/le-7-bowling-bag-large-black-brown-A000G52ACPR6028.html",
]

BAG_NAME_HINTS = (
    "bag",
    "backpack",
    "hobo",
    "bucket",
    "rodeo",
    "city",
    "tote",
    "clutch",
    "pouch",
    "bowling",
    "shoulder",
    "hourglass",
    "crush",
)


def ecom_image_url(url: str) -> str:
    url = url.split("?")[0]
    url = url.replace("/Large/", "/eCom/").replace("/Large-", "/eCom-")
    url = url.replace("/Medium/", "/eCom/").replace("/Medium2/", "/eCom/")
    url = url.replace("/Thumbnail/", "/eCom/").replace("/Small/", "/eCom/")
    url = url.replace("/Small_thumbnail/", "/eCom/")
    return url


def sku_from_url(url: str) -> str:
    leaf = url.rstrip("/").rsplit("/", 1)[-1].replace(".html", "")
    match = re.search(r"([A-Z0-9]{8,})$", leaf, re.I)
    return match.group(1) if match else leaf


def parse_json_ld_product(html: str) -> dict:
    for raw in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.I | re.S,
    ):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if isinstance(node, dict) and node.get("@type") == "Product":
                return node
    return {}


def images_from_html(html: str, sku: str) -> list[str]:
    """Collect unique eCom DAM URLs for *sku* (each view has its own asset UUID)."""
    urls: list[str] = []
    seen: set[str] = set()
    pattern = re.compile(
        rf"https://balenciaga\.dam\.kering\.com/asset/[a-f0-9\-]+/"
        rf"(?:eCom|Large|Medium2|Medium|Thumbnail)/{re.escape(sku)}_[A-Z]\.jpg",
        re.I,
    )
    for match in pattern.findall(html):
        url = ecom_image_url(html_lib.unescape(match))
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def packshot_from_html(html: str, sku: str) -> str | None:
    images = images_from_html(html, sku)
    if images:
        return images[0]
    # Fallback: any DAM asset whose filename starts with sku
    match = re.search(
        rf"https://balenciaga\.dam\.kering\.com/asset/[a-f0-9\-]+/"
        rf"(?:eCom|Thumbnail|Medium|Large)/{re.escape(sku)}_[A-Z]\.jpg",
        html,
        re.I,
    )
    return ecom_image_url(match.group(0)) if match else None


def is_bag_gtm(gtm: dict) -> bool:
    name = str(gtm.get("name") or "").lower()
    if any(hint in name for hint in BAG_NAME_HINTS):
        return True
    category = str(gtm.get("category") or "").lower()
    return category in {"bags", "bag"}


def parse_listing_products(html: str) -> list[dict]:
    products: list[dict] = []
    seen: set[str] = set()
    # Build sku -> first eCom/Thumbnail URL map for fallback
    sku_images: dict[str, str] = {}
    for match in re.finditer(
        r"https://balenciaga\.dam\.kering\.com/asset/[a-f0-9\-]+/"
        r"(?:eCom|Thumbnail|Medium|Large)/([A-Z0-9]+)_[A-Z]\.jpg",
        html,
        re.I,
    ):
        code = match.group(1)
        sku_images.setdefault(code, ecom_image_url(match.group(0)))

    for match in re.finditer(r'data-gtmproduct="([^"]+)"', html):
        try:
            gtm = json.loads(html_lib.unescape(match.group(1)))
        except json.JSONDecodeError:
            continue
        if not isinstance(gtm, dict) or not is_bag_gtm(gtm):
            continue
        sku = str(gtm.get("productSMC") or gtm.get("id") or "")
        if not sku or sku in seen:
            continue
        window = html[max(0, match.start() - 2500) : match.end() + 4000]
        href_match = re.search(r'href="(/en-us/[^"]+\.html)"', window)
        path = href_match.group(1) if href_match else f"/en-us/product-{sku}.html"
        image = packshot_from_html(window, sku) or sku_images.get(sku)
        seen.add(sku)
        products.append(
            {
                "sku": sku,
                "name": strip_html(str(gtm.get("name") or "")),
                "color": strip_html(str(gtm.get("color") or "")),
                "price": normalize_price(gtm.get("discountPrice") or gtm.get("price")),
                "url": urllib.parse.urljoin("https://www.balenciaga.com", path),
                "images": [image] if image else [],
                "description": "",
                "material": strip_html(str(gtm.get("material") or "")),
            }
        )
    return products


def enrich_from_pdp_html(product: dict, html: str) -> dict:
    ld = parse_json_ld_product(html)
    sku = product["sku"]
    images = images_from_html(html, sku)
    if images:
        product["images"] = images
    if ld.get("name"):
        product["name"] = strip_html(str(ld["name"]))
    if ld.get("color"):
        product["color"] = strip_html(str(ld["color"]))
    if ld.get("description"):
        product["description"] = strip_html(str(ld["description"]))
    offers = ld.get("offers") if isinstance(ld.get("offers"), dict) else {}
    if offers.get("price") is not None:
        product["price"] = normalize_price(offers.get("price"))
    for match in re.findall(r"Material:\s*([^<\n]+)", html, re.I):
        product["material"] = strip_html(match)
        break
    if ld.get("url"):
        product["url"] = str(ld["url"])
    return product


def load_products_from_html_dir(html_dir: str, limit: int) -> list[dict]:
    by_sku: dict[str, dict] = {}
    pdp_html_by_sku: dict[str, str] = {}
    global_images: dict[str, str] = {}
    for name in sorted(os.listdir(html_dir)):
        if not name.endswith(".html"):
            continue
        path = os.path.join(html_dir, name)
        html = open(path, encoding="utf-8", errors="ignore").read()
        for match in re.finditer(
            r"https://balenciaga\.dam\.kering\.com/asset/[a-f0-9\-]+/"
            r"(?:eCom|Thumbnail|Medium|Large)/([A-Z0-9]+)_[A-Z]\.jpg",
            html,
            re.I,
        ):
            code = match.group(1)
            url = ecom_image_url(match.group(0))
            if code not in global_images or "/eCom/" in url:
                global_images[code] = url
        if "application/ld+json" in html and "Product" in html:
            ld = parse_json_ld_product(html)
            sku = ""
            if ld.get("url"):
                sku = sku_from_url(str(ld["url"]))
            if not sku:
                codes = re.findall(r"/eCom/([A-Z0-9]+)_[A-Z]\.jpg", html, re.I)
                sku = codes[0] if codes else ""
            if sku:
                pdp_html_by_sku[sku] = html
        for product in parse_listing_products(html):
            existing = by_sku.get(product["sku"])
            if not existing:
                by_sku[product["sku"]] = product
            elif not existing.get("images") and product.get("images"):
                by_sku[product["sku"]] = product

    for sku, product in by_sku.items():
        if not product.get("images") and sku in global_images:
            product["images"] = [global_images[sku]]

    for sku, html in pdp_html_by_sku.items():
        base = by_sku.get(sku) or {
            "sku": sku,
            "name": "",
            "color": "",
            "price": None,
            "url": f"https://www.balenciaga.com/en-us/product-{sku}.html",
            "images": [],
            "description": "",
            "material": "",
        }
        by_sku[sku] = enrich_from_pdp_html(base, html)

    skip_name = ("phone strap", "pouch on strap", "card holder", "keyring", "wallet")

    def style_key(name: str) -> str:
        text = re.sub(r"\s+", " ", (name or "").lower()).strip()
        # Drop trailing color words for grouping
        text = re.sub(
            r"\b(black|white|tan|camel|pink|red|green|brown|optic|volcanic|"
            r"rock|espresso|caramel|berry|biscuit|cowboy|new)\b",
            "",
            text,
        )
        return re.sub(r"\s+", " ", text).strip()

    ranked = sorted(
        by_sku.values(),
        key=lambda item: (
            0 if item.get("images") else 1,
            0 if item.get("description") else 1,
            # Prefer core handbags over small accessories
            1
            if any(skip in (item.get("name") or "").lower() for skip in skip_name)
            else 0,
            item.get("name") or "",
            item.get("sku") or "",
        ),
    )
    selected: list[dict] = []
    seen_styles: set[str] = set()
    for product in ranked:
        if not product.get("images"):
            continue
        name = (product.get("name") or "").lower()
        if any(skip in name for skip in skip_name):
            continue
        key = style_key(product.get("name") or "")
        if key in seen_styles:
            continue
        seen_styles.add(key)
        selected.append(product)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for product in ranked:
            if product in selected or not product.get("images"):
                continue
            name = (product.get("name") or "").lower()
            if any(skip in name for skip in skip_name):
                continue
            selected.append(product)
            if len(selected) >= limit:
                break
    return selected[:limit]


def discover_urls(limit: int = DEFAULT_LIMIT) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for start in (0, 13, 26, 39):
        ajax = (
            "https://www.balenciaga.com/en-us/searchajax"
            f"?cgid=women_bags&prefn1=akeneo_employeesSalesVisible"
            f"&prefn2=akeneo_markDownInto&prefn3=countryInclusion"
            f"&prefv1=false&prefv2=no_season&prefv3=US&start={start}&sz=13"
        )
        try:
            html = fetch_html(ajax, settle=2.0)
        except Exception as error:  # noqa: BLE001
            print(f"discover page start={start} failed: {error}", file=sys.stderr)
            continue
        if "Access Denied" in html:
            print(f"discover page start={start} Access Denied", file=sys.stderr)
            continue
        for product in parse_listing_products(html):
            full = product["url"]
            if full in seen:
                continue
            seen.add(full)
            urls.append(full)
            if len(urls) >= limit:
                return urls
    return urls or list(DEFAULT_URLS)[:limit]


def scrape_variation(url: str) -> dict | None:
    sku = sku_from_url(url)
    expression = f"""
    (async () => {{
      const pid = {json.dumps(sku)};
      const candidates = [
        `/on/demandware.store/Sites-balenciaga-us-Site/en_US/Product-Variation?pid=${{encodeURIComponent(pid)}}&quantity=1`,
        `/on/demandware.store/Sites-BAL-US-Site/en_US/Product-Variation?pid=${{encodeURIComponent(pid)}}&quantity=1`,
      ];
      for (const path of candidates) {{
        try {{
          const resp = await fetch(path, {{
            credentials: 'include',
            headers: {{ 'x-requested-with': 'XMLHttpRequest', 'Accept': 'application/json' }},
          }});
          if (!resp.ok) continue;
          const data = await resp.json();
          if (data && data.product) return data;
        }} catch (e) {{}}
      }}
      return null;
    }})()
    """
    try:
        return fetch_json_in_page(url, expression, settle=3.0)
    except Exception:  # noqa: BLE001
        return None


def images_from_variation(product: dict) -> list[str]:
    images = product.get("akeneoImages")
    urls: list[str] = []
    if isinstance(images, dict):
        packshot = images.get("packshot") or []
        if isinstance(packshot, list):
            for item in packshot:
                if isinstance(item, dict) and item.get("ecom"):
                    urls.append(ecom_image_url(str(item["ecom"])))
    elif isinstance(images, list):
        for item in images:
            if isinstance(item, dict) and item.get("url"):
                urls.append(ecom_image_url(str(item["url"])))
            elif isinstance(item, str):
                urls.append(ecom_image_url(item))
    return urls


def write_product(product: dict, output_dir: str) -> dict:
    sku = product["sku"]
    name = product.get("name") or sku
    color = product.get("color") or ""
    price = product.get("price")
    material = product.get("material") or ""
    description = product.get("description") or ""
    url = product.get("url") or ""
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

    details = [description] if description else []
    if material:
        details.append(f"Material: {material}")
    details.append(f"Product code: {sku}")
    variant = {
        "sku": sku,
        "color": color.title() if color.islower() else color,
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
        name=name.title() if name.islower() else name,
        description=description,
        brand=BRAND,
        material=material,
        capacity="",
        source_url=url,
        tags=["balenciaga", "bags"],
        variants=[variant],
        details=details,
        dimensions={},
        currency="USD",
        product_group_id=sku[:12],
        selected_sku=sku,
    )
    write_info_json(info, output_dir)
    print(
        f"  wrote info.json name={name!r} sku={sku} price={price} images={len(image_files)}"
    )
    return {
        "sku": sku,
        "name": name,
        "url": url,
        "images": len(image_files),
        "dir": output_dir,
    }


def scrape_one(url: str, output_dir: str) -> dict:
    print(f"Fetching {url}")
    html = fetch_html(url, settle=3.0)
    if "Access Denied" in html:
        raise RuntimeError("Access Denied (Akamai)")
    ld = parse_json_ld_product(html)
    variation = scrape_variation(url) or {}
    product_obj = variation.get("product") if isinstance(variation, dict) else None
    if not isinstance(product_obj, dict):
        product_obj = {}

    sku = str(
        product_obj.get("styleMaterialColor")
        or product_obj.get("variationGroupId")
        or product_obj.get("id")
        or ld.get("sku")
        or sku_from_url(url)
    )
    name = strip_html(
        str(
            product_obj.get("productTitle")
            or product_obj.get("productName")
            or ld.get("name")
            or ""
        )
    )
    color = ""
    for attr in product_obj.get("variationAttributes") or []:
        if isinstance(attr, dict) and attr.get("selectedValue"):
            color = strip_html(str(attr["selectedValue"]))
            break
    if not color:
        color = strip_html(str(ld.get("color") or ""))
    price = None
    price_obj = product_obj.get("price") or {}
    if isinstance(price_obj, dict):
        sales = price_obj.get("sales") or price_obj.get("list") or {}
        if isinstance(sales, dict):
            price = normalize_price(sales.get("value") or sales.get("decimalPrice"))
    if price is None:
        offers = ld.get("offers") if isinstance(ld.get("offers"), dict) else {}
        price = normalize_price(offers.get("price"))
    material = strip_html(
        str(product_obj.get("composition") or product_obj.get("compositions") or "")
    )
    if not material:
        for match in re.findall(r"Material:\s*([^<\n]+)", html, re.I):
            material = strip_html(match)
            break
    description = strip_html(
        str(
            product_obj.get("longDescription")
            or product_obj.get("shortDescription")
            or ld.get("description")
            or ""
        )
    )
    image_urls = images_from_variation(product_obj) or images_from_html(html, sku)
    return write_product(
        {
            "sku": sku,
            "name": name,
            "color": color,
            "price": price,
            "material": material,
            "description": description,
            "url": url,
            "images": image_urls,
        },
        output_dir,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape Balenciaga bag PDPs")
    parser.add_argument("urls", nargs="*")
    parser.add_argument("-o", "--output-dir", default="images/balenciaga")
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
