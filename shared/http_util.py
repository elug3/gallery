#!/usr/bin/env python3
"""Shared HTTP / image helpers for luxury brand scrapers."""

from __future__ import annotations

import html as html_lib
import json
import os
import re
import urllib.error
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

ONE_SIZE_VALUES = frozenset({"tu", "one size", "onesize", "os", "u", "uni"})


def fetch(
    url: str,
    *,
    timeout: int = 60,
    headers: dict | None = None,
    referer: str | None = None,
) -> bytes:
    hdrs = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        hdrs["Referer"] = referer
    if headers:
        hdrs.update(headers)
    request = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def strip_html(text: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html_lib.unescape(without_tags)).strip()


def normalize_size(raw: str) -> str:
    value = (raw or "").strip()
    if value.lower() in ONE_SIZE_VALUES:
        return ""
    return value


def normalize_price(raw: object) -> float | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace(",", "").replace("$", "")
    try:
        return float(text)
    except ValueError:
        return None


def capacity_from_dimensions(dimensions: dict[str, str]) -> str:
    order = ("height", "width", "length", "depth")
    parts = [dimensions[key] for key in order if key in dimensions]
    if not parts:
        parts = list(dimensions.values())
    return " × ".join(parts)


def write_info_json(info: dict, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    dest = os.path.join(output_dir, "info.json")
    with open(dest, "w", encoding="utf-8") as handle:
        json.dump(info, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return dest


def download_bytes(url: str, dest: str, *, accept: str = "image/jpeg,*/*;q=0.8") -> int:
    data = fetch(url, headers={"Accept": accept})
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "wb") as handle:
        handle.write(data)
    return len(data)


def extension_for(data: bytes, fallback: str = ".jpg") -> str:
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    return fallback


def download_image(url: str, dest_no_ext: str) -> str | None:
    """Download image and write with correct extension from magic bytes."""
    try:
        data = fetch(
            url,
            headers={"Accept": "image/jpeg,image/png,image/webp,*/*;q=0.8"},
        )
    except (urllib.error.URLError, urllib.error.HTTPError):
        return None
    ext = extension_for(data)
    dest = dest_no_ext if dest_no_ext.endswith(ext) else dest_no_ext + ext
    # If caller passed .jpg but content is webp, replace extension.
    if dest_no_ext.endswith((".jpg", ".jpeg", ".png", ".webp")):
        base, _ = os.path.splitext(dest_no_ext)
        dest = base + ext
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "wb") as handle:
        handle.write(data)
    return dest


def build_info(
    *,
    name: str,
    description: str,
    brand: str,
    material: str,
    capacity: str,
    source_url: str,
    tags: list[str],
    variants: list[dict],
    details: list[str] | None = None,
    dimensions: dict | None = None,
    currency: str = "USD",
    product_group_id: str = "",
    selected_sku: str = "",
) -> dict:
    available_colors: list[str] = []
    available_sizes: list[str] = []
    for variant in variants:
        color = variant.get("color") or ""
        if color and color not in available_colors:
            available_colors.append(color)
        if variant.get("selected"):
            size = variant.get("size") or ""
            if size and size not in available_sizes:
                available_sizes.append(size)
    return {
        "product": {
            "name": name,
            "description": description,
            "brand": brand,
            "material": material,
            "category": "bags",
            "capacity": capacity,
            "status": "draft",
            "tags": tags,
            "sourceUrl": source_url,
        },
        "variants": variants,
        "availableColors": available_colors,
        "availableSizes": available_sizes,
        "details": details or [],
        "dimensions": dimensions or {},
        "currency": currency,
        "productGroupId": product_group_id,
        "selectedSku": selected_sku,
    }
