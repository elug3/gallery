#!/usr/bin/env python3
"""Scrape top-20 Chanel bag PDPs via Chrome CDP into images/chanel."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import urllib.request

from websocket import create_connection

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_DIR = os.path.join(ROOT, "images", "chanel")
HTML_DIR = "/tmp/chanel-chrome-html"
INDEX_PATH = "/tmp/chanel-chrome-index.json"
CDP = "http://127.0.0.1:9222"


def load_chanel():
    path = os.path.join(ROOT, "chanel", "main.py")
    spec = importlib.util.spec_from_file_location("chanel_main", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CdpSession:
    def __init__(self, ws_url: str):
        self.ws = create_connection(ws_url, timeout=60)
        self._id = 0

    def call(self, method: str, params: dict | None = None, timeout: float = 90):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = json.loads(self.ws.recv())
            if data.get("id") == mid:
                if "error" in data:
                    raise RuntimeError(data["error"])
                return data.get("result", {})
        raise TimeoutError(method)

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def new_page() -> dict:
    req = urllib.request.Request(f"{CDP}/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read())


def close_page(page_id: str) -> None:
    try:
        urllib.request.urlopen(f"{CDP}/json/close/{page_id}", timeout=15).read()
    except Exception:  # noqa: BLE001
        pass


def fetch_html(url: str, settle: float = 2.5) -> str:
    page = new_page()
    session = CdpSession(page["webSocketDebuggerUrl"])
    try:
        session.call("Page.enable")
        session.call("Runtime.enable")
        session.call("Network.enable")
        session.call("Page.navigate", {"url": url})
        deadline = time.time() + 60
        while time.time() < deadline:
            data = json.loads(session.ws.recv())
            if data.get("method") == "Page.loadEventFired":
                break
        time.sleep(settle)
        result = session.call(
            "Runtime.evaluate",
            {
                "expression": "document.documentElement.outerHTML",
                "returnByValue": True,
            },
            timeout=120,
        )
        html = result["result"]["value"]
        meta = session.call(
            "Runtime.evaluate",
            {
                "expression": (
                    "JSON.stringify({title:document.title,url:location.href,"
                    "denied:(document.body&&document.body.innerText||'').includes('Access Denied'),"
                    "next:!!document.getElementById('__NEXT_DATA__')})"
                ),
                "returnByValue": True,
            },
        )
        info = json.loads(meta["result"]["value"])
        if info.get("denied") and not info.get("next"):
            raise RuntimeError(f"Access Denied for {url}")
        if not info.get("next") and "application/ld+json" not in html.lower():
            raise RuntimeError(f"No product payload on {info.get('url')}")
        return html
    finally:
        session.close()
        close_page(page["id"])


def already_complete(target: str, min_images: int = 3) -> bool:
    info_path = os.path.join(target, "info.json")
    if not os.path.exists(info_path):
        return False
    images = [
        name
        for name in os.listdir(target)
        if name.startswith("image_") and name.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
    ]
    return len(images) >= min_images


def main() -> int:
    chanel = load_chanel()
    urls = list(chanel.DEFAULT_URLS)
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(HTML_DIR, exist_ok=True)

    index: dict[str, dict] = {}
    if os.path.exists(INDEX_PATH):
        try:
            rows = json.load(open(INDEX_PATH, encoding="utf-8"))
            index = {row["sku"]: row for row in rows if isinstance(row, dict) and row.get("sku")}
        except json.JSONDecodeError:
            index = {}

    total_images = 0
    successes = 0

    for i, url in enumerate(urls, start=1):
        sku = chanel.sku_from_url(url)
        target = os.path.join(OUT_DIR, sku)
        print(f"\n[{i}/{len(urls)}] {sku}", flush=True)
        if already_complete(target):
            info = json.load(open(os.path.join(target, "info.json"), encoding="utf-8"))
            n_images = len(
                [
                    name
                    for name in os.listdir(target)
                    if name.startswith("image_")
                ]
            )
            successes += 1
            total_images += n_images
            row = {
                "sku": sku,
                "url": url,
                "ok": True,
                "skipped": True,
                "images": n_images,
                "name": (info.get("product") or {}).get("name"),
                "price": next(
                    (
                        variant.get("price")
                        for variant in info.get("variants") or []
                        if variant.get("selected")
                    ),
                    None,
                ),
                "output": target,
            }
            print(f"  SKIP existing images={n_images} name={row['name']!r}", flush=True)
            index[sku] = row
            json.dump(list(index.values()), open(INDEX_PATH, "w", encoding="utf-8"), indent=2)
            continue

        try:
            html = fetch_html(url)
            cache = os.path.join(HTML_DIR, f"{sku}.html")
            open(cache, "w", encoding="utf-8").write(html)
            print(f"  html={len(html)} cached={cache}", flush=True)
            saved = chanel.extract_from_html(html, url, target, download=True)
            info = json.load(open(os.path.join(target, "info.json"), encoding="utf-8"))
            n_images = len(saved)
            total_images += n_images
            successes += 1
            row = {
                "sku": sku,
                "url": url,
                "ok": True,
                "images": n_images,
                "name": (info.get("product") or {}).get("name"),
                "price": next(
                    (
                        variant.get("price")
                        for variant in info.get("variants") or []
                        if variant.get("selected")
                    ),
                    None,
                ),
                "output": target,
            }
            print(
                f"  OK name={row['name']!r} price={row['price']} images={n_images}",
                flush=True,
            )
        except Exception as error:  # noqa: BLE001
            print(f"  FAIL {error}", flush=True)
            row = {"sku": sku, "url": url, "ok": False, "error": str(error)}
        index[sku] = row
        json.dump(list(index.values()), open(INDEX_PATH, "w", encoding="utf-8"), indent=2)
        time.sleep(2)

    summary = list(index.values())
    ok = [row for row in summary if row.get("ok") and (row.get("images") or 0) > 0]
    print(f"\nDone. {len(ok)} products with images, {total_images} images under {OUT_DIR}")
    for row in summary:
        mark = "Y" if row.get("ok") and (row.get("images") or 0) > 0 else "N"
        print(f"  {mark} {row.get('sku')} {row.get('name') or row.get('error')}")
    return 0 if len(ok) >= 15 else 2


if __name__ == "__main__":
    raise SystemExit(main())
