#!/usr/bin/env python3
"""Scrape Loewe US bag PDPs into Dupli1-shaped info.json + source images."""

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
    capacity_from_dimensions,
    download_image,
    fetch,
    normalize_price,
    normalize_size,
    strip_html,
    write_info_json,
)

BRAND = "Loewe"
BAGS_PLP = "https://www.loewe.com/usa/en/women/bags"
DEFAULT_LIMIT = 18

DEFAULT_URLS = [
    "https://www.loewe.com/usa/en/women/bags/flamenco/medium-flamenco-purse-in-mellow-nappa-lambskin/A411FCRX75-0018.html",
    "https://www.loewe.com/usa/en/women/bags/flamenco/mini-flamenco-clutch-in-nappa-calfskin/A411FC2XA6-5557.html",
    "https://www.loewe.com/usa/en/women/bags/puzzle/small-puzzle-bag-in-embroidered-canvas/A510S21XDL-2423.html",
    "https://www.loewe.com/usa/en/women/bags/shoulder-bags/medium-scarf-bag-in-smooth-calfskin/ABNYBNMX01-1100.html",
    "https://www.loewe.com/usa/en/women/bags/mini-bags/cala-mini-bag-in-mellow-nappa-lambskin/A120HPLX03-0040.html",
    "https://www.loewe.com/usa/en/women/bags/totes/large-verano-tote-in-embroidered-canvas/A039ATMX02-2423.html",
]

PID_RE = re.compile(
    r"window\['__pid_([^']+)'\]\s*=\s*",
)


def extract_json_object(text: str, start: int) -> dict | None:
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_str = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_str:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return None
    return None


def parse_pid(html: str) -> dict | None:
    match = PID_RE.search(html)
    if not match:
        return None
    payload = extract_json_object(html, match.end())
    if not payload:
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else None


def discover_urls(limit: int = DEFAULT_LIMIT) -> list[str]:
    html = fetch(BAGS_PLP).decode("utf-8", errors="replace")
    urls: list[str] = []
    seen: set[str] = set()
    for href in re.findall(r'href="([^"]+\.html)"', html):
        if "/women/bags/" not in href:
            continue
        if href.count("/") < 6:
            continue
        full = urllib.parse.urljoin(BAGS_PLP, href).split("?")[0]
        sku = full.rstrip("/").rsplit("/", 1)[-1].replace(".html", "")
        if "-" not in sku:
            continue
        if full in seen:
            continue
        seen.add(full)
        urls.append(full)
        if len(urls) >= limit:
            break
    return urls or list(DEFAULT_URLS)[:limit]


def static_image_url(url: str) -> str:
    """Prefer Demandware static JPEG over transform endpoint."""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path
    if "/dw/image/v2/" in path:
        # /dw/image/v2/BBPC_PRD/on/demandware.static/-/... -> /on/demandware.static/-/...
        path = re.sub(r"^/dw/image/v2/[^/]+", "", path)
    return urllib.parse.urlunparse(
        (parsed.scheme or "https", parsed.netloc, path, "", "", "")
    )


def _image_src(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("disBaseLink", "src", "url", "link"):
            raw = value.get(key)
            if raw:
                return str(raw)
    return ""


def extract_images(data: dict) -> list[str]:
    attrs = data.get("customAttributes") or {}
    candidates: list[str] = []
    for key in ("c_allZoomImages", "c_allDetailImages"):
        block = attrs.get(key) or {}
        for device in ("desktop", "mobile"):
            values = block.get(device) or []
            if isinstance(values, list):
                for item in values:
                    src = _image_src(item)
                    if src:
                        candidates.append(src)
    for value in attrs.get("c_allImages") or []:
        src = _image_src(value)
        if src:
            candidates.append(src)
    seen: set[str] = set()
    urls: list[str] = []
    for raw in candidates:
        absolute = raw if raw.startswith("http") else urllib.parse.urljoin(
            "https://www.loewe.com", raw
        )
        url = static_image_url(absolute)
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def dimensions_from(data: dict) -> dict[str, str]:
    attrs = data.get("customAttributes") or {}
    dimensions: dict[str, str] = {}
    mapping = {
        "height": "c_LW_Height",
        "width": "c_LW_Width",
        "depth": "c_LW_Depth",
        "length": "c_LW_Length",
    }
    for key, attr in mapping.items():
        raw = attrs.get(attr)
        if raw is not None and str(raw).strip():
            value = str(raw).strip()
            dimensions[key] = value if re.search(r"[a-zA-Z]", value) else f"{value} cm"
    measures = attrs.get("c_LW_measures")
    if measures and not dimensions:
        dimensions["measures"] = strip_html(str(measures))
    return dimensions


def details_from(data: dict) -> list[str]:
    short = strip_html(str(data.get("shortDescription") or ""))
    parts = [part.strip(" *") for part in re.split(r"\*+|•|\n", short) if part.strip(" *")]
    attrs = data.get("customAttributes") or {}
    composition = attrs.get("c_LW_compositionName")
    if composition:
        parts.append(f"Composition: {strip_html(str(composition))}")
    return parts


def scrape_one(url: str, output_dir: str) -> dict:
    print(f"Fetching {url}")
    html = fetch(url).decode("utf-8", errors="replace")
    data = parse_pid(html)
    if not data:
        raise RuntimeError(f"no __pid_ payload for {url}")

    sku = str(data.get("id") or url.rstrip("/").rsplit("/", 1)[-1].replace(".html", ""))
    name = strip_html(str(data.get("name") or ""))
    attrs = data.get("customAttributes") or {}
    color = strip_html(str(attrs.get("c_LW_colorLabel") or ""))
    material = strip_html(str(attrs.get("c_LW_materialDescription") or ""))
    price = normalize_price(data.get("price"))
    currency = str(data.get("currency") or "USD")
    description = strip_html(str(data.get("shortDescription") or ""))
    dimensions = dimensions_from(data)
    details = details_from(data)
    details.append(f"Product code: {sku}")
    image_urls = extract_images(data)
    master = ""
    master_obj = data.get("master")
    if isinstance(master_obj, dict):
        master = str(master_obj.get("masterId") or "")
    if not master and "-" in sku:
        master = sku.split("-", 1)[0]

    os.makedirs(output_dir, exist_ok=True)
    image_files: list[str] = []
    saved_urls: list[str] = []
    for index, image_url in enumerate(image_urls, start=1):
        dest = download_image(image_url, os.path.join(output_dir, f"image_{index:02d}"))
        if not dest:
            print(f"  [{index}/{len(image_urls)}] FAILED {image_url}", file=sys.stderr)
            continue
        name_only = os.path.basename(dest)
        image_files.append(name_only)
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
    tags = ["loewe", "bags"]
    for token in re.findall(r"/bags/([^/]+)/", urllib.parse.urlparse(url).path):
        if token not in tags:
            tags.append(token)
    info = build_info(
        name=name,
        description=description,
        brand=BRAND,
        material=material,
        capacity=capacity_from_dimensions(dimensions),
        source_url=url,
        tags=tags,
        variants=[variant],
        details=details,
        dimensions=dimensions,
        currency=currency,
        product_group_id=master,
        selected_sku=sku,
    )
    write_info_json(info, output_dir)
    print(f"  wrote info.json name={name!r} sku={sku} price={price} images={len(image_files)}")
    return {"sku": sku, "name": name, "url": url, "images": len(image_files), "dir": output_dir}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape Loewe bag PDPs")
    parser.add_argument("urls", nargs="*")
    parser.add_argument("-o", "--output-dir", default="images/loewe")
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args(argv)

    if args.discover:
        for url in discover_urls(args.limit):
            print(url)
        return 0

    urls = args.urls or discover_urls(args.limit)
    if args.list_only:
        for url in urls:
            print(url)
        return 0

    catalog = []
    multi = len(urls) > 1
    for url in urls:
        sku = url.rstrip("/").rsplit("/", 1)[-1].replace(".html", "")
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
    return 0 if len(catalog) == len(urls) else 2


if __name__ == "__main__":
    raise SystemExit(main())
