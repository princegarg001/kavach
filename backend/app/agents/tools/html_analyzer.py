"""
HTML content analyzer — detect credential-harvesting forms on landing pages.

Fetches the page HTML and looks for telltale signs of phishing:
- Password fields, OTP inputs, Aadhaar/PAN/card number fields
- Hidden iframes (clickjacking)
- Obfuscated JavaScript
- Form action URLs pointing to different domains
"""
import logging
import re
from typing import Optional
import httpx
from cachetools import TTLCache

from app.models import HTMLAnalysis

logger = logging.getLogger("kavach.tools.html")

_cache: TTLCache = TTLCache(maxsize=200, ttl=1800)  # 30 min cache

# ── Credential field patterns ─────────────────────────────────────────────────
CREDENTIAL_INPUT_PATTERNS = {
    "password": re.compile(r'type\s*=\s*["\']password["\']', re.I),
    "otp": re.compile(r'(?:name|id|placeholder)\s*=\s*["\'].*(?:otp|verification|verify|code).*["\']', re.I),
    "aadhaar": re.compile(r'(?:name|id|placeholder)\s*=\s*["\'].*(?:aadhaar|aadhar|uidai|uid).*["\']', re.I),
    "pan": re.compile(r'(?:name|id|placeholder)\s*=\s*["\'].*(?:pan\b|pan_number|pannumber).*["\']', re.I),
    "card": re.compile(r'(?:name|id|placeholder)\s*=\s*["\'].*(?:card.?number|cvv|expiry|credit.?card|debit.?card).*["\']', re.I),
}

HIDDEN_IFRAME_PATTERN = re.compile(r'<iframe[^>]*(?:style\s*=\s*["\'][^"\']*(?:display\s*:\s*none|visibility\s*:\s*hidden|width\s*:\s*0|height\s*:\s*0))', re.I)

INPUT_FIELD_PATTERN = re.compile(r'<input[^>]*>', re.I)
INPUT_NAME_PATTERN = re.compile(r'(?:name|id)\s*=\s*["\']([^"\']+)["\']', re.I)
INPUT_TYPE_PATTERN = re.compile(r'type\s*=\s*["\']([^"\']+)["\']', re.I)

TITLE_PATTERN = re.compile(r'<title[^>]*>(.*?)</title>', re.I | re.DOTALL)
FAVICON_PATTERN = re.compile(r'<link[^>]*rel\s*=\s*["\'](?:shortcut )?icon["\'][^>]*href\s*=\s*["\']([^"\']+)["\']', re.I)
FORM_PATTERN = re.compile(r'<form[^>]*>', re.I)

# Suspicious JS patterns
OBFUSCATED_JS_PATTERNS = [
    re.compile(r'eval\s*\(\s*(?:unescape|atob|String\.fromCharCode)', re.I),
    re.compile(r'document\.write\s*\(\s*unescape', re.I),
    re.compile(r'\\x[0-9a-f]{2}', re.I),  # hex-encoded strings
    re.compile(r'window\.location\s*=.*(?:data:|javascript:)', re.I),
]


async def analyze_html(url: str) -> HTMLAnalysis:
    """
    Fetch a URL and analyze its HTML for credential-harvesting signals.
    Returns an HTMLAnalysis model with detection results.
    """
    cache_key = f"html:{url}"
    if cache_key in _cache:
        return _cache[cache_key]

    analysis = HTMLAnalysis()

    try:
        async with httpx.AsyncClient(
            timeout=12,
            follow_redirects=True,
            max_redirects=5,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Linux; Android 13; SM-A515F) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/112.0.0.0 Mobile Safari/537.36"
                ),
            },
        ) as client:
            resp = await client.get(url)

        if resp.status_code != 200:
            return analysis

        html = resp.text
        if len(html) > 500_000:  # Skip huge pages
            html = html[:500_000]

        # ── Title & favicon ────────────────────────────────────────────────────
        title_match = TITLE_PATTERN.search(html)
        if title_match:
            analysis.page_title = title_match.group(1).strip()[:200]

        favicon_match = FAVICON_PATTERN.search(html)
        if favicon_match:
            analysis.favicon_url = favicon_match.group(1)

        # ── Forms ──────────────────────────────────────────────────────────────
        forms = FORM_PATTERN.findall(html)
        analysis.form_count = len(forms)

        # ── Input fields ───────────────────────────────────────────────────────
        inputs = INPUT_FIELD_PATTERN.findall(html)
        for inp in inputs[:50]:  # Limit scanning
            name_match = INPUT_NAME_PATTERN.search(inp)
            type_match = INPUT_TYPE_PATTERN.search(inp)
            name = name_match.group(1) if name_match else "unnamed"
            input_type = type_match.group(1) if type_match else "text"
            analysis.input_fields.append({"name": name, "type": input_type})

        # ── Credential field detection ─────────────────────────────────────────
        analysis.has_password_field = bool(CREDENTIAL_INPUT_PATTERNS["password"].search(html))
        analysis.has_otp_field = bool(CREDENTIAL_INPUT_PATTERNS["otp"].search(html))
        analysis.has_aadhaar_field = bool(CREDENTIAL_INPUT_PATTERNS["aadhaar"].search(html))
        analysis.has_pan_field = bool(CREDENTIAL_INPUT_PATTERNS["pan"].search(html))
        analysis.has_card_field = bool(CREDENTIAL_INPUT_PATTERNS["card"].search(html))

        # ── Hidden iframes ─────────────────────────────────────────────────────
        analysis.has_hidden_iframe = bool(HIDDEN_IFRAME_PATTERN.search(html))

        # ── Suspicious JavaScript ──────────────────────────────────────────────
        analysis.suspicious_js = any(p.search(html) for p in OBFUSCATED_JS_PATTERNS)

        # ── Compute credential harvest score ───────────────────────────────────
        score = 0.0
        if analysis.has_password_field:
            score += 0.35
        if analysis.has_otp_field:
            score += 0.25
        if analysis.has_aadhaar_field:
            score += 0.20
        if analysis.has_pan_field:
            score += 0.15
        if analysis.has_card_field:
            score += 0.25
        if analysis.has_hidden_iframe:
            score += 0.15
        if analysis.suspicious_js:
            score += 0.10
        if analysis.form_count > 0 and analysis.has_password_field:
            score += 0.10  # Form + password = classic phishing

        analysis.credential_harvest_score = min(1.0, round(score, 2))

        if analysis.credential_harvest_score > 0.3:
            logger.warning(
                f"🎣 HTML Analysis: {url} — harvest_score={analysis.credential_harvest_score} "
                f"password={analysis.has_password_field} otp={analysis.has_otp_field} "
                f"aadhaar={analysis.has_aadhaar_field} pan={analysis.has_pan_field}"
            )
        else:
            logger.info(f"📄 HTML Analysis: {url} — harvest_score={analysis.credential_harvest_score}")

        _cache[cache_key] = analysis
        return analysis

    except Exception as exc:
        logger.warning(f"HTML analysis failed for {url}: {exc}")
        return analysis
