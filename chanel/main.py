#!/usr/bin/env python3
"""Extract product images and Dupli1-shaped ``info.json`` from Chanel PDPs.

Chanel fashion product pages embed commerce data in ``__NEXT_DATA__`` (and often
JSON-LD). Product imagery is served from ``https://www.chanel.com/images/...``
with Cloudinary-style transformations; this script prefers the largest / least-
cropped packshot URLs it can derive.

Akamai often blocks automated GET requests to ``/fashion/p/...`` and ``/c/...``
PLP routes from datacenter IPs (HTTP 403 Access Denied). Editorial pages and
the US sitemap remain reachable. Use ``--discover`` to list bag URLs from the
sitemap, ``--from-html`` to parse a saved PDP, or run from a network that can
reach product pages.

Usage::

    python3 chanel/main.py
    python3 chanel/main.py --discover
    python3 chanel/main.py --list-only <product-url>
    python3 chanel/main.py -o images <product-url> [...]
    python3 chanel/main.py --from-html path/to/pdp.html -o images

Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

# Curated top Chanel bag PDPs (US sitemap, classic + modern icons).
DEFAULT_URLS = [
    "https://www.chanel.com/us/fashion/p/A01113Y01864C3906/small-classic-handbag-grained-calfskin-gold-tone-metal/",
    "https://www.chanel.com/us/fashion/p/A01112B25720UB663/classic-11-12-handbag-grained-shiny-calfskin-gold-tone-metal/",
    "https://www.chanel.com/us/fashion/p/A37586Y04634C3906/2-55-handbag-aged-calfskin-gold-tone-metal/",
    "https://www.chanel.com/us/fashion/p/A69900Y0405994305/mini-classic-handbag-lambskin-gold-tone-metal/",
    "https://www.chanel.com/us/fashion/p/AP4241B10583U6544/classic-wallet-on-chain-grained-shiny-calfskin-gold-tone-metal/",
    "https://www.chanel.com/us/fashion/p/A67085Y0995394305/small-boy-chanel-handbag-calfskin-ruthenium-finish-metal/",
    "https://www.chanel.com/us/fashion/p/A67086Y0995394305/boy-chanel-handbag-calfskin-ruthenium-finish-metal/",
    "https://www.chanel.com/us/fashion/p/AS1161B0485294305/chanel-19-large-handbag-shiny-lambskin-gold-tone-silver-tone-ruthenium-finish-metal/",
    "https://www.chanel.com/us/fashion/p/AS3260B0985910601/chanel-22-small-handbag-calfskin-gold-tone-lacquered-metal/",
    "https://www.chanel.com/us/fashion/p/AS5311B2030494305/chanel-25-medium-handbag-grained-calfskin-gold-tone-metal/",
    "https://www.chanel.com/us/fashion/p/AS5293B23556U7740/chanel-25-small-handbag-washed-denim-gold-tone-metal/",
    "https://www.chanel.com/us/fashion/p/AS6398B25034UB022/flap-bag-with-top-handle-lambskin-gold-tone-metal/",
    "https://www.chanel.com/us/fashion/p/AP5475B25518UC984/vanity-with-chain-metallic-calfskin-white-metal/",
    "https://www.chanel.com/us/fashion/p/AS6130B2348394305/small-flap-bag-grained-shiny-calfskin-gold-tone-metal/",
    "https://www.chanel.com/us/fashion/p/AP3267B0485294305/chanel-19-wallet-on-chain-shiny-lambskin-gold-tone-silver-tone-ruthenium-finish-metal/",
    "https://www.chanel.com/us/fashion/p/AS6387B2403094305/shopping-bag-lambskin-gold-tone-metal/",
    "https://www.chanel.com/us/fashion/p/AS6424B25066U8752/mini-flap-bag-with-top-handle-crocodile-embossed-calfskin-gold-tone-metal/",
    "https://www.chanel.com/us/fashion/p/A01112Y0129594305/classic-11-12-handbag-lambskin-gold-tone-metal/",
    "https://www.chanel.com/us/fashion/p/A37586B2374694305/2-55-handbag-lambskin-gold-tone-metal/",
    "https://www.chanel.com/us/fashion/p/AS6132B2333294305/small-shopping-bag-calfskin-gold-tone-metal/",
]

SITEMAP_URL = "https://www.chanel.com/us/sitemap.xml"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

IMAGE_URL_RE = re.compile(
    r"https://www\.chanel\.com/images/[^\"'\s<>\\]+",
    re.IGNORECASE,
)

META_CONTENT_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']([^"\']+)["\'][^>]+content=["\']([^"\']*)["\']'
    r'|<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

DIMENSIONS_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(in|cm|mm)?",
    re.IGNORECASE,
)

ONE_SIZE_VALUES = frozenset({"tu", "one size", "onesize", "os", "u", "uni", ""})

DEFAULT_CATEGORY = "bags"
DEFAULT_STATUS = "draft"
DEFAULT_BRAND = "Chanel"
INFO_FILENAME = "info.json"

# Prefer these packshot typologies first when ordering images.
PREFERRED_TYPOLOGIES = (
    "PACKSHOT_DEFAULT",
    "PACKSHOT_ALTERNATIVE",
    "PACKSHOT_OTHER",
    "PACKSHOT_EXTRA",
    "PACKSHOT_ARTISTIQUE_VUE1",
    "PACKSHOT_ARTISTIQUE_VUE1_LARGE",
    "PACKSHOT_ARTISTIQUE_VUE2",
    "PACKSHOT_ARTISTIQUE_VUE3",
    "PACKSHOT_ARTISTIQUE_VUE4",
    "PACKSHOT_ARTISTIQUE_VUE5",
)

BAG_SLUG_HINTS = (
    "handbag",
    "bag",
    "flap",
    "tote",
    "vanity",
    "wallet-on-chain",
    "deauville",
    "gabrielle",
    "boy-chanel",
    "2-55",
    "chanel-19",
    "chanel-22",
    "chanel-25",
    "shopping-bag",
    "hobo",
    "backpack",
    "clutch",
    "bucket",
)


class ChanelAccessDenied(RuntimeError):
    """Raised when Akamai (or similar) blocks a product page GET."""


def fetch(url: str, timeout: int = 45) -> bytes:
    """Fetch *url* with a browser-like User-Agent and return the raw bytes."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Upgrade-Insecure-Requests": "1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        body = error.read()
        text = body.decode("utf-8", errors="replace")
        if error.code == 403 or "Access Denied" in text:
            raise ChanelAccessDenied(
                f"Chanel blocked GET {url} (HTTP {error.code}). "
                "Akamai often denies automated access to /fashion/p/ pages. "
                "Try --from-html with a browser-saved PDP, or run from a "
                "network that can open the page."
            ) from error
        raise


def strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    without_tags = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_lib.unescape(without_tags)).strip()


def filename_for(url: str, index: int | None = None) -> str:
    """Derive a local filename from an image URL."""
    parsed = urllib.parse.urlparse(url)
    name = os.path.basename(parsed.path) or "image.jpg"
    name = urllib.parse.unquote(name).split("?")[0]
    if not re.search(r"\.(jpe?g|png|webp|gif)$", name, re.IGNORECASE):
        name = f"{name}.jpg"
    # Numeric CDN ids like "-9559951147038.jpg" are fine; ensure uniqueness.
    if index is not None and not re.match(r"^[A-Za-z0-9].*", name):
        name = f"image_{index:02d}_{name.lstrip('-')}"
    elif index is not None and name.startswith("-"):
        name = f"image_{index:02d}_{name.lstrip('-')}"
    return re.sub(r"[^\w.\-]+", "_", name)


def normalize_price(raw: object) -> float | None:
    """Normalize Chanel / schema.org prices to a float dollars amount."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, dict):
        for key in ("priceAmount", "amount", "price", "value"):
            if key in raw and raw[key] not in (None, ""):
                return normalize_price(raw[key])
        return None
    text = str(raw).strip()
    text = text.replace(",", "").replace("$", "").replace("USD", "").strip()
    text = re.sub(r"[^\d.]", "", text)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_size(raw: object) -> str:
    """Map one-size / UNI labels to Dupli1 empty size."""
    value = strip_html(str(raw or "")).strip()
    if value.lower() in ONE_SIZE_VALUES:
        return ""
    return value


def absolute_url(path_or_url: str, page_url: str = "https://www.chanel.com") -> str:
    """Resolve a possibly-relative Chanel URL."""
    value = (path_or_url or "").strip()
    if not value:
        return ""
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("http://") or value.startswith("https://"):
        return value
    parsed = urllib.parse.urlparse(page_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if not value.startswith("/"):
        value = "/" + value
    return origin + value


def upgrade_image_url(url: str) -> str:
    """Prefer a large, lightly-cropped delivery URL for a Chanel image asset."""
    if not url or "chanel.com/images" not in url:
        return url
    # Drop query/fragment; keep path transforms but bump width when present.
    cleaned = url.split("?")[0].split("#")[0]
    # Replace low widths with a large one when a w_ token is present.
    cleaned = re.sub(r"(?<![a-zA-Z0-9])w_\d+", "w_3200", cleaned)
    # Prefer good quality / auto format when absent.
    if "q_auto" not in cleaned and "/images/" in cleaned:
        cleaned = cleaned.replace(
            "/images/",
            "/images/q_auto:good,f_auto,fl_lossy,dpr_1.1/",
            1,
        )
        # Avoid double-insert when transforms already follow /images/.
        cleaned = cleaned.replace(
            "/images/q_auto:good,f_auto,fl_lossy,dpr_1.1/q_auto",
            "/images/q_auto",
        )
    return cleaned


def typology_rank(typology: str) -> int:
    """Sort key for packshot typology preference."""
    try:
        return PREFERRED_TYPOLOGIES.index(typology)
    except ValueError:
        if typology.startswith("PACKSHOT"):
            return len(PREFERRED_TYPOLOGIES)
        if typology.startswith("GP_VISUAL"):
            return len(PREFERRED_TYPOLOGIES) + 1
        return len(PREFERRED_TYPOLOGIES) + 5


def extract_image_urls_from_html(html: str) -> list[str]:
    """Collect unique Chanel image URLs from raw HTML (src / srcset / JSON)."""
    seen: set[str] = set()
    urls: list[str] = []
    for match in IMAGE_URL_RE.findall(html):
        url = upgrade_image_url(match.rstrip("\\").rstrip(","))
        if url not in seen and not url.endswith(".svg"):
            seen.add(url)
            urls.append(url)
    return urls


def parse_json_ld_products(html: str) -> list[dict]:
    """Return Product / ProductGroup JSON-LD objects from *html*."""
    products: list[dict] = []
    for raw in JSON_LD_RE.findall(html):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        nodes = data if isinstance(data, list) else [data]
        stack = list(nodes)
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            node_type = node.get("@type", "")
            types = node_type if isinstance(node_type, list) else [node_type]
            if any(t in {"Product", "ProductGroup"} for t in types):
                products.append(node)
            for key in ("@graph", "hasVariant", "isVariantOf"):
                child = node.get(key)
                if isinstance(child, list):
                    stack.extend(child)
                elif isinstance(child, dict):
                    stack.append(child)
    return products


def parse_meta(html: str) -> dict[str, str]:
    """Return a map of meta property/name → content."""
    meta: dict[str, str] = {}
    for match in META_CONTENT_RE.finditer(html):
        if match.group(1) is not None:
            key, value = match.group(1), match.group(2)
        else:
            value, key = match.group(3), match.group(4)
        meta[key.lower()] = html_lib.unescape(value or "")
    return meta


def walk_find_product(obj: object, depth: int = 0) -> dict | None:
    """Depth-first search for a Chanel product-like dict in ``__NEXT_DATA__``."""
    if depth > 14 or not isinstance(obj, (dict, list)):
        return None
    if isinstance(obj, dict):
        keys = set(obj.keys())
        looks_like_product = (
            ("sku" in keys or "id" in keys)
            and ("images" in keys or "price" in keys)
            and ("title" in keys or "titleLabel" in keys or "name" in keys)
        )
        if looks_like_product and isinstance(obj.get("images"), list):
            return obj
        # Common Chanel pageProps nesting.
        for key in (
            "productData",
            "product",
            "pdp",
            "data",
            "pageProps",
            "props",
            "fshProduct",
            "selectedProduct",
        ):
            if key in obj:
                found = walk_find_product(obj[key], depth + 1)
                if found:
                    return found
        for value in obj.values():
            found = walk_find_product(value, depth + 1)
            if found:
                return found
    else:
        for item in obj[:50]:
            found = walk_find_product(item, depth + 1)
            if found:
                return found
    return None


def parse_next_data(html: str) -> dict | None:
    """Return parsed ``__NEXT_DATA__`` JSON, if present."""
    match = NEXT_DATA_RE.search(html)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def labels_from(items: object) -> list[str]:
    """Extract label strings from Chanel color/material arrays."""
    if not isinstance(items, list):
        if isinstance(items, str) and items.strip():
            return [strip_html(items)]
        return []
    labels: list[str] = []
    for item in items:
        if isinstance(item, dict):
            label = strip_html(str(item.get("label") or item.get("name") or ""))
        else:
            label = strip_html(str(item or ""))
        if label and label not in labels:
            labels.append(label)
    return labels


def image_sources_from_product(product: dict) -> list[str]:
    """Return ordered high-res image URLs from a Chanel product dict."""
    images = product.get("images")
    if not isinstance(images, list):
        return []
    ranked: list[tuple[int, int, str]] = []
    for index, item in enumerate(images):
        if not isinstance(item, dict):
            if isinstance(item, str) and item.startswith("http"):
                ranked.append((typology_rank(""), index, upgrade_image_url(item)))
            continue
        source = str(item.get("source") or item.get("url") or item.get("src") or "")
        if not source:
            continue
        typology = str(item.get("typology") or "")
        # Skip obvious non-product / mobile chrome assets when better shots exist.
        if typology in {"PDT_VIEW_MOBILE", "PDT_VIEW_HEADER"} and any(
            isinstance(other, dict)
            and str(other.get("typology") or "").startswith("PACKSHOT")
            for other in images
        ):
            continue
        url = upgrade_image_url(absolute_url(source))
        if "chanel.com" in url or url.startswith("http"):
            ranked.append((typology_rank(typology), index, url))
    ranked.sort()
    seen: set[str] = set()
    urls: list[str] = []
    for _, _, url in ranked:
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def sku_from_url(page_url: str) -> str:
    """Extract the Chanel product code segment from a PDP URL."""
    path = urllib.parse.urlparse(page_url).path.rstrip("/")
    parts = [part for part in path.split("/") if part]
    if "p" in parts:
        index = parts.index("p")
        if index + 1 < len(parts):
            return parts[index + 1]
    return parts[-1] if parts else ""


def capacity_from_text(*texts: str) -> tuple[str, dict[str, str]]:
    """Parse ``H x W x D`` dimensions into capacity + dimensions map."""
    for text in texts:
        if not text:
            continue
        match = DIMENSIONS_RE.search(text)
        if not match:
            continue
        height, width, depth, unit = match.groups()
        unit = (unit or "in").strip()
        dimensions = {
            "height": f"{height} {unit}",
            "width": f"{width} {unit}",
            "depth": f"{depth} {unit}",
        }
        capacity = f"{height} {unit} × {width} {unit} × {depth} {unit}"
        return capacity, dimensions
    return "", {}


def build_variants_from_chanel(
    *,
    product: dict,
    selected_sku: str,
    selected_color: str,
    price: float | None,
    image_files: list[str],
    image_urls: list[str],
    page_url: str,
) -> list[dict]:
    """Build Dupli1-shaped variants from Chanel ``variations`` / self."""
    variants: list[dict] = []
    size_value = normalize_size(
        product.get("sizeLabel") or product.get("size") or ""
    )

    variation_groups = product.get("variations")
    sibling_products: list[dict] = []
    if isinstance(variation_groups, list):
        for group in variation_groups:
            if not isinstance(group, dict):
                continue
            for item in group.get("products") or []:
                if isinstance(item, dict):
                    sibling_products.append(item)

    if not sibling_products:
        sibling_products = [product]

    selected_seen = False
    for item in sibling_products:
        sku = str(
            item.get("sku")
            or item.get("id")
            or item.get("formattedId")
            or ""
        )
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        color = strip_html(
            str(
                details.get("color")
                or (labels_from(item.get("colors")) or [""])[0]
                or item.get("color")
                or ""
            )
        )
        item_url = absolute_url(str(item.get("url") or ""), page_url)
        selected = bool(
            sku and sku == selected_sku
        ) or (
            not selected_sku
            and color
            and color == selected_color
            and not selected_seen
        ) or (item is product and not selected_seen and len(sibling_products) == 1)
        if selected:
            selected_seen = True

        thumb = ""
        item_images = item.get("images")
        if isinstance(item_images, list) and item_images:
            first = item_images[0]
            if isinstance(first, dict):
                thumb = absolute_url(str(first.get("source") or ""))
            elif isinstance(first, str):
                thumb = absolute_url(first)

        item_price = normalize_price(item.get("price"))
        if item_price is None:
            item_price = price

        variant: dict = {
            "sku": sku or selected_sku,
            "color": color or selected_color,
            "size": size_value if selected else normalize_size(item.get("sizeLabel") or ""),
            "price": item_price,
            "status": "active" if selected else "draft",
            "images": list(image_files) if selected else [],
            "imageUrls": list(image_urls) if selected else [],
            "selected": bool(selected),
            "available": True if selected else bool(item_url),
            "sourceUrl": item_url or (page_url if selected else ""),
        }
        if thumb and not selected:
            variant["thumbnail"] = upgrade_image_url(thumb)
        hex_code = str(item.get("hex") or details.get("hex") or "")
        if hex_code:
            variant["hex"] = hex_code
        variants.append(variant)

    if not any(item.get("selected") for item in variants) and variants:
        variants[0]["selected"] = True
        variants[0]["images"] = list(image_files)
        variants[0]["imageUrls"] = list(image_urls)
        variants[0]["status"] = "active"
        variants[0]["sourceUrl"] = variants[0].get("sourceUrl") or page_url

    return variants


def extract_product_info(html: str, page_url: str, image_urls: list[str] | None = None) -> dict:
    """Build a Dupli1-oriented info.json payload from a Chanel PDP."""
    next_data = parse_next_data(html)
    product = walk_find_product(next_data) if next_data else None
    ld_products = parse_json_ld_products(html)
    ld = ld_products[0] if ld_products else {}
    meta = parse_meta(html)

    if product is None:
        product = {}

    details = product.get("details") if isinstance(product.get("details"), dict) else {}

    name = strip_html(
        str(
            product.get("title")
            or product.get("titleLabel")
            or product.get("name")
            or ld.get("name")
            or meta.get("og:title")
            or meta.get("twitter:title")
            or ""
        )
    )
    # Strip trailing " — Fashion | CHANEL" style suffixes.
    name = re.sub(r"\s*[|—–-]\s*Fashion.*$", "", name, flags=re.IGNORECASE).strip()
    name = re.sub(r"\s*\|\s*CHANEL\s*$", "", name, flags=re.IGNORECASE).strip()

    description = strip_html(
        str(
            product.get("briefDescription")
            or product.get("description")
            or ld.get("description")
            or meta.get("og:description")
            or meta.get("description")
            or ""
        )
    )

    selected_sku = str(
        product.get("sku")
        or product.get("id")
        or product.get("formattedId")
        or ld.get("sku")
        or sku_from_url(page_url)
    )
    reference = strip_html(str(details.get("reference") or ""))
    if reference and not selected_sku:
        selected_sku = reference.replace("-", "")

    color = strip_html(
        str(
            details.get("color")
            or (labels_from(product.get("colors")) or [""])[0]
            or product.get("color")
            or ld.get("color")
            or ""
        )
    )
    material = strip_html(
        str(
            details.get("fabrics")
            or (labels_from(product.get("materials")) or [""])[0]
            or product.get("material")
            or ld.get("material")
            or ""
        )
    )

    offers = ld.get("offers") if isinstance(ld.get("offers"), dict) else {}
    price = normalize_price(product.get("price"))
    if price is None:
        price = normalize_price(offers.get("price") or offers.get("priceAmount"))
    currency = ""
    raw_price = product.get("price")
    if isinstance(raw_price, dict):
        currency = str(raw_price.get("currency") or raw_price.get("priceCurrency") or "")
    if not currency:
        currency = str(offers.get("priceCurrency") or meta.get("product:price:currency") or "USD")

    next_images = image_sources_from_product(product) if product else []
    html_images = extract_image_urls_from_html(html)
    combined_images = image_urls if image_urls is not None else []
    if not combined_images:
        combined_images = next_images or html_images
    # Prefer next_data ordering when both exist.
    if next_images:
        combined_images = next_images

    image_files = [filename_for(url, index=i) for i, url in enumerate(combined_images, start=1)]

    capacity, dimensions = capacity_from_text(
        description,
        str(product.get("sizeLabel") or ""),
        str(details),
        html,
    )

    detail_bullets: list[str] = []
    if reference:
        detail_bullets.append(f"Reference: {reference}")
    collection = strip_html(str(product.get("collection") or ""))
    if collection:
        detail_bullets.append(f"Collection: {collection}")
    size_label = strip_html(str(product.get("sizeLabel") or ""))
    if size_label:
        detail_bullets.append(f"Size: {size_label}")
    for key in ("hardware", "lining", "closure"):
        value = strip_html(str(details.get(key) or ""))
        if value:
            detail_bullets.append(f"{key.capitalize()}: {value}")

    variants = build_variants_from_chanel(
        product=product or {
            "sku": selected_sku,
            "details": {"color": color},
            "sizeLabel": size_label,
            "price": price,
            "url": page_url,
        },
        selected_sku=selected_sku,
        selected_color=color,
        price=price,
        image_files=image_files,
        image_urls=combined_images,
        page_url=page_url,
    )

    available_colors: list[str] = []
    for variant in variants:
        value = variant.get("color") or ""
        if value and value not in available_colors:
            available_colors.append(value)

    available_sizes: list[str] = []
    for variant in variants:
        if not variant.get("selected"):
            continue
        value = variant.get("size") or ""
        if value and value not in available_sizes:
            available_sizes.append(value)

    product_group_id = ""
    if selected_sku:
        # Chanel refs are often A01113Y01864C3906 → style A01113
        match = re.match(r"^([A-Z]{1,2}\d{4,5})", selected_sku)
        product_group_id = match.group(1) if match else selected_sku[:6]

    tags = ["chanel", DEFAULT_CATEGORY]
    if collection:
        tags.append(re.sub(r"\s+", "-", collection.lower()))

    info = {
        "product": {
            "name": name,
            "description": description,
            "brand": DEFAULT_BRAND,
            "material": material,
            "category": DEFAULT_CATEGORY,
            "capacity": capacity,
            "status": DEFAULT_STATUS,
            "tags": tags,
            "sourceUrl": page_url,
        },
        "variants": variants,
        "availableColors": available_colors,
        "availableSizes": available_sizes,
        "details": detail_bullets,
        "dimensions": dimensions,
        "currency": currency,
        "productGroupId": product_group_id,
        "selectedSku": selected_sku,
    }
    return info


def write_info_json(info: dict, output_dir: str) -> str:
    """Write *info* as ``info.json`` under *output_dir* and return its path."""
    os.makedirs(output_dir, exist_ok=True)
    dest = os.path.join(output_dir, INFO_FILENAME)
    with open(dest, "w", encoding="utf-8") as handle:
        json.dump(info, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return dest


def download_images(urls: list[str], output_dir: str) -> list[str]:
    """Download each URL into *output_dir*, returning the saved file paths."""
    os.makedirs(output_dir, exist_ok=True)
    saved: list[str] = []
    for index, url in enumerate(urls, start=1):
        name = filename_for(url, index=index)
        dest = os.path.join(output_dir, name)
        try:
            data = fetch(url)
        except (ChanelAccessDenied, urllib.error.URLError, urllib.error.HTTPError) as error:
            print(f"  [{index}/{len(urls)}] FAILED {url} ({error})", file=sys.stderr)
            continue
        with open(dest, "wb") as handle:
            handle.write(data)
        print(f"  [{index}/{len(urls)}] saved {dest} ({len(data):,} bytes)")
        saved.append(dest)
    return saved


def output_dir_for(url: str, base_dir: str, multi: bool) -> str:
    """Return a per-SKU subdirectory when scraping multiple URLs."""
    if not multi:
        return base_dir
    sku = sku_from_url(url) or url.rstrip("/").rsplit("/", 1)[-1]
    return os.path.join(base_dir, sku)


def extract_from_html(html: str, page_url: str, output_dir: str, download: bool = True) -> list[str]:
    """Parse HTML, write info.json, and optionally download images."""
    info = extract_product_info(html, page_url)
    selected = next((item for item in info.get("variants") or [] if item.get("selected")), None)
    image_urls = list((selected or {}).get("imageUrls") or [])
    print(f"Found {len(image_urls)} product image(s):")
    for image_url in image_urls:
        print(f"  - {image_url}")

    info_path = write_info_json(info, output_dir)
    product = info.get("product") or {}
    print(f"Wrote product info: {info_path}")
    if product.get("name"):
        print(f"  name: {product['name']}")
    if info.get("selectedSku"):
        print(f"  sku:  {info['selectedSku']}")
    if selected and selected.get("price") is not None:
        currency = info.get("currency") or ""
        label = f"{currency} {selected['price']}".strip()
        print(f"  price: {label}")
    print(
        f"  variants: {len(info.get('variants') or [])} "
        f"(colors={info.get('availableColors')}, sizes={info.get('availableSizes')})"
    )

    if not download or not image_urls:
        return []
    print(f"Downloading into: {output_dir}")
    return download_images(image_urls, output_dir)


def extract_from_page(url: str, output_dir: str) -> list[str]:
    """Scrape *url*, write info.json, and download product images."""
    print(f"Fetching product page: {url}", flush=True)
    html = fetch(url).decode("utf-8", errors="replace")
    if "Access Denied" in html and "__NEXT_DATA__" not in html:
        raise ChanelAccessDenied(
            f"Chanel returned Access Denied HTML for {url}. "
            "Use --from-html with a browser-saved PDP."
        )
    return extract_from_html(html, url, output_dir, download=True)


def discover_bag_urls(limit: int = 20) -> list[str]:
    """Return fashion bag PDP URLs from the US sitemap (reachable under Akamai)."""
    print(f"Fetching sitemap: {SITEMAP_URL}")
    xml = fetch(SITEMAP_URL).decode("utf-8", errors="replace")
    locs = re.findall(r"<loc>(.*?)</loc>", xml)
    bags: list[str] = []
    seen: set[str] = set()
    for url in locs:
        if "/us/fashion/p/" not in url:
            continue
        slug = url.rstrip("/").rsplit("/", 1)[-1].lower()
        if not any(hint in slug for hint in BAG_SLUG_HINTS):
            continue
        if url in seen:
            continue
        seen.add(url)
        bags.append(url)
        if limit and len(bags) >= limit:
            break
    return bags


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract product images and Dupli1-shaped info.json "
            "from a Chanel fashion product page."
        ),
    )
    parser.add_argument(
        "urls",
        nargs="*",
        default=None,
        help="Product page URL(s) to scrape (default: curated top-20 bag list).",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="images/chanel",
        help="Directory for images + info.json (default: ./images/chanel).",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Only print image URLs; do not download or write info.json.",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="List bag product URLs from the US sitemap and exit.",
    )
    parser.add_argument(
        "--discover-limit",
        type=int,
        default=20,
        help="Max URLs to print with --discover (default: 20).",
    )
    parser.add_argument(
        "--from-html",
        metavar="PATH",
        help="Parse a saved Chanel PDP HTML file instead of fetching.",
    )
    parser.add_argument(
        "--page-url",
        default="",
        help="Canonical page URL when using --from-html.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.discover:
        try:
            urls = discover_bag_urls(limit=args.discover_limit)
        except ChanelAccessDenied as error:
            print(error, file=sys.stderr)
            return 2
        for url in urls:
            print(url)
        print(f"# {len(urls)} bag URL(s)", file=sys.stderr, flush=True)
        return 0

    if args.from_html:
        with open(args.from_html, encoding="utf-8", errors="replace") as handle:
            html = handle.read()
        page_url = args.page_url or f"file://{os.path.abspath(args.from_html)}"
        # Prefer SKU inferred from filename / embedded canonical.
        canonical = re.search(
            r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
        if canonical and not args.page_url:
            page_url = canonical.group(1)
        if args.list_only:
            info = extract_product_info(html, page_url)
            selected = next(
                (item for item in info.get("variants") or [] if item.get("selected")),
                None,
            )
            for image_url in (selected or {}).get("imageUrls") or []:
                print(image_url)
            return 0
        extract_from_html(html, page_url, args.output_dir, download=not args.list_only)
        print("Done.")
        return 0

    urls = args.urls if args.urls else list(DEFAULT_URLS)
    total_saved = 0
    multi = len(urls) > 1
    failures = 0

    for url in urls:
        try:
            if args.list_only:
                html = fetch(url).decode("utf-8", errors="replace")
                info = extract_product_info(html, url)
                selected = next(
                    (item for item in info.get("variants") or [] if item.get("selected")),
                    None,
                )
                for image_url in (selected or {}).get("imageUrls") or []:
                    print(image_url)
            else:
                target_dir = output_dir_for(url, args.output_dir, multi)
                saved = extract_from_page(url, target_dir)
                total_saved += len(saved)
        except ChanelAccessDenied as error:
            failures += 1
            print(error, file=sys.stderr)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as error:
            failures += 1
            print(f"Failed {url}: {error}", file=sys.stderr)

    if not args.list_only:
        print(f"Done. Downloaded {total_saved} image(s).")
    if failures and total_saved == 0 and not args.list_only:
        print(
            "Hint: Chanel PDPs are often blocked here. "
            "Try `python3 chanel/main.py --discover` or "
            "`python3 chanel/main.py --from-html saved.html`.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
