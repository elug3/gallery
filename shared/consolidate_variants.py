#!/usr/bin/env python3
"""Consolidate duplicate/similar Dupli1 products into one parent + color/size variants.

Groups products by brand + normalized name (strips trailing 백/가방/bag).
For each group with 2+ live products:
  - keep one parent (prefer active, most images, higher officialPrice)
  - POST each other product's variants onto the keeper (reuse CDN imageUrls)
  - set loser parents to status=archived

Color/size collisions (same colorCode+sizeCode already on keeper) are resolved by:
  - Boy Chanel "small" → sizeCode SML
  - otherwise editionCode A (alternate construction)

Usage:
  DUPLI1_PASSWORD=… python3 shared/consolidate_variants.py --dry-run
  DUPLI1_PASSWORD=… python3 shared/consolidate_variants.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from typing import Any

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from shared.import_dupli1 import Dupli1  # noqa: E402


def norm_name(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[\s\-_/·•]+", " ", s)
    s = re.sub(r"[^\w가-힣a-z0-9 ]", "", s)
    return s.strip()


def base_name(s: str) -> str:
    n = norm_name(s)
    n = re.sub(r"(백|가방|bag|handbag)$", "", n).strip()
    return n


def image_count(product: dict) -> int:
    urls = product.get("imageUrls") or []
    if urls:
        return len(urls)
    total = 0
    for v in product.get("variants") or []:
        total += len(v.get("imageUrls") or [])
    return total


def keeper_score(product: dict) -> tuple:
    status = product.get("status") or ""
    active_bonus = 2 if status == "active" else (1 if status == "draft" else 0)
    ofic = int(product.get("officialPrice") or 0)
    price = int(product.get("price") or 0)
    return (active_bonus, image_count(product), ofic, price, product.get("id") or "")


def option_key(variant: dict, *, size_code: str | None = None, edition: str | None = None) -> tuple:
    return (
        (variant.get("colorCode") or "").upper(),
        (size_code if size_code is not None else (variant.get("sizeCode") or "OS")).upper(),
        (edition if edition is not None else (variant.get("editionCode") or "")).upper(),
    )


def hints_small(product: dict, variant: dict) -> bool:
    blob = " ".join(
        [
            product.get("name") or "",
            product.get("description") or "",
            product.get("styleCode") or "",
            variant.get("sku") or "",
            ((product.get("attributes") or {}).get("product_official_site_url") or ""),
        ]
    ).lower()
    return bool(
        re.search(r"\bsmall\b", blob)
        or "스몰" in blob
        or "/small-" in blob
        or re.search(r"a67085", blob)
    )


def resolve_collision(
    product: dict,
    variant: dict,
    occupied: set[tuple],
) -> dict[str, str] | None:
    """Return size/edition overrides so the option key is free, or None if impossible."""
    color = (variant.get("colorCode") or "").upper()
    size = (variant.get("sizeCode") or "OS").upper() or "OS"
    edition = (variant.get("editionCode") or "").upper()

    candidates: list[tuple[str, str]] = [(size, edition)]
    if hints_small(product, variant):
        candidates.append(("SML", edition))
    # Prefer alternate construction before inventing more sizes.
    for ed in ("A", "R", "V"):
        candidates.append((size, ed))
        if hints_small(product, variant):
            candidates.append(("SML", ed))
    candidates.append(("MED", edition))
    candidates.append(("MIN", edition))

    seen: set[tuple[str, str]] = set()
    for size_c, ed_c in candidates:
        key = (size_c, ed_c)
        if key in seen:
            continue
        seen.add(key)
        opt = (color, size_c, ed_c)
        if opt not in occupied:
            return {"sizeCode": size_c, "editionCode": ed_c}
    return None


def size_display(size_code: str) -> str:
    mapping = {
        "OS": "One Size",
        "SML": "Small",
        "MED": "Medium",
        "MIN": "Mini",
        "LRG": "Large",
        "S": "S",
        "M": "M",
        "L": "L",
        "XS": "XS",
        "XL": "XL",
    }
    return mapping.get(size_code.upper(), size_code)


def build_groups(products: list[dict]) -> dict[tuple[str, str], list[dict]]:
    by: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for p in products:
        if (p.get("status") or "") == "archived":
            continue
        brand = p.get("brand") or ""
        by[(brand, base_name(p.get("name") or ""))].append(p)
    return {k: v for k, v in by.items() if len(v) > 1}


def plan_group(client: Dupli1, items: list[dict]) -> dict[str, Any]:
    full = [client.request("GET", f"/api/v1/products/{p['id']}") for p in items]
    full.sort(key=keeper_score, reverse=True)
    keeper = full[0]
    losers = full[1:]

    occupied = {option_key(v) for v in (keeper.get("variants") or [])}
    actions: list[dict[str, Any]] = []

    for loser in losers:
        for variant in loser.get("variants") or []:
            key = option_key(variant)
            overrides: dict[str, str] = {}
            if key in occupied:
                resolved = resolve_collision(loser, variant, occupied)
                if not resolved:
                    actions.append(
                        {
                            "op": "skip_variant",
                            "fromProductId": loser["id"],
                            "sku": variant.get("sku"),
                            "color": variant.get("color"),
                            "reason": "color/size/edition collision",
                        }
                    )
                    continue
                overrides = resolved
                # Fill size display when we changed size code.
                if overrides.get("sizeCode") and overrides["sizeCode"] != (
                    variant.get("sizeCode") or "OS"
                ):
                    overrides["size"] = size_display(overrides["sizeCode"])

            payload = {
                "color": variant.get("color") or "",
                "colorCode": variant.get("colorCode") or "",
                "size": overrides.get("size")
                or variant.get("size")
                or size_display(overrides.get("sizeCode") or variant.get("sizeCode") or "OS"),
                "sizeCode": overrides.get("sizeCode") or variant.get("sizeCode") or "OS",
                "status": variant.get("status") or "active",
                "imageUrls": list(variant.get("imageUrls") or []),
            }
            if overrides.get("editionCode") or variant.get("editionCode"):
                payload["editionCode"] = overrides.get("editionCode") or variant.get(
                    "editionCode"
                )

            occupied.add(
                option_key(
                    variant,
                    size_code=payload["sizeCode"],
                    edition=payload.get("editionCode") or "",
                )
            )
            actions.append(
                {
                    "op": "create_variant",
                    "fromProductId": loser["id"],
                    "fromSku": variant.get("sku"),
                    "payload": payload,
                }
            )
        actions.append({"op": "archive", "productId": loser["id"], "name": loser.get("name")})

    return {
        "brand": keeper.get("brand"),
        "name": keeper.get("name"),
        "base": base_name(keeper.get("name") or ""),
        "keeperId": keeper["id"],
        "keeperStyle": keeper.get("styleCode"),
        "loserIds": [p["id"] for p in losers],
        "actions": actions,
    }


def apply_plan(client: Dupli1, plan: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    keeper_id = plan["keeperId"]
    for action in plan["actions"]:
        if action["op"] == "create_variant":
            payload = action["payload"]
            if not payload.get("colorCode"):
                results.append({**action, "ok": False, "error": "missing colorCode"})
                continue
            if dry_run:
                results.append({**action, "ok": True, "dryRun": True})
                continue
            try:
                client.ensure_color(payload["colorCode"], payload.get("color") or payload["colorCode"])
                created = client.request(
                    "POST", f"/api/v1/products/{keeper_id}/variants", payload
                )
                results.append(
                    {
                        **action,
                        "ok": True,
                        "createdSku": created.get("sku") if isinstance(created, dict) else None,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                results.append({**action, "ok": False, "error": str(exc)})
        elif action["op"] == "archive":
            if dry_run:
                results.append({**action, "ok": True, "dryRun": True})
                continue
            try:
                updated = client.request(
                    "PUT", f"/api/v1/products/{action['productId']}", {"status": "archived"}
                )
                status = updated.get("status") if isinstance(updated, dict) else None
                results.append({**action, "ok": status == "archived", "status": status})
            except Exception as exc:  # noqa: BLE001
                results.append({**action, "ok": False, "error": str(exc)})
        else:
            results.append({**action, "ok": True})
    return {**plan, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write changes to Dupli1")
    parser.add_argument("--dry-run", action="store_true", help="Plan only (default)")
    parser.add_argument(
        "--report",
        default=os.path.join(ROOT, "shared", "consolidate_variants_report.json"),
        help="Write JSON report path",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    password = os.environ.get("DUPLI1_PASSWORD", "")
    if not password:
        print("Set DUPLI1_PASSWORD", file=sys.stderr)
        return 2

    client = Dupli1(os.environ.get("DUPLI1_EMAIL", "agent@dupli1.com"), password)
    products = client.list_products(force=True)
    groups = build_groups(products)
    print(f"live products={len(products)} duplicate groups={len(groups)} dry_run={dry_run}")

    plans = []
    for (brand, base), items in sorted(
        groups.items(), key=lambda x: (-len(x[1]), x[0][0], x[0][1])
    ):
        plan = plan_group(client, items)
        applied = apply_plan(client, plan, dry_run=dry_run)
        plans.append(applied)
        creates = sum(1 for a in applied["actions"] if a["op"] == "create_variant")
        archives = sum(1 for a in applied["actions"] if a["op"] == "archive")
        skips = sum(1 for a in applied["actions"] if a["op"] == "skip_variant")
        failed = sum(1 for r in applied["results"] if not r.get("ok"))
        print(
            f"  {brand} | {plan['name']}: keeper={plan['keeperId']} "
            f"+{creates} variants archive={archives} skip={skips} fail={failed}",
            flush=True,
        )

    report = {
        "dryRun": dry_run,
        "groupCount": len(plans),
        "plans": plans,
    }
    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"wrote {args.report}")

    if not dry_run:
        # Refresh and summarize.
        products = client.list_products(force=True)
        by_status: dict[str, int] = defaultdict(int)
        for p in products:
            by_status[p.get("status") or "?"] += 1
        remaining = build_groups(products)
        print(f"after: status={dict(by_status)} remaining_duplicate_groups={len(remaining)}")
        for (brand, base), items in sorted(remaining.items()):
            print(f"  leftover {brand} | {base}: {[i['id'] for i in items]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
