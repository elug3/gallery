#!/usr/bin/env python3
"""Scrape Balenciaga US bag PDPs via Chrome CDP into info.json + images."""

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
LISTING_URL = "https://www.balenciaga.com/en-us/women/bags-for-women"
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


def ecom_image_url(url: str) -> str:
    url = url.replace("/Large/", "/eCom/").replace("/Large-", "/eCom-")
    url = url.replace("/Medium/", "/eCom/").replace("/Medium2/", "/eCom/")
    return url


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
        for href in re.findall(r'href="([^"]+\.html)"', html):
            if "/en-us/" not in href and not href.startswith("/"):
                continue
            full = urllib.parse.urljoin("https://www.balenciaga.com", href).split("?")[0]
            if not full.endswith(".html"):
                continue
            if "/women/" in full or "/men/" in full:
                continue  # category pages
            # PDPs are like /en-us/slug-SKU.html
            if full.count("/") < 4:
                continue
            if full in seen:
                continue
            # Skip obvious non-product
            if any(x in full for x in ["/bags-for-", "/handbags", "/view-all"]):
                continue
            seen.add(full)
            urls.append(full)
            if len(urls) >= limit:
                return urls
    return urls or list(DEFAULT_URLS)[:limit]


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


def sku_from_url(url: str) -> str:
    leaf = url.rstrip("/").rsplit("/", 1)[-1].replace(".html", "")
    # trailing SKU-like token after last hyphen group often includes letters+digits
    match = re.search(r"([A-Z0-9]{8,})$", leaf)
    return match.group(1) if match else leaf


def scrape_variation(url: str) -> dict | None:
    sku = sku_from_url(url)
    # Try Demandware Product-Variation via in-page fetch after navigating PDP.
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


def images_from_html(html: str, sku: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in re.findall(
        r"https://balenciaga\.dam\.kering\.com/[^\"'\s]+",
        html,
    ):
        url = ecom_image_url(html_lib.unescape(match.split("?")[0]))
        if sku and sku not in url and sku[:10] not in url:
            # still allow if eCom path
            if "/eCom/" not in url and "/eCom-" not in url:
                continue
        if url not in seen:
            seen.add(url)
            urls.append(url if "?" in match else url)
    # Prefer eCom only
    ecom = [u for u in urls if "/eCom/" in u or "/eCom-" in u]
    return ecom or urls


def scrape_one(url: str, output_dir: str) -> dict:
    print(f"Fetching {url}")
    html = fetch_html(url, settle=3.0)
    ld = parse_json_ld_product(html)
    variation = scrape_variation(url) or {}
    product = variation.get("product") if isinstance(variation, dict) else None
    if not isinstance(product, dict):
        product = {}

    sku = str(
        product.get("styleMaterialColor")
        or product.get("variationGroupId")
        or product.get("id")
        or ld.get("sku")
        or sku_from_url(url)
    )
    name = strip_html(
        str(product.get("productTitle") or product.get("productName") or ld.get("name") or "")
    )
    color = ""
    for attr in product.get("variationAttributes") or []:
        if isinstance(attr, dict) and attr.get("selectedValue"):
            color = strip_html(str(attr["selectedValue"]))
            break
    if not color:
        color = strip_html(str(ld.get("color") or ""))
    price = None
    price_obj = product.get("price") or {}
    if isinstance(price_obj, dict):
        sales = price_obj.get("sales") or price_obj.get("list") or {}
        if isinstance(sales, dict):
            price = normalize_price(sales.get("value") or sales.get("decimalPrice"))
    if price is None:
        offers = ld.get("offers") if isinstance(ld.get("offers"), dict) else {}
        price = normalize_price(offers.get("price"))
    material = strip_html(
        str(product.get("composition") or product.get("compositions") or "")
    )
    if not material:
        for match in re.findall(r"Material:\s*([^<\n]+)", html, re.I):
            material = strip_html(match)
            break
    description = strip_html(
        str(product.get("longDescription") or product.get("shortDescription") or ld.get("description") or "")
    )
    dimensions: dict[str, str] = {}
    for key in ("pdpDimension", "pdpDimension2"):
        raw = product.get(key)
        if raw:
            dimensions[key] = strip_html(str(raw))
    unit = product.get("dimensionDataUnit")
    if unit and dimensions:
        dimensions = {k: f"{v} {unit}" for k, v in dimensions.items()}

    image_urls: list[str] = []
    for item in product.get("akeneoImages") or []:
        if isinstance(item, dict) and item.get("url"):
            image_urls.append(ecom_image_url(str(item["url"])))
        elif isinstance(item, str):
            image_urls.append(ecom_image_url(item))
    if not image_urls:
        image_urls = images_from_html(html, sku)
    # unique
    seen: set[str] = set()
    uniq = []
    for u in image_urls:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    image_urls = uniq

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

    details = [description] if description else []
    if material:
        details.append(f"Material: {material}")
    details.append(f"Product code: {sku}")
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
        capacity=" × ".join(dimensions.values()),
        source_url=url,
        tags=["balenciaga", "bags"],
        variants=[variant],
        details=details,
        dimensions=dimensions,
        currency="USD",
        product_group_id=sku[:12],
        selected_sku=sku,
    )
    write_info_json(info, output_dir)
    print(f"  wrote info.json name={name!r} sku={sku} price={price} images={len(image_files)}")
    return {"sku": sku, "name": name, "url": url, "images": len(image_files), "dir": output_dir}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape Balenciaga bag PDPs via Chrome CDP")
    parser.add_argument("urls", nargs="*")
    parser.add_argument("-o", "--output-dir", default="images/balenciaga")
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
