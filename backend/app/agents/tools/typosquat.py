"""
Typosquat detection with IDN homograph attack detection.
Uses rapidfuzz Levenshtein distance + Unicode confusable character analysis.
"""
import logging
import unicodedata
from typing import Optional
from rapidfuzz import fuzz, process
from rapidfuzz.distance import Levenshtein

logger = logging.getLogger("kavach.tools.typosquat")

# ── Expanded brand list — Indian banks, telecom, e-commerce, government ────────
BRAND_LIST = [
    # Indian Banks
    "hdfcbank", "sbibank", "icicibank", "axisbank", "kotakbank",
    "pnbbank", "unionbank", "bankofbaroda", "canarabank", "idfcbank",
    "yesbank", "indusindbank", "federalbank", "rblbank", "csbbank",
    "bobfinancial", "bandhanbank", "indianbank", "centralbank",
    # Payment / UPI
    "paytm", "phonepe", "googlepay", "amazonpay", "bhimupi",
    "npci", "rbi", "irdai", "cred", "mobikwik", "freecharge",
    # Telecom
    "jio", "airtel", "vodafone", "bsnl", "vi",
    # E-commerce
    "amazon", "flipkart", "myntra", "meesho", "snapdeal",
    "ajio", "nykaa", "tatacliq", "bigbasket",
    # Food / Delivery
    "swiggy", "zomato", "dunzo", "blinkit", "zepto",
    # Government
    "incometax", "incometaxindia", "epfindia", "nsdl", "uidai", "aadhar",
    "aadhaar", "digilocker", "umang", "irctc", "cowin",
    "egovernance", "parivahan", "passportindia",
    # Insurance / Financial
    "licindia", "policybazaar", "etmoney", "groww", "zerodha",
    "upstox", "angelone",
    # Global
    "google", "microsoft", "apple", "facebook", "instagram",
    "whatsapp", "netflix", "youtube", "twitter", "linkedin",
    "telegram", "paypal",
]

# ── IDN homograph confusable characters ────────────────────────────────────────
# Maps visually similar Unicode characters to their ASCII equivalents
CONFUSABLES = {
    "а": "a",  # Cyrillic а → Latin a
    "е": "e",  # Cyrillic е → Latin e
    "о": "o",  # Cyrillic о → Latin o
    "р": "p",  # Cyrillic р → Latin p
    "с": "c",  # Cyrillic с → Latin c
    "у": "y",  # Cyrillic у → Latin y
    "і": "i",  # Ukrainian і → Latin i
    "ɡ": "g",  # Latin small letter script g
    "ⅼ": "l",  # Roman numeral one
    "ı": "i",  # Turkish dotless i
    "ο": "o",  # Greek omicron → Latin o
    "ν": "v",  # Greek nu → Latin v
    "τ": "t",  # Greek tau → Latin t
    "ᴀ": "a",  # Latin letter small capital A
    "ʙ": "b",  # Latin letter small capital B
    "ᴅ": "d",  # Latin letter small capital D
    "ɴ": "n",  # Latin letter small capital N
    "ꜱ": "s",  # Latin letter small capital S
    "ᴛ": "t",  # Latin letter small capital T
    "０": "0",  # Fullwidth digit 0
    "１": "1",  # Fullwidth digit 1
}


def _extract_domain_name(domain: str) -> str:
    """Strip TLD and www prefix to get the core brand name."""
    domain = domain.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    # Remove TLD
    parts = domain.split(".")
    if len(parts) >= 2:
        return parts[-2]
    return domain


def _extract_subdomain_parts(domain: str) -> list[str]:
    """Get all meaningful parts of a domain for analysis."""
    domain = domain.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    parts = domain.split(".")
    # Return all parts except TLD
    return parts[:-1] if len(parts) > 1 else parts


def _detect_homograph(domain: str) -> tuple[bool, str]:
    """
    Check if the domain contains IDN homograph characters.
    Returns (is_homograph, normalized_domain).
    """
    has_confusable = False
    normalized = []
    for char in domain.lower():
        if char in CONFUSABLES:
            normalized.append(CONFUSABLES[char])
            has_confusable = True
        else:
            normalized.append(char)
    return has_confusable, "".join(normalized)


def _has_mixed_scripts(domain: str) -> bool:
    """Check if domain uses characters from multiple Unicode scripts (suspicious)."""
    scripts = set()
    for char in domain:
        if char.isalpha():
            try:
                script = unicodedata.name(char, "").split()[0]
                scripts.add(script)
            except ValueError:
                pass
    # More than 1 script = suspicious
    return len(scripts) > 1


async def check_typosquat(domain: str) -> dict:
    """
    Check if domain is a typosquat or IDN homograph of a known brand.
    Returns {'target': str, 'distance': int, 'score': float, 'homograph': bool}.
    """
    try:
        core = _extract_domain_name(domain)
        if len(core) < 3:
            return {}

        # ── Check 1: IDN homograph detection ───────────────────────────────────
        is_homograph, normalized = _detect_homograph(domain)
        if is_homograph:
            # Check if the normalized version matches a brand
            normalized_core = _extract_domain_name(normalized)
            if normalized_core in BRAND_LIST:
                logger.warning(
                    f"🎯 HOMOGRAPH ATTACK: {domain} → {normalized_core} "
                    f"(uses confusable Unicode characters)"
                )
                return {
                    "target": normalized_core,
                    "distance": 0,
                    "score": 100.0,
                    "core": core,
                    "homograph": True,
                    "normalized": normalized,
                }

        # ── Check 2: Mixed script detection ────────────────────────────────────
        mixed_scripts = _has_mixed_scripts(core)

        # ── Check 3: Classic typosquat (Levenshtein distance) ──────────────────
        match = process.extractOne(
            core,
            BRAND_LIST,
            scorer=fuzz.ratio,
            score_cutoff=55,  # Slightly lower cutoff for more catches
        )

        if match:
            brand_name, score, _ = match
            distance = Levenshtein.distance(core, brand_name)

            if distance <= 3:  # Max 3 character edits
                logger.warning(
                    f"🎯 Typosquat: {domain} → {brand_name} "
                    f"(distance={distance}, score={score:.1f})"
                )
                return {
                    "target": brand_name,
                    "distance": distance,
                    "score": score,
                    "core": core,
                    "homograph": False,
                    "mixed_scripts": mixed_scripts,
                }

        # ── Check 4: Subdomain brand impersonation ─────────────────────────────
        # e.g., hdfc.phishing-domain.com
        parts = _extract_subdomain_parts(domain)
        for part in parts:
            if part in BRAND_LIST and part != core:
                logger.warning(
                    f"🎯 Subdomain impersonation: {domain} contains brand '{part}' in subdomain"
                )
                return {
                    "target": part,
                    "distance": 0,
                    "score": 85.0,
                    "core": core,
                    "homograph": False,
                    "subdomain_impersonation": True,
                }

    except Exception as exc:
        logger.warning(f"Typosquat check error for {domain}: {exc}")
    return {}
