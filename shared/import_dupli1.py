#!/usr/bin/env python3
"""Import scraped luxury bags into manage.dupli1.com (products + JPEG images)."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from typing import Any

from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BASE = os.environ.get("DUPLI1_BASE", "https://manage.dupli1.com").rstrip("/")
EMAIL = os.environ.get("DUPLI1_EMAIL", "agent@dupli1.com")
PASSWORD = os.environ.get("DUPLI1_PASSWORD", "")

BRANDS = {
    "balenciaga": {
        "code": "BAL",
        "name": "Balenciaga",
        "dir": "images/balenciaga",
        "tags": ["balenciaga", "bags"],
    },
    "hermes": {
        "code": "HER",
        "name": "Hermes",
        "dir": "images/hermes",
        "tags": ["hermes", "bags"],
    },
    "loewe": {
        "code": "LOE",
        "name": "Loewe",
        "dir": "images/loewe",
        "tags": ["loewe", "bags"],
    },
    "ysl": {
        "code": "YSL",
        "name": "Saint Laurent",
        "dir": "images/ysl",
        "tags": ["ysl", "saint-laurent", "bags"],
    },
}

COLOR_CODE_MAP = {
    "black": "BLK",
    "white": "WHT",
    "optic white": "WHT",
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
    "petal pink": "PNK",
    "purple": "PRP",
    "red": "RED",
    "berry red": "RED",
    "silver": "SLV",
    "tan": "TAN",
    "new tan": "TAN",
    "yellow": "YLW",
    "burgundy": "BUR",
    "cognac": "BRN",
    "caramel": "TAN",
    "camel": "CML",
    "espresso": "BRN",
    "light espresso": "BRN",
    "volcanic rock": "GRY",
    "biscuit": "BGE",
    "cowboy": "TAN",
    "tan cowboy": "TAN",
    "ebony": "BLK",
    "taupe": "TAU",
    "khaki": "KHK",
    "sand": "SND",
    "natural": "NAT",
    "ochre": "OCR",
    "terracotta": "TER",
    "ecru": "ECR",
}


def ascii_fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def style_code_for(sku: str, info: dict | None = None) -> str:
    """Dupli1 style codes: uppercase alphanumeric, max 12 chars.

    Prefer the product SKU so each colorway/PDP stays unique. Short Hermès
    ``productGroupId`` values like ``C041`` collide across unrelated bags.
    When the SKU is longer than 12 chars, use first 6 + last 6 of the
    alphanumeric form so trailing color suffixes stay distinct.
    """
    cleaned = re.sub(r"[^A-Za-z0-9]", "", sku or "").upper()
    if not cleaned and info:
        cleaned = re.sub(
            r"[^A-Za-z0-9]", "", str(info.get("productGroupId") or "")
        ).upper()
    if not cleaned:
        return "UNKNOWN"
    if len(cleaned) <= 12:
        return cleaned
    return cleaned[:6] + cleaned[-6:]


def color_code_for(color: str) -> tuple[str, str]:
    raw = (color or "").strip() or "Black"
    lower = raw.lower()
    folded = ascii_fold(lower)
    if lower in COLOR_CODE_MAP:
        return COLOR_CODE_MAP[lower], raw
    if folded in COLOR_CODE_MAP:
        return COLOR_CODE_MAP[folded], raw
    for key, code in sorted(COLOR_CODE_MAP.items(), key=lambda item: -len(item[0])):
        if key in lower or key in folded:
            return code, raw
    # Multi-color strings like "écru/noir/noir" → primary segment
    primary = re.split(r"[/,]| and ", folded)[0].strip()
    if primary in COLOR_CODE_MAP:
        return COLOR_CODE_MAP[primary], raw
    letters = "".join(ch for ch in ascii_fold(raw).upper() if ch.isalpha())
    code = (letters[:3] or "UNK").ljust(3, "X")
    return code, raw


def size_for_dupli1(raw: str) -> str:
    """Bags use empty Dupli1 size; skip soft labels like 'Small model'."""
    value = (raw or "").strip()
    if not value:
        return ""
    lower = value.lower()
    if "model" in lower or "one size" in lower or lower in {"tu", "u", "os", "uni"}:
        return ""
    # Only keep values that already look like catalog size codes.
    if re.fullmatch(r"[A-Za-z0-9]{1,12}", value):
        return value.upper()
    return ""


def ensure_jpeg(path: str) -> str:
    """Return a filesystem path to a JPEG (convert WebP/PNG if needed)."""
    data = open(path, "rb").read(16)
    if data[:3] == b"\xff\xd8\xff":
        return path
    image = Image.open(path)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    elif image.mode == "L":
        image = image.convert("RGB")
    dest = os.path.splitext(path)[0] + ".dupli1.jpg"
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=92, optimize=True)
    open(dest, "wb").write(buf.getvalue())
    return dest


class Dupli1:
    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.token = ""
        self._products_cache: list[dict] | None = None
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
            "User-Agent": "dupli1-luxury-importer/1.0",
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

    def list_products(self, force: bool = False) -> list[dict]:
        if self._products_cache is not None and not force:
            return self._products_cache
        all_rows: list[dict] = []
        offset = 0
        while True:
            page = self.request("GET", f"/api/v1/products?limit=100&offset={offset}") or {}
            rows = page.get("results") or []
            all_rows.extend(rows)
            total = int(page.get("total") or 0)
            offset += len(rows)
            if not rows or offset >= total:
                break
        self._products_cache = all_rows
        return all_rows

    def invalidate_products(self) -> None:
        self._products_cache = None

    def ensure_brand(self, code: str, name: str) -> None:
        brands = self.request("GET", "/api/v1/catalog/brands") or []
        codes = {item.get("code") for item in brands if isinstance(item, dict)}
        if code in codes:
            return
        try:
            self.request(
                "POST",
                "/api/v1/catalog/brands",
                {"code": code, "name": name},
            )
            print(f"created brand {code} ({name})", flush=True)
        except RuntimeError as error:
            if "409" in str(error) or "already" in str(error).lower():
                return
            raise

    def ensure_style(self, brand_code: str, code: str, name: str) -> None:
        existing = (
            self.request("GET", f"/api/v1/catalog/brands/{brand_code}/styles") or []
        )
        codes = {item.get("code") for item in existing if isinstance(item, dict)}
        if code in codes:
            return
        try:
            self.request(
                "POST",
                f"/api/v1/catalog/brands/{brand_code}/styles",
                {"code": code, "name": name},
            )
            print(f"  created style {brand_code}/{code} ({name})", flush=True)
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

    def find_product(self, brand_code: str, style_code: str, variant_sku: str) -> dict | None:
        for item in self.list_products():
            if item.get("brandCode") == brand_code and item.get("styleCode") == style_code:
                return item
        for item in self.list_products():
            urls = item.get("imageUrls") or []
            if any(f"/{variant_sku}/" in (url or "") for url in urls):
                return item
            if item.get("brandCode") == brand_code and variant_sku in (
                item.get("defaultImageUrl") or ""
            ):
                return item
        return None

    def upload_image(self, product_id: str, sku: str, path: str) -> dict:
        jpeg_path = ensure_jpeg(path)
        filename = os.path.basename(jpeg_path)
        if not filename.lower().endswith((".jpg", ".jpeg")):
            filename = os.path.splitext(filename)[0] + ".jpg"
        filedata = open(jpeg_path, "rb").read()
        if filedata[:3] != b"\xff\xd8\xff":
            raise RuntimeError(f"{jpeg_path} is not a JPEG after conversion")
        boundary = "----Dupli1BoundaryLuxuryUpload"
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


def load_targets(images_dir: str) -> list[dict]:
    catalog_path = os.path.join(images_dir, "catalog.json")
    if os.path.exists(catalog_path):
        catalog = json.load(open(catalog_path, encoding="utf-8"))
        rows = catalog.get("top") or catalog.get("top15") or catalog.get("products") or []
        out = []
        for row in rows:
            sku = row["sku"]
            folder = row.get("dir") or os.path.join(images_dir, sku)
            if not os.path.isabs(folder):
                folder = os.path.join(ROOT, folder)
            if not os.path.isdir(folder):
                folder = os.path.join(images_dir, sku)
            out.append({"sku": sku, "name": row.get("name"), "dir": folder})
        return out
    rows = []
    for sku in sorted(os.listdir(images_dir)):
        path = os.path.join(images_dir, sku, "info.json")
        if os.path.exists(path):
            info = json.load(open(path, encoding="utf-8"))
            rows.append(
                {
                    "sku": sku,
                    "name": (info.get("product") or {}).get("name"),
                    "dir": os.path.join(images_dir, sku),
                }
            )
    return rows


def import_one(client: Dupli1, brand: dict, sku: str, folder: str) -> dict:
    info = json.load(open(os.path.join(folder, "info.json"), encoding="utf-8"))
    product = info.get("product") or {}
    name = product.get("name") or sku
    brand_code = brand["code"]
    brand_name = brand["name"]
    style_code = style_code_for(sku, info)
    client.ensure_style(brand_code, style_code, name)

    selected = next((v for v in info.get("variants") or [] if v.get("selected")), None)
    if not selected:
        selected = next((v for v in info.get("variants") or [] if v.get("images")), None)
    if not selected:
        raise RuntimeError(f"no selected variant with images for {sku}")
    variant_sku = selected["sku"]

    existing = client.find_product(brand_code, style_code, variant_sku)
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
                "size": size_for_dupli1(selected.get("size") or ""),
                "price": selected.get("price"),
                "status": selected.get("status") or "active",
            }
            try:
                client.request(
                    "POST", f"/api/v1/products/{product_id}/variants", vpayload
                )
                print(f"  created variant {variant_sku} color={color_code}", flush=True)
            except RuntimeError as error:
                print(f"  variant create note: {error}", flush=True)
    else:
        payload = {
            "name": name,
            "description": product.get("description") or "",
            "brand": brand_name,
            "brandCode": brand_code,
            "styleCode": style_code,
            "material": product.get("material") or "",
            "category": product.get("category") or "bags",
            "capacity": product.get("capacity") or "",
            "status": product.get("status") or "draft",
            "tags": product.get("tags") or brand["tags"],
        }
        created = client.request("POST", "/api/v1/products", payload)
        product_id = created["id"]
        have = 0
        client.invalidate_products()
        print(f"  created product={product_id} style={style_code}", flush=True)
        color_code, color_name = color_code_for(selected.get("color") or "")
        client.ensure_color(color_code, color_name)
        vpayload = {
            "sku": variant_sku,
            "color": color_name,
            "colorCode": color_code,
            "size": size_for_dupli1(selected.get("size") or ""),
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
        local_images = sorted(
            os.path.join(folder, name)
            for name in os.listdir(folder)
            if name.lower().endswith((".jpg", ".jpeg", ".webp", ".png"))
            and ".dupli1." not in name.lower()
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

    client.invalidate_products()
    summary = client.find_product(brand_code, style_code, variant_sku) or {}
    image_count = len(summary.get("imageUrls") or last_urls or [])
    return {
        "sku": variant_sku,
        "name": name,
        "brand": brand_name,
        "brandCode": brand_code,
        "productId": product_id,
        "styleCode": style_code,
        "price": summary.get("price") or selected.get("price"),
        "images": image_count,
        "uploaded": uploaded,
        "url": f"{BASE}/products/{product_id}",
    }


def import_brand(client: Dupli1, key: str) -> list[dict]:
    brand = BRANDS[key]
    images_dir = os.path.join(ROOT, brand["dir"])
    if not os.path.isdir(images_dir):
        print(f"missing images dir: {images_dir}", file=sys.stderr)
        return [{"ok": False, "brand": key, "error": f"missing {images_dir}"}]

    client.ensure_brand(brand["code"], brand["name"])
    targets = load_targets(images_dir)
    print(f"\n=== {brand['name']} ({len(targets)} products) ===", flush=True)
    report: list[dict] = []
    for i, row in enumerate(targets, start=1):
        sku = row["sku"]
        folder = row["dir"]
        print(f"\n[{key} {i}/{len(targets)}] {sku} {row.get('name')!r}", flush=True)
        try:
            result = import_one(client, brand, sku, folder=folder)
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
                    "brand": brand["name"],
                    "ok": False,
                    "error": str(error),
                }
            )
        time.sleep(0.25)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload scraped bags to Dupli1")
    parser.add_argument(
        "brands",
        nargs="*",
        choices=sorted(BRANDS.keys()),
        help="Brand keys to import (default: all four)",
    )
    args = parser.parse_args(argv)
    if not PASSWORD:
        print("Set DUPLI1_PASSWORD", file=sys.stderr)
        return 2

    keys = args.brands or list(BRANDS.keys())
    client = Dupli1(EMAIL, PASSWORD)
    me = client.request("GET", "/api/v1/auth/me")
    print(
        f"authenticated as {me.get('email')} ({me.get('account_type')})",
        flush=True,
    )

    report: list[dict] = []
    for key in keys:
        report.extend(import_brand(client, key))

    out = "/tmp/dupli1-luxury-import-report.json"
    json.dump(report, open(out, "w", encoding="utf-8"), indent=2)
    ok = [r for r in report if r.get("ok")]
    print(f"\nDone. {len(ok)}/{len(report)} products imported. Report: {out}")
    for row in ok:
        print(
            f"  [{row.get('brandCode')}] {row['sku']} imgs={row['images']} {row['url']}",
            flush=True,
        )
    failed = [r for r in report if not r.get("ok")]
    for row in failed:
        print(f"  FAIL {row.get('sku')}: {row.get('error')}", flush=True)
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
