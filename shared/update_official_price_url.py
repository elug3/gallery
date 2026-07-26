#!/usr/bin/env python3
"""Set official reference price + official site URL on Dupli1 products.

Writes two things per parent product (merge-on-update, so other fields keep
their values):

  officialPrice                        -> KRW reference/list price (integer)
  attributes.product_official_site_url -> product page on the brand's own site

Source data: shared/official_prices.json (one row per product id).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from import_dupli1 import Dupli1, EMAIL, PASSWORD  # noqa: E402

DEFAULT_DATA = os.path.join(os.path.dirname(__file__), "official_prices.json")
URL_ATTR = "product_official_site_url"

BRAND_DOMAIN = {
    "LV": "louisvuitton.com",
    "PRA": "prada.com",
    "HER": "hermes.com",
    "CH": "chanel.com",
    "LOE": "loewe.com",
    "MM": "miumiu.com",
    "YSL": "ysl.com",
    "BAL": "balenciaga.com",
}


def validate(rows: list[dict]) -> list[str]:
    errors = []
    seen = set()
    for r in rows:
        pid = r.get("id")
        if not pid or pid in seen:
            errors.append(f"missing/duplicate id: {pid}")
            continue
        seen.add(pid)
        price = r.get("officialPriceKrw")
        if not isinstance(price, int) or price <= 0:
            errors.append(f"{pid}: bad officialPriceKrw {price!r}")
        url = (r.get("officialUrl") or "").strip()
        domain = BRAND_DOMAIN.get(r.get("brandCode", ""))
        if not url.startswith("https://"):
            errors.append(f"{pid}: url not https: {url!r}")
        elif domain and domain not in url:
            errors.append(f"{pid}: url not on {domain}: {url!r}")
        if len(url) > 512:
            errors.append(f"{pid}: url exceeds 512 chars")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=DEFAULT_DATA)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.05)
    args = parser.parse_args()

    rows = json.load(open(args.data, encoding="utf-8"))
    if args.limit:
        rows = rows[: args.limit]

    errors = validate(rows)
    if errors:
        print(f"{len(errors)} validation errors:", file=sys.stderr)
        for e in errors[:20]:
            print("  " + e, file=sys.stderr)
        return 2
    print(f"validated {len(rows)} rows")

    if not args.apply:
        print("dry run (pass --apply to write)")
        return 0
    if not PASSWORD:
        print("Set DUPLI1_PASSWORD", file=sys.stderr)
        return 2

    client = Dupli1(EMAIL, PASSWORD)
    live = {p["id"]: p for p in client.list_products(force=True)}

    ok = fail = 0
    report = []
    for i, row in enumerate(rows, 1):
        pid = row["id"]
        price = row["officialPriceKrw"]
        url = row["officialUrl"].strip()
        current = live.get(pid) or {}
        attrs = dict(current.get("attributes") or {})
        attrs[URL_ATTR] = url

        print(
            f"[{i}/{len(rows)}] {row.get('brandCode'):4} {row.get('styleCode'):16} "
            f"₩{price:>10,} {url[:70]}",
            flush=True,
        )
        try:
            client.request(
                "PUT",
                f"/api/v1/products/{pid}",
                {"officialPrice": price, "attributes": attrs},
            )
            ok += 1
            report.append({"id": pid, "ok": True, "officialPrice": price, "url": url})
        except Exception as error:  # noqa: BLE001
            fail += 1
            print(f"  FAIL {error}", flush=True)
            report.append({"id": pid, "ok": False, "error": str(error)})
        time.sleep(args.sleep)

    out = "/tmp/dupli1-official-price-apply-report.json"
    json.dump(report, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"done ok={ok} fail={fail} report={out}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
