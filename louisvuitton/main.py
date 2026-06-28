#!/usr/bin/env python3
"""Extract product metadata from Louis Vuitton product pages.

Louis Vuitton product pages embed structured data in JSON-LD, Next.js
hydration payloads, and a storefront catalog API. This script fetches each
product URL, extracts name, description, price, material, color, size, and
the highest-resolution product image available, then prints JSON to stdout.

Usage::

    python louisvuitton/main.py
    python louisvuitton/main.py <product-url> [<product-url> ...]
    python louisvuitton/main.py --pretty <product-url>

Only the Python standard library is used, so no extra dependencies are needed.
"""

from __future__ import annotations

import argparse
import html as html_lib
import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = (
    "https://us.louisvuitton.com/eng-us/products/"
    "speedy-bandouliere-25-monogram-nvprod5320019v/M46977"
)

CHROME_VERSION = "131"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/{CHROME_VERSION}.0.0.0 Safari/537.36"
)

# Shared Chrome-on-Windows client hints and transport headers. Accept-Encoding is
# omitted because urllib does not transparently decode gzip/br responses.
BASE_BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Sec-Ch-Ua": (
        f'"Google Chrome";v="{CHROME_VERSION}", '
        f'"Chromium";v="{CHROME_VERSION}", "Not_A Brand";v="24"'
    ),
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Upgrade-Insecure-Requests": "1",
}

DOCUMENT_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
    "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
)
API_ACCEPT = "application/json, text/plain, */*"

PRODUCT_URL_RE = re.compile(
    r"^https?://(?P<host>[\w.-]+\.louisvuitton\.(?:com|cn))"
    r"/(?P<lang>[a-z]{3}-[a-z]{2})/products/(?P<slug>[^/?#]+)/(?P<sku>[A-Za-z0-9]+)",
    re.IGNORECASE,
)

JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
IMG_TAG_RE = re.compile(
    r"<img\b[^>]*\bsrc=['\"]([^'\"]+)['\"][^>]*>",
    re.IGNORECASE,
)
IMG_SRCSET_RE = re.compile(
    r"<img\b[^>]*\bsrcset=['\"]([^'\"]+)['\"][^>]*>",
    re.IGNORECASE,
)
META_CONTENT_RE = re.compile(
    r'<meta\b[^>]+(?:property|name)=["\'](?P<key>[^"\']+)["\'][^>]+'
    r'content=["\'](?P<content>[^"\']*)["\'][^>]*>',
    re.IGNORECASE,
)
META_CONTENT_RE_ALT = re.compile(
    r'<meta\b[^>]+content=["\'](?P<content>[^"\']*)["\'][^>]+'
    r'(?:property|name)=["\'](?P<key>[^"\']+)["\'][^>]*>',
    re.IGNORECASE,
)

PRODUCT_IMAGE_HINTS = (
    "/images/is/image/",
    "/content/dam/lv/",
    "product",
)
SKIP_IMAGE_HINTS = (
    "maintenance",
    "editorial-content/maintenance",
    "favicon",
    "data:image",
    "logo",
)

SIZE_TOKENS = ("nano", "micro", "bb", "pm", "mm", "gm", "xl", "xxl")


def registrable_domain(host: str) -> str:
    """Return the last two labels of *host* (e.g. louisvuitton.com)."""
    labels = host.lower().split(".")
    if len(labels) >= 2:
        return ".".join(labels[-2:])
    return host.lower()


def sec_fetch_site(referer: str | None, url: str) -> str:
    if not referer:
        return "none"
    ref = urllib.parse.urlparse(referer)
    req = urllib.parse.urlparse(url)
    if ref.netloc == req.netloc:
        return "same-origin"
    if registrable_domain(ref.netloc) == registrable_domain(req.netloc):
        return "same-site"
    return "cross-site"


def request_headers(
    url: str,
    *,
    referer: str | None = None,
    api: bool = False,
) -> dict[str, str]:
    """Build Chrome-like request headers for a document or XHR fetch."""
    headers = dict(BASE_BROWSER_HEADERS)
    fetch_site = sec_fetch_site(referer, url)

    if api:
        headers["Accept"] = API_ACCEPT
        headers["Sec-Fetch-Dest"] = "empty"
        headers["Sec-Fetch-Mode"] = "cors"
        headers["Sec-Fetch-Site"] = fetch_site
        headers["X-Requested-With"] = "XMLHttpRequest"
        headers.pop("Upgrade-Insecure-Requests", None)
        headers.pop("Sec-Fetch-User", None)
    else:
        headers["Accept"] = DOCUMENT_ACCEPT
        headers["Sec-Fetch-Dest"] = "document"
        headers["Sec-Fetch-Mode"] = "navigate"
        headers["Sec-Fetch-Site"] = fetch_site
        headers["Sec-Fetch-User"] = "?1"

    if referer:
        headers["Referer"] = referer
        parsed = urllib.parse.urlparse(referer)
        headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"

    return headers


class Fetcher:
    """HTTP client that keeps cookies across storefront and API requests."""

    def __init__(self) -> None:
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar),
        )

    def fetch(
        self,
        url: str,
        *,
        referer: str | None = None,
        api: bool = False,
        timeout: int = 30,
    ) -> bytes:
        headers = request_headers(url, referer=referer, api=api)
        request = urllib.request.Request(url, headers=headers)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            body = error.read()
            if body:
                return body
            raise


def parse_product_url(url: str) -> dict[str, str]:
    """Return host, locale, slug, and SKU parsed from a product URL."""
    match = PRODUCT_URL_RE.match(url.strip())
    if not match:
        raise ValueError(
            "Not a Louis Vuitton product URL "
            "(expected .../products/<slug>/<SKU>)."
        )
    return {
        "host": match.group("host").lower(),
        "lang": match.group("lang").lower(),
        "slug": match.group("slug"),
        "sku": match.group("sku").upper(),
        "link": url.strip(),
    }


def api_base_for_host(host: str) -> str:
    if host.endswith(".louisvuitton.cn"):
        return "https://api-www.louisvuitton.cn"
    return "https://api.louisvuitton.com"


def strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    if not text:
        return ""
    without_tags = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_lib.unescape(without_tags)).strip()


def format_price(value: object, currency: str | None = None) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        if float(value).is_integer():
            amount = f"{int(value):,}"
        else:
            amount = f"{float(value):,.2f}".rstrip("0").rstrip(".")
        return f"{currency} {amount}".strip() if currency else amount
    return str(value).strip()


def parse_srcset(srcset: str) -> str:
    """Return the highest-width URL from an img srcset attribute."""
    best_url = ""
    best_width = -1
    for part in srcset.split(","):
        piece = part.strip()
        if not piece:
            continue
        tokens = piece.split()
        if not tokens:
            continue
        url = tokens[0]
        width = 0
        if len(tokens) > 1 and tokens[1].endswith("w"):
            try:
                width = int(tokens[1][:-1])
            except ValueError:
                width = 0
        if width >= best_width:
            best_width = width
            best_url = url
    return best_url


def normalize_image_url(url: str) -> str:
    """Prefer the largest Scene7 / DAM rendition when width params are present."""
    if not url or url.startswith("data:"):
        return ""
    absolute = url.strip()
    if absolute.startswith("//"):
        absolute = "https:" + absolute
    if "?" in absolute:
        base, _query = absolute.split("?", 1)
        if "/images/is/image/" in base or "content/dam/lv/" in base:
            return f"{base}?wid=4096"
    return absolute


def is_product_image(url: str) -> bool:
    lowered = url.lower()
    if any(hint in lowered for hint in SKIP_IMAGE_HINTS):
        return False
    return any(hint in lowered for hint in PRODUCT_IMAGE_HINTS)


def extract_meta_tags(page_html: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    for pattern in (META_CONTENT_RE, META_CONTENT_RE_ALT):
        for match in pattern.finditer(page_html):
            key = match.group("key").lower()
            content = html_lib.unescape(match.group("content").strip())
            if content:
                tags[key] = content
    return tags


def extract_json_ld(page_html: str) -> list[object]:
    documents: list[object] = []
    for match in JSON_LD_RE.finditer(page_html):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            documents.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return documents


def extract_next_data(page_html: str) -> object | None:
    match = NEXT_DATA_RE.search(page_html)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def iter_json_nodes(node: object):
    """Yield every dict nested inside *node*."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from iter_json_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_json_nodes(item)


def first_string(node: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def first_price(node: dict) -> tuple[str, str]:
    offers = node.get("offers")
    if isinstance(offers, dict):
        price = offers.get("price")
        currency = offers.get("priceCurrency") or offers.get("currency")
        if price is not None:
            return format_price(price, str(currency) if currency else None), ""
        spec = offers.get("priceSpecification")
        if isinstance(spec, dict):
            return format_price(
                spec.get("price"),
                str(spec.get("priceCurrency") or spec.get("currency") or ""),
            ), ""
    if isinstance(offers, list):
        for offer in offers:
            if isinstance(offer, dict):
                price, _ = first_price(offer)
                if price:
                    return price, ""
    price = node.get("price")
    if price is not None:
        return format_price(price, str(node.get("priceCurrency") or "")), ""
    return "", ""


def collect_images(node: dict) -> list[str]:
    images: list[str] = []
    for key in ("image", "images", "contentUrl", "thumbnailUrl"):
        value = node.get(key)
        if isinstance(value, str):
            images.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    images.append(item)
                elif isinstance(item, dict):
                    url = item.get("contentUrl") or item.get("url")
                    if isinstance(url, str):
                        images.append(url)
        elif isinstance(value, dict):
            url = value.get("contentUrl") or value.get("url")
            if isinstance(url, str):
                images.append(url)
    return images


def additional_property_value(node: dict, *names: str) -> str:
    props = node.get("additionalProperty")
    if not isinstance(props, list):
        return ""
    wanted = {name.lower() for name in names}
    for prop in props:
        if not isinstance(prop, dict):
            continue
        prop_name = str(prop.get("name", "")).lower()
        if prop_name in wanted:
            value = prop.get("value")
            if value is not None:
                return str(value).strip()
    return ""


def infer_size_from_slug(slug: str) -> str:
    slug_lower = slug.lower()
    for token in SIZE_TOKENS:
        if re.search(rf"(?:^|-){re.escape(token)}(?:$|-)", slug_lower):
            return token.upper()
    match = re.search(r"-(\d{1,2})(?:$|-)", slug_lower)
    if match:
        return match.group(1)
    return ""


def is_bot_block(page_html: str) -> bool:
    lowered = page_html.lower()
    return (
        "access denied" in lowered
        or "lv-waiting" in lowered
        or "accès refusé" in lowered
        or "vpn request denied" in lowered
        or "vpn" in lowered and "denied" in lowered
    )


BLOCK_TITLE_RE = re.compile(
    r"<h1[^>]*class=['\"][^'\"]*lv-waiting__top-title[^'\"]*['\"][^>]*>(.*?)</h1>",
    re.IGNORECASE | re.DOTALL,
)
BLOCK_REF_RE = re.compile(r"REF\s#([^<\s]+)", re.IGNORECASE)


def analyze_block_page(page_html: str) -> dict[str, str] | None:
    """Return structured block-page details when LV serves a bot/IP block."""
    if not is_bot_block(page_html):
        return None

    titles = [
        re.sub(r"\s+", " ", html_lib.unescape(match)).strip()
        for match in BLOCK_TITLE_RE.findall(page_html)
    ]
    titles = [title for title in titles if title]
    message = titles[0] if titles else "Access Denied"

    ref_match = BLOCK_REF_RE.search(page_html)
    ref = ref_match.group(1).strip() if ref_match else ""

    lowered = page_html.lower()
    if "vpn request denied" in lowered or (
        "vpn" in lowered and "denied" in lowered
    ):
        block_type = "vpn"
    elif "access denied" in lowered or "accès refusé" in lowered:
        block_type = "access_denied"
    else:
        block_type = "bot_protection"

    if block_type == "vpn":
        hint = (
            "Louis Vuitton rejected traffic from a VPN or proxy IP. "
            "Disable the VPN and retry from a normal residential connection."
        )
    else:
        hint = (
            "Louis Vuitton blocks many VPN, datacenter, cloud, and flagged ISP IPs. "
            "Try without VPN on home broadband or mobile data."
        )

    return {
        "message": message,
        "block_type": block_type,
        "ref": ref,
        "hint": hint,
    }


def format_block_error(info: dict[str, str]) -> str:
    parts = [f"Louis Vuitton blocked this request: {info['message']}"]
    if info.get("ref"):
        parts.append(f"(REF {info['ref']})")
    parts.append(info["hint"])
    return " ".join(parts)


def homepage_url(parsed: dict[str, str]) -> str:
    return f"https://{parsed['host']}/{parsed['lang']}/homepage"


def warmup_session(fetcher: Fetcher, parsed: dict[str, str]) -> None:
    """Visit the regional homepage first to collect storefront cookies."""
    try:
        fetcher.fetch(homepage_url(parsed))
    except (urllib.error.URLError, urllib.error.HTTPError):
        return


def pick_best_image(candidates: list[str]) -> str:
    normalized: list[str] = []
    seen: set[str] = set()
    for url in candidates:
        cleaned = normalize_image_url(url)
        if not cleaned or not is_product_image(cleaned):
            continue
        if cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)
    if not normalized:
        return ""
    return max(normalized, key=lambda item: ("wid=4096" in item, len(item)))


def extract_from_img_tags(page_html: str) -> list[str]:
    urls: list[str] = []
    for match in IMG_SRCSET_RE.finditer(page_html):
        best = parse_srcset(match.group(1))
        if best:
            urls.append(best)
    for match in IMG_TAG_RE.finditer(page_html):
        src = match.group(1).strip()
        if src:
            urls.append(src)
    return urls


def product_from_model(product: dict, sku: str) -> dict | None:
    models = product.get("model")
    if not isinstance(models, list):
        return None
    for model in models:
        if isinstance(model, dict) and str(model.get("identifier", "")).upper() == sku:
            return model
    if models and isinstance(models[0], dict):
        return models[0]
    return None


def build_record(
    *,
    link: str,
    sku: str,
    slug: str,
    name: str = "",
    description: str = "",
    price: str = "",
    material: str = "",
    color: str = "",
    size: str = "",
    images: list[str] | None = None,
) -> dict[str, str]:
    image = pick_best_image(images or [])
    if not size:
        size = infer_size_from_slug(slug)
    return {
        "name": name,
        "description": description,
        "price": price,
        "link": link,
        "material": material,
        "color": color,
        "size": size,
        "image": image,
    }


def merge_record(base: dict[str, str], patch: dict[str, str]) -> dict[str, str]:
    merged = dict(base)
    for key, value in patch.items():
        if key == "image":
            images = [merged.get("image", ""), value]
            merged["image"] = pick_best_image([item for item in images if item])
            continue
        if value and not merged.get(key):
            merged[key] = value
    return merged


def parse_json_ld_documents(documents: list[object], sku: str, slug: str) -> dict[str, str]:
    record = build_record(link="", sku=sku, slug=slug)
    for document in documents:
        nodes: list[dict] = []
        if isinstance(document, list):
            nodes = [node for node in document if isinstance(node, dict)]
        elif isinstance(document, dict):
            graph = document.get("@graph")
            if isinstance(graph, list):
                nodes = [node for node in graph if isinstance(node, dict)]
            else:
                nodes = [document]
        for node in nodes:
            node_type = node.get("@type")
            types = node_type if isinstance(node_type, list) else [node_type]
            if not any(str(item).lower() == "product" for item in types if item):
                continue
            price, _ = first_price(node)
            record = merge_record(
                record,
                {
                    "name": first_string(node, ("name",)),
                    "description": strip_html(
                        first_string(
                            node,
                            ("description", "disambiguatingDescription"),
                        )
                    ),
                    "price": price,
                    "material": first_string(node, ("material",))
                    or additional_property_value(node, "Material", "material"),
                    "color": first_string(node, ("color",))
                    or additional_property_value(node, "Color", "colour", "color"),
                    "size": first_string(node, ("size",))
                    or additional_property_value(node, "Size", "size"),
                    "image": pick_best_image(collect_images(node)),
                },
            )
    return record


def parse_embedded_json(node: object, sku: str, slug: str) -> dict[str, str]:
    record = build_record(link="", sku=sku, slug=slug)
    images: list[str] = []
    for item in iter_json_nodes(node):
        identifier = str(item.get("identifier", "")).upper()
        sku_match = identifier == sku if identifier else False
        if item.get("@type") == "Product" or sku_match or item.get("productId"):
            name = first_string(item, ("name", "productName", "title"))
            description = strip_html(
                first_string(
                    item,
                    ("description", "disambiguatingDescription", "detailedDescription"),
                )
            )
            price, _ = first_price(item)
            material = (
                first_string(item, ("material", "mainMaterial"))
                or additional_property_value(item, "Material", "material")
            )
            color = (
                first_string(item, ("color", "colorName", "colour"))
                or additional_property_value(item, "Color", "colour", "color")
            )
            size = (
                first_string(item, ("size", "sizeLabel"))
                or additional_property_value(item, "Size", "size")
            )
            images.extend(collect_images(item))
            record = merge_record(
                record,
                {
                    "name": name,
                    "description": description,
                    "price": price,
                    "material": material,
                    "color": color,
                    "size": size,
                },
            )
    record = merge_record(record, {"image": pick_best_image(images)})
    return record


def parse_api_product(product: dict, sku: str, slug: str) -> dict[str, str]:
    model = product_from_model(product, sku) or {}
    price, _ = first_price(model)
    if not price:
        price, _ = first_price(product)
    description = strip_html(
        first_string(
            model,
            ("disambiguatingDescription", "description", "detailedDescription"),
        )
        or first_string(
            product,
            ("description", "detailedDescription", "disambiguatingDescription"),
        )
    )
    images = collect_images(model) or collect_images(product)
    record = build_record(
        link="",
        sku=sku,
        slug=slug,
        name=first_string(product, ("name", "productName"))
        or first_string(model, ("name",)),
        description=description,
        price=price,
        material=first_string(product, ("material", "mainMaterial"))
        or first_string(model, ("material", "mainMaterial"))
        or additional_property_value(product, "Material", "material")
        or additional_property_value(model, "Material", "material"),
        color=first_string(model, ("color", "colorName"))
        or first_string(product, ("color", "colorName"))
        or additional_property_value(model, "Color", "colour", "color"),
        size=first_string(model, ("size", "sizeLabel"))
        or first_string(product, ("size", "sizeLabel"))
        or additional_property_value(model, "Size", "size"),
        images=images,
    )
    return record


def api_error(payload: dict) -> str | None:
    message = payload.get("message")
    if message == "Access Denied":
        ref = str(payload.get("ref") or "").strip()
        ip = str(payload.get("ip") or "").strip()
        details = f" REF {ref}" if ref else ""
        if ip:
            details += f" (seen IP {ip})"
        return (
            "Louis Vuitton catalog API blocked this request (Access Denied)."
            f"{details} Louis Vuitton blocks many VPN, datacenter, cloud, and "
            "flagged ISP IPs — disable VPN and retry from a residential connection."
        )
    errors = payload.get("errors")
    if errors:
        return str(errors)
    return None


def fetch_api_product(
    fetcher: Fetcher,
    *,
    api_base: str,
    lang: str,
    sku: str,
    slug: str,
    referer: str,
) -> dict[str, str]:
    sku_url = f"{api_base}/api/{lang}/catalog/sku/{sku}/persodetails"
    sku_payload = json.loads(
        fetcher.fetch(sku_url, referer=referer, api=True).decode("utf-8", errors="replace")
    )
    sku_error = api_error(sku_payload)
    if sku_error:
        raise ValueError(sku_error)
    product_id = sku_payload.get("productId")
    if not product_id:
        raise ValueError("Catalog API did not return a productId.")
    product_url = f"{api_base}/api/{lang}/catalog/product/{product_id}"
    product_payload = json.loads(
        fetcher.fetch(product_url, referer=referer, api=True).decode(
            "utf-8", errors="replace"
        )
    )
    product_error = api_error(product_payload)
    if product_error:
        raise ValueError(product_error)
    return parse_api_product(product_payload, sku, slug)


def extract_product(url: str, fetcher: Fetcher) -> dict[str, str]:
    """Fetch and parse a single Louis Vuitton product page."""
    parsed = parse_product_url(url)
    link = parsed["link"]
    sku = parsed["sku"]
    slug = parsed["slug"]
    lang = parsed["lang"]
    api_base = api_base_for_host(parsed["host"])

    record = build_record(link=link, sku=sku, slug=slug)
    warmup_session(fetcher, parsed)
    page_bytes = fetcher.fetch(link, referer=link)
    page_html = page_bytes.decode("utf-8", errors="replace")

    block = analyze_block_page(page_html)
    if block:
        raise ValueError(format_block_error(block))

    meta = extract_meta_tags(page_html)
    record = merge_record(
        record,
        {
            "name": meta.get("og:title", "") or meta.get("twitter:title", ""),
            "description": meta.get("og:description", "")
            or meta.get("description", "")
            or meta.get("twitter:description", ""),
            "image": meta.get("og:image", "") or meta.get("twitter:image", ""),
        },
    )

    json_ld = parse_json_ld_documents(extract_json_ld(page_html), sku, slug)
    record = merge_record(record, json_ld)
    record["link"] = link

    next_data = extract_next_data(page_html)
    if next_data is not None:
        embedded = parse_embedded_json(next_data, sku, slug)
        record = merge_record(record, embedded)

    img_urls = extract_from_img_tags(page_html)
    if img_urls:
        record = merge_record(record, {"image": pick_best_image(img_urls)})

    needs_api = not any(
        record.get(field)
        for field in ("name", "description", "price", "image")
    )
    if needs_api:
        api_record = fetch_api_product(
            fetcher,
            api_base=api_base,
            lang=lang,
            sku=sku,
            slug=slug,
            referer=link,
        )
        record = merge_record(record, api_record)

    record["link"] = link
    return public_record(record)


def public_record(record: dict[str, str]) -> dict[str, str]:
    """Return only the fields requested by the CLI contract."""
    return {
        "name": record.get("name", ""),
        "description": record.get("description", ""),
        "price": record.get("price", ""),
        "link": record.get("link", ""),
        "material": record.get("material", ""),
        "color": record.get("color", ""),
        "size": record.get("size", ""),
        "image": record.get("image", ""),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract product metadata from Louis Vuitton product pages.",
    )
    parser.add_argument(
        "urls",
        nargs="*",
        default=[DEFAULT_URL],
        help=f"Product page URL(s) to scrape (default: {DEFAULT_URL}).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Print a single pretty-printed JSON array instead of JSON lines.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    fetcher = Fetcher()
    results: list[dict[str, object]] = []
    exit_code = 0

    for url in args.urls:
        print(f"Fetching product page: {url}", file=sys.stderr)
        try:
            product = extract_product(url, fetcher)
            results.append(product)
            if not args.pretty:
                print(json.dumps(product, ensure_ascii=False))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as error:
            exit_code = 1
            message = str(error)
            print(f"FAILED {url}: {message}", file=sys.stderr)
            error_obj = {"link": url, "error": message}
            results.append(error_obj)
            if not args.pretty:
                print(json.dumps(error_obj, ensure_ascii=False))

    if args.pretty:
        print(json.dumps(results, ensure_ascii=False, indent=2))

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
