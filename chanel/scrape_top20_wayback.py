#!/usr/bin/env python3
"""Fetch top-20 Chanel bag PDPs via Wayback Machine and scrape into images/chanel."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_DIR = os.path.join(ROOT, "images", "chanel")
HTML_DIR = "/tmp/chanel-wayback-html"
INDEX_PATH = "/tmp/chanel-wayback-index.json"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def load_chanel():
    path = os.path.join(ROOT, "chanel", "main.py")
    spec = importlib.util.spec_from_file_location("chanel_main", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def http_get(url: str, timeout: int = 60, retries: int = 5) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": UA, "Accept": "text/html,application/json,*/*"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except Exception as error:  # noqa: BLE001 - retry wrapper
            last = error
            code = getattr(error, "code", None)
            wait = 8 * (attempt + 1) if code in {503, 429, 502} else 3 * (attempt + 1)
            print(f"  retry {attempt + 1}/{retries} after {wait}s ({error})", flush=True)
            time.sleep(wait)
    assert last is not None
    raise last


def latest_timestamp(original: str) -> str | None:
    queries = [original]
    # Fallback: any snapshot under this SKU path (slug may have changed).
    if "/fashion/p/" in original:
        sku = original.split("/fashion/p/", 1)[1].split("/", 1)[0]
        queries.append(f"https://www.chanel.com/us/fashion/p/{sku}/*")
        queries.append(f"www.chanel.com/us/fashion/p/{sku}/*")

    for query in queries:
        cdx = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(
            {
                "url": query,
                "output": "json",
                "filter": "statuscode:200",
                "fl": "timestamp,original,length",
                "limit": "-20",
                "matchType": "prefix" if query.endswith("*") else "exact",
            }
        )
        try:
            data = json.loads(http_get(cdx, retries=3))
        except Exception as error:  # noqa: BLE001
            print(f"  cdx fail for {query}: {error}", flush=True)
            continue
        rows = data[1:]
        if not rows:
            continue
        rows.sort(key=lambda row: row[0], reverse=True)
        for timestamp, _original, length in rows:
            try:
                if int(length) > 30000:
                    return timestamp
            except ValueError:
                continue
        return rows[0][0]
    return None


def fetch_html(url: str, sku: str) -> tuple[str, str]:
    """Return (html, wayback_timestamp)."""
    os.makedirs(HTML_DIR, exist_ok=True)
    cached = os.path.join(HTML_DIR, f"{sku}.html")
    if os.path.exists(cached) and os.path.getsize(cached) > 20000:
        html = open(cached, encoding="utf-8", errors="replace").read()
        if "__NEXT_DATA__" in html or "application/ld+json" in html:
            return html, "cached"

    timestamp = latest_timestamp(url)
    if not timestamp:
        raise RuntimeError(f"no Wayback snapshot for {url}")

    # Prefer the exact URL; also try timestampid_ with SKU-only path from CDX originals.
    candidates = [
        f"https://web.archive.org/web/{timestamp}id_/{url}",
        f"https://web.archive.org/web/{timestamp}/{url}",
        f"https://web.archive.org/web/{timestamp}id_/https://www.chanel.com/us/fashion/p/{sku}/",
    ]
    last_error: Exception | None = None
    for wayback_url in candidates:
        try:
            body = http_get(wayback_url)
            text = body.decode("utf-8", errors="replace")
            if "Access Denied" in text[:800] and "__NEXT_DATA__" not in text:
                raise RuntimeError("wayback returned Access Denied body")
            if "__NEXT_DATA__" not in text and "application/ld+json" not in text.lower():
                raise RuntimeError("wayback page missing product payloads")
            open(cached, "w", encoding="utf-8").write(text)
            return text, timestamp
        except Exception as error:  # noqa: BLE001
            last_error = error
            continue
    assert last_error is not None
    raise last_error


def main() -> int:
    chanel = load_chanel()
    urls = list(chanel.DEFAULT_URLS)
    os.makedirs(OUT_DIR, exist_ok=True)

    index: list[dict] = []
    if os.path.exists(INDEX_PATH):
        try:
            index = json.load(open(INDEX_PATH, encoding="utf-8"))
        except json.JSONDecodeError:
            index = []
    by_sku = {row.get("sku"): row for row in index if isinstance(row, dict)}

    total_images = 0
    successes = 0

    for i, url in enumerate(urls, start=1):
        sku = chanel.sku_from_url(url)
        target = os.path.join(OUT_DIR, sku)
        print(f"\n[{i}/{len(urls)}] {sku}", flush=True)
        try:
            html, timestamp = fetch_html(url, sku)
            print(
                f"  html={len(html)} ts={timestamp} next={('__NEXT_DATA__' in html)}",
                flush=True,
            )
            saved = chanel.extract_from_html(html, url, target, download=True)
            info_path = os.path.join(target, "info.json")
            info = json.load(open(info_path, encoding="utf-8"))
            n_images = len(saved)
            total_images += n_images
            successes += 1
            row = {
                "sku": sku,
                "url": url,
                "ok": True,
                "ts": timestamp,
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
        by_sku[sku] = row
        json.dump(list(by_sku.values()), open(INDEX_PATH, "w", encoding="utf-8"), indent=2)
        time.sleep(4)

    summary = list(by_sku.values())
    ok = [row for row in summary if row.get("ok")]
    print(f"\nDone. {len(ok)}/{len(urls)} products, {total_images} images saved under {OUT_DIR}")
    for row in summary:
        mark = "Y" if row.get("ok") else "N"
        print(f"  {mark} {row.get('sku')} {row.get('name') or row.get('error')}")
    return 0 if len(ok) >= 10 else 2


if __name__ == "__main__":
    raise SystemExit(main())
