#!/usr/bin/env python3
"""Re-upload Chanel product images via Dupli1's internal product API.

Run this on an EC2/ECS host that can resolve Cloud Map names:

  AUTH=http://auth.dupli1.local:8080
  PROD=http://product.dupli1.local:8080

Expected layout under CHANEL_JPEG_ROOT (default /tmp/chanel-reupload/chanel-jpeg):

  products.json          # list of {sku, productId, name, styleCode}
  <SKU>/info.json        # scraped Dupli1-shaped metadata
  <SKU>/image_*.jpg      # real JPEG bytes (FF D8 FF), not Chanel WebP

Convert Chanel f_auto WebP downloads first, e.g.:

  from PIL import Image
  Image.open(src).convert("RGB").save(dst, "JPEG", quality=92)

Environment:

  DUPLI1_EMAIL / DUPLI1_PASSWORD  — required
  AUTH / PROD / CHANEL_JPEG_ROOT  — optional overrides
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

AUTH = os.environ.get("AUTH", "http://auth.dupli1.local:8080")
PROD = os.environ.get("PROD", "http://product.dupli1.local:8080")
EMAIL = os.environ.get("DUPLI1_EMAIL", "agent@dupli1.com")
PASSWORD = os.environ.get("DUPLI1_PASSWORD", "")
ROOT = os.environ.get("CHANEL_JPEG_ROOT", "/tmp/chanel-reupload/chanel-jpeg")

COLOR_MAP = {
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
    "dark burgundy": "BUR",
    "dark red": "DRE",
    "dark khaki": "OLV",
    "dark blue": "NVY",
    "camel": "TAN",
    "ecru": "CRM",
    "silvery": "SLV",
}


def color_code(color: str) -> tuple[str, str]:
    raw = (color or "Black").strip()
    lower = raw.lower()
    if lower in COLOR_MAP:
        return COLOR_MAP[lower], raw
    if "&" in lower or "," in lower:
        for key, code in COLOR_MAP.items():
            if lower.startswith(key):
                return code, raw
        return "MLT", raw
    for key, code in COLOR_MAP.items():
        if key in lower:
            return code, raw
    letters = "".join(ch for ch in raw.upper() if ch.isalpha())
    return (letters[:3] or "UNK").ljust(3, "X"), raw


class Client:
    def __init__(self) -> None:
        self.token = ""
        self.login()

    def login(self) -> None:
        login = self._request(
            "POST",
            f"{AUTH}/api/v1/auth/login",
            {"email": EMAIL, "password": PASSWORD},
            auth=False,
        )
        tok = self._request(
            "POST",
            f"{AUTH}/api/v1/auth/refresh",
            {"refresh_token": login["refresh_token"]},
            auth=False,
        )
        self.token = tok["token"]

    def _request(
        self,
        method: str,
        url: str,
        data=None,
        raw: bytes | None = None,
        content_type: str | None = None,
        auth: bool = True,
        timeout: int = 180,
    ):
        headers = {"Accept": "application/json", "User-Agent": "ec2-internal-uploader/1.0"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = None
        if raw is not None:
            body = raw
            if content_type:
                headers["Content-Type"] = content_type
        elif data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
                return resp.status, (json.loads(payload) if payload else None)
        except urllib.error.HTTPError as error:
            payload = error.read()
            try:
                parsed = json.loads(payload)
            except Exception:
                parsed = payload.decode("utf-8", errors="replace")[:500]
            if error.code == 401 and auth:
                self.login()
                return self._request(
                    method,
                    url,
                    data=data,
                    raw=raw,
                    content_type=content_type,
                    auth=True,
                    timeout=timeout,
                )
            raise RuntimeError(f"{method} {url} -> {error.code}: {parsed}") from error

    def api(self, method: str, path: str, **kwargs):
        return self._request(method, PROD + path, **kwargs)

    def ensure_color(self, code: str, name: str) -> None:
        try:
            _, colors = self.api("GET", "/api/v1/catalog/colors")
        except Exception:
            colors = []
        if any(isinstance(item, dict) and item.get("code") == code for item in (colors or [])):
            return
        try:
            self.api("POST", "/api/v1/catalog/colors", data={"code": code, "name": name})
        except RuntimeError as error:
            if "409" not in str(error) and "exists" not in str(error).lower():
                print(f"  color warn: {error}", flush=True)

    def upload_image(self, product_id: str, sku: str, path: str) -> dict:
        filename = os.path.basename(path)
        data = open(path, "rb").read()
        if data[:3] != b"\xff\xd8\xff":
            raise RuntimeError(f"not a JPEG: {path}")
        boundary = "----Dupli1BoundaryEC2Upload"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
            f"Content-Type: image/jpeg\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        _, result = self.api(
            "POST",
            f"/api/v1/products/{product_id}/variants/{sku}/images",
            raw=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        return result or {}


def main() -> int:
    if not PASSWORD:
        raise SystemExit("DUPLI1_PASSWORD required")
    products = json.load(open(os.path.join(ROOT, "products.json"), encoding="utf-8"))
    client = Client()
    print(f"authenticated; products={len(products)} root={ROOT}", flush=True)
    report = []

    for index, row in enumerate(products, start=1):
        sku = row["sku"]
        old_pid = row["productId"]
        name = row["name"]
        style = row["styleCode"]
        folder = os.path.join(ROOT, sku)
        print(f"\n[{index}/{len(products)}] {sku} {name}", flush=True)
        info = json.load(open(os.path.join(folder, "info.json"), encoding="utf-8"))
        product = info["product"]
        selected = next(item for item in info["variants"] if item.get("selected"))
        images = sorted(
            name
            for name in os.listdir(folder)
            if name.startswith("image_") and name.lower().endswith(".jpg")
        )

        try:
            status, _ = client.api("DELETE", f"/api/v1/products/{old_pid}")
            print(f"  deleted old product {old_pid} status={status}", flush=True)
        except Exception as error:  # noqa: BLE001
            print(f"  delete note: {error}", flush=True)

        payload = {
            "name": product.get("name") or name,
            "description": product.get("description") or "",
            "brand": product.get("brand") or "Chanel",
            "brandCode": "CH",
            "styleCode": style,
            "material": product.get("material") or "",
            "category": product.get("category") or "bags",
            "capacity": product.get("capacity") or "",
            "status": product.get("status") or "draft",
            "tags": product.get("tags") or ["chanel", "bags"],
        }
        _, created = client.api("POST", "/api/v1/products", data=payload)
        new_pid = created["id"]
        print(f"  created {new_pid}", flush=True)

        code, color_name = color_code(selected.get("color") or "")
        client.ensure_color(code, color_name)
        vpayload = {
            "sku": selected["sku"],
            "color": color_name,
            "colorCode": code,
            "size": selected.get("size") or "",
            "price": selected.get("price"),
            "status": selected.get("status") or "active",
        }
        client.api("POST", f"/api/v1/products/{new_pid}/variants", data=vpayload)
        print(f"  variant {selected['sku']} color={code}", flush=True)

        last_urls: list[str] = []
        for image_name in images:
            path = os.path.join(folder, image_name)
            result = client.upload_image(new_pid, selected["sku"], path)
            last_urls = list(result.get("imageUrls") or last_urls)
            print(f"    uploaded {image_name} ({len(last_urls)})", flush=True)

        report.append(
            {
                "sku": sku,
                "name": name,
                "productId": new_pid,
                "images": len(last_urls),
                "uploaded": len(images),
                "styleCode": style,
                "imageUrls": last_urls,
            }
        )
        time.sleep(0.2)

    out = "/tmp/chanel-reupload-report.json"
    json.dump(report, open(out, "w", encoding="utf-8"), indent=2)
    ok = [row for row in report if row["images"] > 0]
    print(f"\nDONE {len(ok)}/{len(report)} report={out}", flush=True)
    for row in report:
        print(f"  {row['sku']} imgs={row['images']} {row['productId']}", flush=True)
    return 0 if len(ok) == len(report) else 2


if __name__ == "__main__":
    raise SystemExit(main())
