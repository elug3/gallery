#!/usr/bin/env python3
"""Extract original (full-resolution) product images from a Prada product page.

Prada serves its product imagery from an Adobe AEM DAM. Each image is exposed
at a base URL such as::

    https://www.prada.com/content/dam/pradabkg_products/.../<CODE>.jpg

and the page references downscaled "renditions" of it under::

    .../<CODE>.jpg/_jcr_content/renditions/cq5dam.web.hebebed.<W>.<H>.jpg

Requesting the base URL (the part up to and including the first ``.jpg``)
returns the original, highest-resolution asset. This script scrapes the page,
collects every unique base image URL, and downloads each one.

Usage::

    python prada/main.py
    python prada/main.py <product-url> [<product-url> ...]
    python prada/main.py -o images <product-url>

Only the Python standard library is used, so no extra dependencies are needed.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.error
import urllib.request

DEFAULT_URL = (
    "https://www.prada.com/ww/en/p/small-re-nylon-backpack/"
    "1BZ677_RV44_F0002_V_OOO"
)

# A desktop browser User-Agent; the site returns a stripped-down response to
# unknown clients.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Matches a Prada DAM image URL up to (and including) the first ".jpg". The
# non-greedy quantifier stops at the base asset rather than the rendition path
# that follows it (".../foo.jpg/_jcr_content/renditions/...jpg").
IMAGE_URL_RE = re.compile(
    r"https://www\.prada\.com/content/dam/pradabkg_products/[^\s\"'<>\\,]+?\.jpg",
    re.IGNORECASE,
)


def fetch(url: str, timeout: int = 30) -> bytes:
    """Fetch *url* with a browser-like User-Agent and return the raw bytes."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def extract_original_image_urls(html: str) -> list[str]:
    """Return the unique, original-resolution image URLs found in *html*.

    Order of first appearance is preserved so the result is deterministic.
    """
    seen: set[str] = set()
    urls: list[str] = []
    for match in IMAGE_URL_RE.findall(html):
        if match not in seen:
            seen.add(match)
            urls.append(match)
    return urls


def filename_for(url: str) -> str:
    """Derive a sensible local filename from an image *url*."""
    return url.rsplit("/", 1)[-1]


def download_images(urls: list[str], output_dir: str) -> list[str]:
    """Download each URL into *output_dir*, returning the saved file paths."""
    os.makedirs(output_dir, exist_ok=True)
    saved: list[str] = []
    for index, url in enumerate(urls, start=1):
        name = filename_for(url)
        dest = os.path.join(output_dir, name)
        try:
            data = fetch(url)
        except (urllib.error.URLError, urllib.error.HTTPError) as error:
            print(f"  [{index}/{len(urls)}] FAILED {url} ({error})", file=sys.stderr)
            continue
        with open(dest, "wb") as handle:
            handle.write(data)
        print(f"  [{index}/{len(urls)}] saved {dest} ({len(data):,} bytes)")
        saved.append(dest)
    return saved


def extract_from_page(url: str, output_dir: str) -> list[str]:
    """Scrape *url* and download its original product images."""
    print(f"Fetching product page: {url}")
    html = fetch(url).decode("utf-8", errors="replace")
    image_urls = extract_original_image_urls(html)
    print(f"Found {len(image_urls)} original image(s):")
    for image_url in image_urls:
        print(f"  - {image_url}")
    if not image_urls:
        return []
    print(f"Downloading into: {output_dir}")
    return download_images(image_urls, output_dir)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract original product images from a Prada product page.",
    )
    parser.add_argument(
        "urls",
        nargs="*",
        default=[DEFAULT_URL],
        help=f"Product page URL(s) to scrape (default: {DEFAULT_URL}).",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="images",
        help="Directory to save downloaded images (default: ./images).",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Only print the original image URLs; do not download anything.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    total_saved = 0
    for url in args.urls:
        if args.list_only:
            html = fetch(url).decode("utf-8", errors="replace")
            for image_url in extract_original_image_urls(html):
                print(image_url)
        else:
            saved = extract_from_page(url, args.output_dir)
            total_saved += len(saved)
    if not args.list_only:
        print(f"Done. Downloaded {total_saved} image(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
