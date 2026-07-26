#!/usr/bin/env python3
"""Replace the uploaded images of Louis Vuitton products on Dupli1.

Image upload appends to a variant's imageUrls, and a variant update replaces
the list when a non-empty one is sent. So each product is refreshed by
uploading the freshly scraped JPEGs and then pinning imageUrls to just those.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared"))
from import_dupli1 import Dupli1, EMAIL, PASSWORD  # noqa: E402

BRAND_CODE = "LV"


def local_images(images_dir: str, reference: str) -> list[str]:
    return sorted(glob.glob(os.path.join(images_dir, reference, "image_*.jpg")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--images-dir", default="images/louisvuitton")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if not PASSWORD and args.apply:
        print("Set DUPLI1_PASSWORD", file=sys.stderr)
        return 2

    client = Dupli1(EMAIL, PASSWORD)
    products = [p for p in client.list_products(force=True) if p["brandCode"] == BRAND_CODE]
    if args.limit:
        products = products[: args.limit]
    print(f"{len(products)} {BRAND_CODE} products")

    report = []
    ok = fail = 0
    for i, product in enumerate(products, 1):
        reference = product["styleCode"]
        files = local_images(args.images_dir, reference)
        print(f"[{i}/{len(products)}] {reference} {product['name'][:30]!r} "
              f"{len(product.get('imageUrls') or [])} old -> {len(files)} new")
        if not files:
            print("  no local images, skipped")
            report.append({"id": product["id"], "ok": False, "error": "no local images"})
            fail += 1
            continue
        if not args.apply:
            continue

        sku = None
        try:
            detail = client.request("GET", f"/api/v1/products/{product['id']}")
            variants = detail.get("variants") or []
            sku = variants[0].get("sku") if variants else None
        except Exception:  # noqa: BLE001 - draft products 404 on the public PDP
            sku = None
        if not sku:
            sku = reference

        try:
            uploaded = []
            for path in files:
                result = client.upload_image(product["id"], sku, path)
                urls = (result or {}).get("imageUrls") or []
                uploaded = urls
                time.sleep(0.05)
            fresh = uploaded[-len(files):] if len(uploaded) >= len(files) else uploaded
            client.request(
                "PUT",
                f"/api/v1/products/{product['id']}/variants/{sku}",
                {"imageUrls": fresh},
            )
            print(f"  uploaded {len(files)}, imageUrls pinned to {len(fresh)}")
            report.append({"id": product["id"], "ok": True, "images": len(fresh)})
            ok += 1
        except Exception as error:  # noqa: BLE001
            print(f"  FAIL {error}")
            report.append({"id": product["id"], "ok": False, "error": str(error)})
            fail += 1

    out = "/tmp/dupli1-lv-image-replace-report.json"
    json.dump(report, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\ndone ok={ok} fail={fail} report={out}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
