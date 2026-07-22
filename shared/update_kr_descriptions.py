#!/usr/bin/env python3
"""Apply Korean product descriptions to manage.dupli1.com.

Reads shared/kr_descriptions.json and PUTs full product payloads so partial
updates do not wipe name/status/tags. Descriptions are expected to already be
cleaned (no strap-drop / material dumps) and translated to natural Korean.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from import_dupli1 import Dupli1, EMAIL, PASSWORD  # noqa: E402

DEFAULT_UPDATES = os.path.join(os.path.dirname(__file__), "kr_descriptions.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--updates", default=DEFAULT_UPDATES)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only-brand", default="")
    parser.add_argument("--sleep", type=float, default=0.05)
    args = parser.parse_args()

    if not PASSWORD and not args.dry_run:
        print("Set DUPLI1_PASSWORD", file=sys.stderr)
        return 2

    rows = json.load(open(args.updates, encoding="utf-8"))
    if args.only_brand:
        code = args.only_brand.upper()
        rows = [r for r in rows if (r.get("brandCode") or "").upper() == code]
    if args.limit:
        rows = rows[: args.limit]

    client = None if args.dry_run else Dupli1(EMAIL, PASSWORD)
    ok = 0
    fail = 0
    report = []

    for i, row in enumerate(rows, 1):
        pid = row["id"]
        name = row["name"]
        desc = (row.get("description") or "").strip()
        print(
            f"[{i}/{len(rows)}] {row.get('brandCode')} {row.get('styleCode')}: "
            f"{name[:40]!r} desc_len={len(desc)}",
            flush=True,
        )
        payload = {
            "name": name,
            "description": desc,
            "brand": row["brand"],
            "brandCode": row["brandCode"],
            "styleCode": row["styleCode"],
            "material": row.get("material") or "",
            "category": row.get("category") or "bags",
            "status": row.get("status") or "draft",
            "tags": row.get("tags") or [],
        }
        if args.dry_run:
            ok += 1
            report.append({"id": pid, "ok": True, "dryRun": True})
            continue
        try:
            assert client is not None
            client.request("PUT", f"/api/v1/products/{pid}", payload)
            ok += 1
            report.append({"id": pid, "ok": True, "name": name})
        except Exception as error:  # noqa: BLE001
            fail += 1
            print(f"  FAIL {error}", flush=True)
            report.append({"id": pid, "ok": False, "error": str(error)})
        time.sleep(args.sleep)

    out = "/tmp/dupli1-kr-desc-apply-report.json"
    json.dump(report, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"done ok={ok} fail={fail} report={out}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
