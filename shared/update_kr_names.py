#!/usr/bin/env python3
"""Apply official Korean product names on manage.dupli1.com.

Reads shared/kr_names.json and PUTs full product payloads (name + existing
description/status/tags) so partial updates do not wipe other fields.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from import_dupli1 import Dupli1, EMAIL, PASSWORD  # noqa: E402

DEFAULT_UPDATES = os.path.join(os.path.dirname(__file__), "kr_names.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--updates", default=DEFAULT_UPDATES)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only-changed", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.05)
    args = parser.parse_args()

    if not PASSWORD and not args.dry_run:
        print("Set DUPLI1_PASSWORD", file=sys.stderr)
        return 2

    rows = json.load(open(args.updates, encoding="utf-8"))
    if args.only_changed:
        rows = [r for r in rows if r.get("changed") or r.get("oldName") != r.get("newName")]
    if args.limit:
        rows = rows[: args.limit]

    client = None if args.dry_run else Dupli1(EMAIL, PASSWORD)
    ok = fail = 0
    report = []
    for i, row in enumerate(rows, 1):
        pid = row["id"]
        name = row.get("newName") or row.get("name")
        print(
            f"[{i}/{len(rows)}] {row.get('brandCode')} {row.get('styleCode')}: "
            f"{row.get('oldName')!r} -> {name!r}",
            flush=True,
        )
        payload = {
            "name": name,
            "description": row.get("description") or "",
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
            report.append({"id": pid, "ok": True, "dryRun": True, "name": name})
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

    out = "/tmp/dupli1-kr-names-apply-report.json"
    json.dump(report, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"done ok={ok} fail={fail} report={out}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
