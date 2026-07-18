#!/usr/bin/env python3
"""Update Dupli1 products to official Korean names and KRW prices.

Reads shared/kr_updates.json (sku → koreanName / krwPrice) and applies:
  PUT /api/v1/products/{id}
  PUT /api/v1/products/{id}/variants/{sku}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from import_dupli1 import Dupli1, EMAIL, PASSWORD, color_code_for  # noqa: E402

DEFAULT_UPDATES = os.path.join(os.path.dirname(__file__), "kr_updates.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--updates",
        default=DEFAULT_UPDATES,
        help="JSON array of update rows (from a previous mapping run)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if not PASSWORD:
        print("Set DUPLI1_PASSWORD", file=sys.stderr)
        return 2

    rows = json.load(open(args.updates, encoding="utf-8"))
    if args.limit:
        rows = rows[: args.limit]

    client = Dupli1(EMAIL, PASSWORD)
    ok = 0
    fail = 0
    for i, row in enumerate(rows, 1):
        pid = row["id"]
        sku = row["sku"]
        name = row["newName"]
        price = int(row["newPrice"])
        color_code, color_name = color_code_for(row.get("color") or "Black")
        if row.get("colorCode"):
            color_code = row["colorCode"]
            color_name = row.get("color") or color_name

        print(
            f"[{i}/{len(rows)}] {row.get('brand')} {sku}: "
            f"{row.get('oldName')} -> {name} / {row.get('oldPrice')} -> {price}",
            flush=True,
        )
        if args.dry_run:
            ok += 1
            continue

        try:
            client.request(
                "PUT",
                f"/api/v1/products/{pid}",
                {
                    "name": name,
                    "description": row.get("description") or "",
                    "brand": row["brand"],
                    "brandCode": row["brandCode"],
                    "styleCode": row["styleCode"],
                    "material": row.get("material") or "",
                    "category": row.get("category") or "bags",
                    "status": row.get("status") or "draft",
                    "tags": row.get("tags") or [],
                },
            )
            client.ensure_color(color_code, color_name)
            client.request(
                "PUT",
                f"/api/v1/products/{pid}/variants/{sku}",
                {
                    "sku": sku,
                    "color": color_name,
                    "colorCode": color_code,
                    "size": row.get("size") or "One Size",
                    "sizeCode": row.get("sizeCode") or "OS",
                    "price": price,
                    "status": row.get("status") or "draft",
                },
            )
            ok += 1
        except Exception as error:  # noqa: BLE001
            fail += 1
            print(f"  FAIL {error}", flush=True)
        time.sleep(0.05)

    print(f"done ok={ok} fail={fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
