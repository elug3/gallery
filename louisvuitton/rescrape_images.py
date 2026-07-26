#!/usr/bin/env python3
"""Re-download Louis Vuitton packshots as clean JPEGs.

LV serves Scene7 assets with a transparent background. Converting those
straight to JPEG turns every transparent pixel black, which is what made the
uploaded catalog images look broken. Here the alpha channel is composited onto
white before the JPEG is written.

Scene7 keys the asset on the product reference only — the slug in the path is
ignored — so images can be fetched from just a reference plus a view token:

    /images/is/image/lv/1/PP_VP_L/lv--<REF>_<VIEW>.png?wid=2000
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ASSET_BASE = "https://www.louisvuitton.cn/images/is/image/lv/1/PP_VP_L/"
WIDTH = 2000

# Ordered so the front packshot leads and lifestyle shots trail.
VIEW_TOKENS = [
    "PM2_Front view",
    "PM1_Side view",
    "PM1_Back view",
    "PM1_Interior view",
    "PM1_Interior2 view",
    "PM1_Closeup view",
    "PM1_Detail view",
    "PM1_Other view",
    "PM1_Other view2",
    "PM1_Worn view",
    "PM1_Cropped worn view",
    "PM1_Ambiance view",
    "PM1_edito",
]


def asset_url(reference: str, view: str, width: int = WIDTH) -> str:
    name = urllib.parse.quote(f"lv--{reference}_{view}.png", safe="")
    return f"{ASSET_BASE}{name}?wid={width}"


def fetch(url: str, timeout: int = 60) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code in {404, 410}:
            return None
        raise


def flatten_to_jpeg(data: bytes, quality: int = 92) -> bytes:
    """Composite any alpha channel onto white, then encode JPEG."""
    image = Image.open(io.BytesIO(data))
    image.load()
    if image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.split()[-1])
        image = canvas
    else:
        image = image.convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def scrape_reference(reference: str, out_dir: str, limit: int = 8) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    seen: set[str] = set()
    saved: list[str] = []
    for view in VIEW_TOKENS:
        if len(saved) >= limit:
            break
        try:
            raw = fetch(asset_url(reference, view))
        except Exception as error:  # noqa: BLE001
            print(f"    {view}: error {str(error)[:50]}")
            continue
        if not raw:
            continue
        digest = hashlib.md5(raw).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        jpeg = flatten_to_jpeg(raw)
        index = len(saved) + 1
        path = os.path.join(out_dir, f"image_{index:02d}.jpg")
        with open(path, "wb") as handle:
            handle.write(jpeg)
        saved.append(path)
        print(f"    {view:24} -> {os.path.basename(path)} ({len(jpeg) // 1024} KB)")
        time.sleep(0.15)
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refs",
        default="",
        help="comma-separated references (default: read products file)",
    )
    parser.add_argument("--products", default="/tmp/lvcheck/lv-products.json")
    parser.add_argument("-o", "--output-dir", default="images/louisvuitton")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()

    if args.refs:
        refs = [r.strip() for r in args.refs.split(",") if r.strip()]
        names = {r: "" for r in refs}
    else:
        products = json.load(open(args.products, encoding="utf-8"))
        names = {p["styleCode"]: p.get("name", "") for p in products}
        refs = list(names)

    catalog = []
    for i, ref in enumerate(refs, 1):
        print(f"[{i}/{len(refs)}] {ref} {names.get(ref, '')}")
        out_dir = os.path.join(args.output_dir, ref)
        saved = scrape_reference(ref, out_dir, limit=args.limit)
        catalog.append({"reference": ref, "name": names.get(ref, ""), "dir": out_dir, "images": len(saved)})
        if not saved:
            print("    NO IMAGES")

    os.makedirs(args.output_dir, exist_ok=True)
    path = os.path.join(args.output_dir, "catalog.json")
    json.dump(
        {"brand": "Louis Vuitton", "top": catalog},
        open(path, "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=2,
    )
    total = sum(c["images"] for c in catalog)
    empty = [c["reference"] for c in catalog if not c["images"]]
    print(f"\n{total} images across {len(catalog)} references -> {path}")
    if empty:
        print(f"references with no images: {empty}")
    return 1 if empty else 0


if __name__ == "__main__":
    raise SystemExit(main())
