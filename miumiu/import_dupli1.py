#!/usr/bin/env python3
"""Import scraped Miu Miu bags into manage.dupli1.com (products + source images)."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

BASE = os.environ.get("DUPLI1_BASE", "https://manage.dupli1.com").rstrip("/")
EMAIL = os.environ.get("DUPLI1_EMAIL", "agent@dupli1.com")
PASSWORD = os.environ.get("DUPLI1_PASSWORD", "")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
IMAGES = os.path.join(ROOT, "images", "miumiu")
CATALOG = os.path.join(IMAGES, "catalog.json")
REPORT = "/tmp/dupli1-miumiu-import-report.json"
BRAND_CODE = "MM"
BRAND_NAME = "Miu Miu"

COLOR_CODE_MAP = {
    "black": "BLK",
    "white": "WHT",
    "beige": "BGE",
    "blue": "BLU",
    "brown": "BRN",
    "cream": "CRM",
    "gold": "GLD",
    "green": "GRN",
    "grey": "GRY",
    "gray": "GRY",
    "navy": "NVY",
    "olive": "OLV",
    "orange": "ORG",
    "pink": "PNK",
    "purple": "PRP",
    "red": "RED",
    "silver": "SLV",
    "tan": "TAN",
    "yellow": "YLW",
    "burgundy": "BUR",
    "cognac": "BRN",
    "oak": "TAN",
    "palisander": "BRN",
    "briarwood": "BRN",
    "aviation blue": "BLU",
    "begonia pink": "PNK",
    "sand beige": "BGE",
    "chalk white": "WHT",
    "fern green": "GRN",
    "caramel": "TAN",
    "opal": "WHT",
    "citron yellow": "YLW",
    "granite gray": "GRY",
    "dark brown": "BRN",
    "amaranth red": "RED",
}


def style_code_for(sku: str, info: dict | None = None) -> str:
    """Dupli1 style codes are max 12 chars and unique per (brand, style)."""
    if info:
        group = str(info.get("productGroupId") or "")
        if group and len(group) <= 12:
            return group
    parts = (sku or "").split("_")
    if len(parts) >= 2:
        code = f"{parts[0]}_{parts[1]}"
        return code[:12]
    return (sku or "")[:12]


def color_code_for(color: str) -> tuple[str, str]:
    """Return (colorCode, colorName) for Dupli1 catalog."""
    raw = (color or "").strip() or "Black"
    lower = raw.lower()
    if lower in COLOR_CODE_MAP:
        return COLOR_CODE_MAP[lower], raw
    if "/" in lower or "&" in lower or "," in lower or " and " in lower:
        for key, code in COLOR_CODE_MAP.items():
            if lower.startswith(key):
                return code, raw
        return "MLT", raw
    for key, code in COLOR_CODE_MAP.items():
        if key in lower:
            return code, raw
    letters = "".join(ch for ch in raw.upper() if ch.isalpha())
    code = (letters[:3] or "UNK").ljust(3, "X")
    return code, raw


class Dupli1:
    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.token = ""
        self._login()

    def _login(self) -> None:
        login = self.request(
            "POST",
            "/api/v1/auth/login",
            {"email": self.email, "password": self.password},
        )
        tok = self.request(
            "POST",
            "/api/v1/auth/refresh",
            {"refresh_token": login["refresh_token"]},
        )
        self.token = tok["token"]

    def request(
        self,
        method: str,
        path: str,
        data: Any = None,
        raw: bytes | None = None,
        content_type: str | None = None,
        retries: int = 3,
    ) -> Any:
        headers = {
            "Accept": "application/json",
            "User-Agent": "dupli1-miumiu-importer/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body: bytes | None = None
        if raw is not None:
            body = raw
            if content_type:
                headers["Content-Type"] = content_type
        elif data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"

        last: Exception | None = None
        for attempt in range(retries):
            req = urllib.request.Request(
                BASE + path, data=body, headers=headers, method=method
            )
            try:
                with urllib.request.urlopen(req, timeout=180) as response:
                    payload = response.read()
                    if not payload:
                        return None
                    try:
                        return json.loads(payload)
                    except json.JSONDecodeError:
                        return payload.decode("utf-8", errors="replace")
            except urllib.error.HTTPError as error:
                raw_body = error.read()
                try:
                    payload = json.loads(raw_body)
                except json.JSONDecodeError:
                    payload = raw_body.decode("utf-8", errors="replace")
                if (
                    error.code == 401
                    and attempt == 0
                    and path not in {"/api/v1/auth/login", "/api/v1/auth/refresh"}
                ):
                    self.token = ""
                    self._login()
                    continue
                if error.code in {429, 502, 503, 504} and attempt + 1 < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise RuntimeError(f"{method} {path} -> {error.code}: {payload}") from error
            except Exception as error:  # noqa: BLE001
                last = error
                time.sleep(2 * (attempt + 1))
        assert last is not None
        raise last

    def ensure_brand(self) -> None:
        brands = self.request("GET", "/api/v1/catalog/brands") or []
        codes = {item.get("code") for item in brands if isinstance(item, dict)}
        if BRAND_CODE in codes:
            return
        try:
            self.request(
                "POST",
                "/api/v1/catalog/brands",
                {"code": BRAND_CODE, "name": BRAND_NAME},
            )
            print(f"created brand {BRAND_CODE}", flush=True)
        except RuntimeError as error:
            if "409" in str(error) or "already" in str(error).lower():
                return
            raise

    def ensure_style(self, code: str, name: str) -> None:
        existing = (
            self.request("GET", f"/api/v1/catalog/brands/{BRAND_CODE}/styles") or []
        )
        codes = {item.get("code") for item in existing if isinstance(item, dict)}
        if code in codes:
            return
        try:
            self.request(
                "POST",
                f"/api/v1/catalog/brands/{BRAND_CODE}/styles",
                {"code": code, "name": name},
            )
            print(f"  created style {BRAND_CODE}/{code} ({name})", flush=True)
        except RuntimeError as error:
            if "409" in str(error) or "already exists" in str(error).lower():
                return
            raise

    def ensure_color(self, code: str, name: str) -> None:
        existing = self.request("GET", "/api/v1/catalog/colors") or []
        codes = {item.get("code") for item in existing if isinstance(item, dict)}
        if code in codes:
            return
        try:
            self.request("POST", "/api/v1/catalog/colors", {"code": code, "name": name})
            print(f"  created color {code} ({name})", flush=True)
        except RuntimeError as error:
            if "409" in str(error) or "already exists" in str(error).lower():
                return
            raise

    def find_product_by_style(self, style_code: str) -> dict | None:
        listing = self.request("GET", "/api/v1/products?limit=200")
        for item in (listing or {}).get("results") or []:
            if (
                item.get("styleCode") == style_code
                and item.get("brandCode") == BRAND_CODE
            ):
                return item
        return None

    def find_product_by_variant_sku(self, sku: str) -> dict | None:
        listing = self.request("GET", "/api/v1/products?limit=200")
        for item in (listing or {}).get("results") or []:
            urls = item.get("imageUrls") or []
            if any(f"/{sku}/" in (url or "") for url in urls):
                return item
            if item.get("styleCode") == style_code_for(sku) and item.get(
                "brandCode"
            ) == BRAND_CODE:
                return item
        return None

    def upload_image(self, product_id: str, sku: str, path: str) -> dict:
        filename = os.path.basename(path)
        filedata = open(path, "rb").read()
        if filedata[:3] != b"\xff\xd8\xff":
            kind = "webp" if filedata[:4] == b"RIFF" else "unknown"
            raise RuntimeError(f"{path} is not a JPEG (detected {kind})")
        boundary = "----Dupli1BoundaryMiuMiuUpload"
        preamble = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
            f"Content-Type: image/jpeg\r\n\r\n"
        ).encode("utf-8")
        epilogue = f"\r\n--{boundary}--\r\n".encode("utf-8")
        body = preamble + filedata + epilogue
        return self.request(
            "POST",
            f"/api/v1/products/{product_id}/variants/{sku}/images",
            raw=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )


def load_targets() -> list[dict]:
    if os.path.exists(CATALOG):
        catalog = json.load(open(CATALOG, encoding="utf-8"))
        rows = catalog.get("top15") or catalog.get("products") or []
        return rows
    rows = []
    for sku in sorted(os.listdir(IMAGES)):
        path = os.path.join(IMAGES, sku, "info.json")
        if os.path.exists(path):
            info = json.load(open(path, encoding="utf-8"))
            rows.append(
                {
                    "sku": sku,
                    "name": (info.get("product") or {}).get("name"),
                    "dir": os.path.join(IMAGES, sku),
                }
            )
    return rows


def import_one(client: Dupli1, sku: str, folder: str | None = None) -> dict:
    folder = folder or os.path.join(IMAGES, sku)
    info = json.load(open(os.path.join(folder, "info.json"), encoding="utf-8"))
    product = info.get("product") or {}
    name = product.get("name") or sku
    style_code = style_code_for(sku, info)
    client.ensure_style(style_code, name)

    selected = next(
        (v for v in info.get("variants") or [] if v.get("selected")), None
    )
    if not selected:
        selected = next(
            (v for v in info.get("variants") or [] if v.get("images")), None
        )
    if not selected:
        raise RuntimeError(f"no selected variant with images for {sku}")
    variant_sku = selected["sku"]

    existing = client.find_product_by_style(
        style_code
    ) or client.find_product_by_variant_sku(variant_sku)
    if existing:
        product_id = existing["id"]
        have = len(existing.get("imageUrls") or [])
        print(f"  exists product={product_id} images={have}", flush=True)
        if have == 0:
            color_code, color_name = color_code_for(selected.get("color") or "")
            client.ensure_color(color_code, color_name)
            vpayload = {
                "sku": variant_sku,
                "color": color_name,
                "colorCode": color_code,
                "size": selected.get("size") or "",
                "price": selected.get("price"),
                "status": selected.get("status") or "active",
            }
            try:
                client.request(
                    "POST", f"/api/v1/products/{product_id}/variants", vpayload
                )
                print(
                    f"  created variant {variant_sku} color={color_code}", flush=True
                )
            except RuntimeError as error:
                print(f"  variant create note: {error}", flush=True)
    else:
        payload = {
            "name": name,
            "description": product.get("description") or "",
            "brand": product.get("brand") or BRAND_NAME,
            "brandCode": BRAND_CODE,
            "styleCode": style_code,
            "material": product.get("material") or "",
            "category": product.get("category") or "bags",
            "capacity": product.get("capacity") or "",
            "status": product.get("status") or "draft",
            "tags": product.get("tags") or ["miumiu", "bags"],
        }
        created = client.request("POST", "/api/v1/products", payload)
        product_id = created["id"]
        have = 0
        print(f"  created product={product_id} style={style_code}", flush=True)
        color_code, color_name = color_code_for(selected.get("color") or "")
        client.ensure_color(color_code, color_name)
        vpayload = {
            "sku": variant_sku,
            "color": color_name,
            "colorCode": color_code,
            "size": selected.get("size") or "",
            "price": selected.get("price"),
            "status": selected.get("status") or "active",
        }
        client.request("POST", f"/api/v1/products/{product_id}/variants", vpayload)
        print(f"  created variant {variant_sku} color={color_code}", flush=True)

    local_images = [
        os.path.join(folder, filename)
        for filename in (selected.get("images") or [])
        if os.path.exists(os.path.join(folder, filename))
    ]
    if not local_images:
        # Fall back to any JPEGs in the folder.
        local_images = sorted(
            os.path.join(folder, name)
            for name in os.listdir(folder)
            if name.lower().endswith(".jpg")
        )

    uploaded = 0
    last_urls: list[str] = list((existing or {}).get("imageUrls") or [])
    if have < len(local_images):
        for path in local_images[have:]:
            result = client.upload_image(product_id, variant_sku, path)
            uploaded += 1
            last_urls = list(result.get("imageUrls") or last_urls)
            print(
                f"    uploaded {os.path.basename(path)} ({len(last_urls)})",
                flush=True,
            )
    else:
        print(f"  images already complete ({have})", flush=True)

    summary = client.find_product_by_variant_sku(variant_sku) or {}
    image_count = len(summary.get("imageUrls") or last_urls or [])
    return {
        "sku": variant_sku,
        "name": name,
        "productId": product_id,
        "styleCode": style_code,
        "price": summary.get("price") or selected.get("price"),
        "images": image_count,
        "uploaded": uploaded,
        "url": f"{BASE}/products/{product_id}",
    }


def main() -> int:
    if not PASSWORD:
        print("Set DUPLI1_PASSWORD", file=sys.stderr)
        return 2
    if not os.path.isdir(IMAGES):
        print(f"missing images dir: {IMAGES}", file=sys.stderr)
        return 2

    client = Dupli1(EMAIL, PASSWORD)
    me = client.request("GET", "/api/v1/auth/me")
    print(
        f"authenticated as {me.get('email')} ({me.get('account_type')})",
        flush=True,
    )
    client.ensure_brand()

    targets = load_targets()
    report: list[dict] = []
    for i, row in enumerate(targets, start=1):
        sku = row["sku"]
        folder = row.get("dir") or os.path.join(IMAGES, sku)
        print(f"\n[{i}/{len(targets)}] {sku} {row.get('name')!r}", flush=True)
        try:
            result = import_one(client, sku, folder=folder)
            result["ok"] = True
            report.append(result)
            print(
                f"  OK images={result['images']} product={result['productId']}",
                flush=True,
            )
        except Exception as error:  # noqa: BLE001
            print(f"  FAIL {error}", flush=True)
            report.append(
                {
                    "sku": sku,
                    "name": row.get("name"),
                    "ok": False,
                    "error": str(error),
                }
            )
        time.sleep(0.3)

    json.dump(report, open(REPORT, "w", encoding="utf-8"), indent=2)
    ok = [r for r in report if r.get("ok")]
    print(f"\nDone. {len(ok)}/{len(report)} products imported. Report: {REPORT}")
    for row in ok:
        print(
            f"  {row['sku']} imgs={row['images']} {row['productId']}",
            flush=True,
        )
    return 0 if len(ok) == len(report) else 2


if __name__ == "__main__":
    raise SystemExit(main())
