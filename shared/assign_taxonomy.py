#!/usr/bin/env python3
"""Assign Dupli1 bag master-catalog taxonomy to all products.

Master catalog codes (from elug3/dupli1 docs/product-master-catalog.md):
  subCategory: handbags | tote | shoulder | cross | mini
  style:       casual | evening | business | weekend | statement
  target:      all | men | women | kids

Brand is preserved from the existing product (brand / brandCode).
Applies via full PUT /api/v1/products/{id} payloads.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any

sys.path.insert(0, os.path.dirname(__file__))
from import_dupli1 import Dupli1, EMAIL, PASSWORD  # noqa: E402

DEFAULT_OUT = os.path.join(os.path.dirname(__file__), "taxonomy_assignments.json")


def _name(product: dict) -> str:
    return (product.get("name") or "").lower()


def _blob(product: dict) -> str:
    """Name-first text; description only for weak secondary signals."""
    return " ".join(
        [
            product.get("name") or "",
            product.get("styleCode") or "",
            " ".join(product.get("tags") or []),
        ]
    ).lower()


def classify_subcategory(product: dict) -> str:
    name = _name(product)
    blob = _blob(product)

    # Wearable packs (no dedicated backpack code in master catalog).
    if re.search(r"백팩|backpack", name):
        return "shoulder"

    # Classic / structured handbags — name beats strap wording in descriptions.
    if re.search(
        r"갤러리아|galleria|알마|alma|카퓌신|capucines|보니|bonnie|"
        r"클래식|classic|보이 샤넬|boy chanel|2\.55|"
        r"chanel 19|chanel 22|chanel 25|플랩|flap|"
        r"핸드백|handbag|볼링|bowling|박스백|"
        r"브리프|briefcase|sac a depeches|in-the-loop",
        name,
    ):
        if re.search(r"미니|mini|마이크로|micro", name):
            return "mini"
        return "handbags"

    # Mini / micro / compact pieces (name only — avoid "파우치" inside descriptions).
    if re.search(
        r"미니|mini|마이크로|micro|스피디 18|speedy 18|icarino|"
        r"체인 지갑|체인 베니티|wallet on chain|"
        r"클러치|clutch|파우치|pouch|"
        r"\bbb\b|온더고 bb|캐리올 bb|picotin",
        name,
    ):
        if re.search(r"네버풀|neverfull", name):
            return "tote"
        return "mini"

    # Totes / shoppers.
    if re.search(
        r"토트|tote|네버풀|neverfull|온더고|onthego|쇼핑|쇼퍼|shopper|"
        r"맥시 토트|맥시 쇼퍼|icare|크로셰|린넨|아이비|ivy",
        name,
    ):
        if re.search(r"호보|hobo", name):
            return "shoulder"
        return "tote"

    # Shoulder / hobo / messenger / flamenco.
    if re.search(
        r"숄더|shoulder|호보|hobo|메신저|messenger|플라멩코|flamenco|"
        r"버킷|bucket|jypsiere|horseback|videpoches|"
        r"완더|wander|아방뛰르|aventure|herbag|펄스|purse|스카프|scarf|"
        r"르 시티 백|르 셋|로데오|비방|vivant|"
        r"스피릿|spirit|아르카디|arcadie|자르디니에르|jardini",
        name,
    ):
        return "shoulder"

    # Explicit crossbody in the name.
    if re.search(r"크로스|crossbody|cross body|반둘리에|bandouli", name):
        return "cross"

    # Weak fallback from tags / styleCode only.
    if re.search(r"백팩|backpack", blob):
        return "shoulder"
    return "handbags"


def classify_style(product: dict) -> str:
    name = _name(product)

    if re.search(r"클러치|clutch|mombasa", name):
        return "evening"

    if re.search(
        r"브리프|briefcase|depeches|메신저|messenger|"
        r"갤러리아|galleria|kelly dep",
        name,
    ):
        return "business"

    if re.search(r"백팩|backpack|트래블|travel|cargo|위크엔드|weekend", name):
        return "weekend"

    if re.search(
        r"보이 샤넬|boy chanel|로데오|rodeo|버클|buckle|"
        r"카퓌신|capucines|2\.55",
        name,
    ):
        return "statement"

    return "casual"


def classify_target(product: dict) -> str:
    name = _name(product)

    if re.search(
        r"sac a depeches|depeches light|etriviere|herbag messenger|"
        r"steve light|tablier sellier",
        name,
    ):
        return "men"

    if re.search(r"kids|키즈|아동", name):
        return "kids"

    if re.search(r"캐리 올 트래블|travel bag", name):
        return "all"

    return "women"


def assign(product: dict) -> dict[str, str]:
    return {
        "subCategory": classify_subcategory(product),
        "style": classify_style(product),
        "target": classify_target(product),
        "brand": product.get("brand") or "",
        "brandCode": product.get("brandCode") or "",
    }


def build_assignments(products: list[dict]) -> list[dict]:
    rows = []
    for p in products:
        tax = assign(p)
        rows.append(
            {
                "id": p["id"],
                "name": p.get("name") or "",
                "brand": tax["brand"],
                "brandCode": tax["brandCode"],
                "styleCode": p.get("styleCode") or "",
                "category": p.get("category") or "bags",
                "status": p.get("status") or "draft",
                "material": p.get("material") or "",
                "description": p.get("description") or "",
                "tags": p.get("tags") or [],
                "subCategory": tax["subCategory"],
                "style": tax["style"],
                "target": tax["target"],
            }
        )
    return rows


def apply_rows(client: Dupli1, rows: list[dict], sleep: float = 0.05) -> dict:
    ok = fail = 0
    report = []
    for i, row in enumerate(rows, 1):
        payload = {
            "name": row["name"],
            "description": row.get("description") or "",
            "brand": row["brand"],
            "brandCode": row["brandCode"],
            "styleCode": row["styleCode"],
            "material": row.get("material") or "",
            "category": row.get("category") or "bags",
            "status": row.get("status") or "draft",
            "tags": row.get("tags") or [],
            "subCategory": row["subCategory"],
            "style": row["style"],
            "target": row["target"],
        }
        print(
            f"[{i}/{len(rows)}] {row['brandCode']} {row['styleCode']}: "
            f"{row['name'][:40]!r} -> "
            f"sub={row['subCategory']} style={row['style']} "
            f"target={row['target']} brand={row['brandCode']}",
            flush=True,
        )
        try:
            client.request("PUT", f"/api/v1/products/{row['id']}", payload)
            ok += 1
            report.append({"id": row["id"], "ok": True, **payload})
        except Exception as error:  # noqa: BLE001
            fail += 1
            print(f"  FAIL {error}", flush=True)
            report.append({"id": row["id"], "ok": False, "error": str(error)})
        time.sleep(sleep)
    return {"ok": ok, "fail": fail, "report": report}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.05)
    args = parser.parse_args()

    if not PASSWORD and args.apply:
        print("Set DUPLI1_PASSWORD", file=sys.stderr)
        return 2

    client = Dupli1(EMAIL, PASSWORD)
    products = client.list_products(force=True)
    if args.limit:
        products = products[: args.limit]

    rows = build_assignments(products)
    json.dump(rows, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"wrote {len(rows)} assignments -> {args.out}")

    from collections import Counter

    print("subCategory", Counter(r["subCategory"] for r in rows))
    print("style", Counter(r["style"] for r in rows))
    print("target", Counter(r["target"] for r in rows))
    print("brandCode", Counter(r["brandCode"] for r in rows))

    if args.dry_run or not args.apply:
        return 0

    result = apply_rows(client, rows, sleep=args.sleep)
    out = "/tmp/dupli1-taxonomy-apply-report.json"
    json.dump(result["report"], open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"done ok={result['ok']} fail={result['fail']} report={out}")
    return 1 if result["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
