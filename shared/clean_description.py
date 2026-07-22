#!/usr/bin/env python3
"""Clean luxury PDP description text: keep prose, drop material/dimension dumps."""

from __future__ import annotations

import re


_STRAP_DIM_RE = re.compile(
    r"(?:"
    r"Strap\s*:|"
    r"Chain\s*:|"
    r"Handle\s*:|"
    r"Fits\s*:|"
    r"Strap drop(?:\s*max)?\s*:|"
    r"Chain drop\s*:|"
    r"Maximum (?:length|drop)|"
    r"Minimum (?:length|drop)|"
    r"Max\.?\s*(?:length|drop)|"
    r"Min\.?\s*(?:length|drop)|"
    r"drop\s*:\s*\d|"
    r"length\s*:\s*\d|"
    r"\d+(?:\.\d+)?\s*(?:inches|cm)\b"
    r")",
    re.I,
)

_MADE_IN_RE = re.compile(
    r"This reference is either Made in.*$",
    re.I | re.S,
)
_HANDMADE_DIM_RE = re.compile(
    r"As this product is handmade, the dimensions indicated may vary\.?",
    re.I,
)
_CHANEL_SITE_RE = re.compile(
    r"\s*on the CHANEL official website\.?\s*$",
    re.I,
)
_MATERIAL_DUMP_START_RE = re.compile(
    r"(?:"
    # LV-style: end of prose then ", BlackLambskin" / ", GrayLambskin leather..."
    r",\s+[A-Z][A-Za-z /&+-]*"
    r"(?:Lambskin|Calf(?:skin)?|Cowhide|Ostrich|Monogram|Damier|Canvas|Leather|Empreinte)"
    r"|"
    # LOEWE bullet features
    r"\s*\*(?:Shoulder|Detachable|Magnetic|Suede|Gold|Silver|Adjustable|Zip|Interior|Exterior)"
    r")"
)


def _trim_sentence_junk(text: str) -> str:
    text = _MADE_IN_RE.sub("", text)
    text = _HANDMADE_DIM_RE.sub("", text)
    text = _CHANEL_SITE_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    return text.strip(" \t\n\r,;.-")


def _cut_at_dimension_clause(text: str) -> str:
    """Drop trailing feature clauses that are mostly dimensions/hardware dumps."""
    # Split Prada-style --- feature lists and keep non-dimension parts briefly
    if "---" in text:
        parts = [p.strip(" -") for p in text.split("---") if p.strip(" -")]
        kept: list[str] = []
        for part in parts:
            if _STRAP_DIM_RE.search(part):
                # Keep a short non-dimension lead-in if present before numbers
                lead = _STRAP_DIM_RE.split(part, maxsplit=1)[0].strip(" -.,;")
                if lead and len(lead) > 8 and not re.search(r"\d", lead):
                    kept.append(lead)
                continue
            # Drop pure lining/hardware-only lines
            if re.fullmatch(
                r"(?:Metal hardware|Enameled metal triangle logo on the front|"
                r"Nylon lining(?: with .*)?|Unlined|Zipper closure|"
                r"Logo-print nylon lining)",
                part,
                re.I,
            ):
                continue
            kept.append(part)
        text = ". ".join(kept)

    m = _STRAP_DIM_RE.search(text)
    if m and m.start() > 40:
        text = text[: m.start()]

    m = _MATERIAL_DUMP_START_RE.search(text)
    if m and m.start() > 40:
        text = text[: m.start()]

    return _trim_sentence_junk(text)


def clean_description(raw: str, brand_code: str = "") -> str:
    """Return marketing/prose description without material & dimension dumps."""
    if not raw:
        return ""
    text = raw.replace("\xa0", " ").replace("\r", " ").strip()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)

    brand = (brand_code or "").upper()

    if brand == "CH":
        # "Flap Bags of the X collection: Name, materials, color"
        text = _CHANEL_SITE_RE.sub("", text).strip()
        if ":" in text:
            left, right = text.split(":", 1)
            # Prefer the product clause after the colon
            text = right.strip() or text
        text = _trim_sentence_junk(text)
        return text

    if brand == "LOE":
        # Keep prose before feature bullets
        if "*" in text:
            text = text.split("*", 1)[0]
        return _trim_sentence_junk(text)

    if brand == "PRA":
        return _cut_at_dimension_clause(text)

    if brand in {"LV", "HER", "MM", "YSL", "BAL"}:
        return _cut_at_dimension_clause(text)

    return _cut_at_dimension_clause(text)


def looks_like_material_only(text: str) -> bool:
    """True when cleaned text is still mostly materials/features, not prose."""
    if not text:
        return True
    if len(text) < 40 and "---" not in text:
        # short feature fragment
        return bool(
            re.search(
                r"(?:leather|canvas|nylon|hardware|lining|strap|handle|pocket)",
                text,
                re.I,
            )
        )
    # No sentence-ending punctuation and lots of feature separators
    if text.count("---") >= 1 or (text.count("-") >= 3 and "." not in text):
        return True
    return False
