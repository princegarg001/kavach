"""Google Safe Browsing API v4 lookup — most comprehensive URL threat database."""
import logging
from typing import Optional
import httpx
from cachetools import TTLCache
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger("kavach.tools.gsb")

# Cache results for 1 hour
_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)

GSB_API_URL = "https://safebrowsing.googleapis.com/v4/threatMatches:find"


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=0.5, max=3), reraise=True)
async def check_google_safe_browsing(url: str) -> dict:
    """
    Check URL against Google Safe Browsing API.
    Returns {'hit': bool, 'threats': list} or {'hit': False} on failure.

    Threat types: MALWARE, SOCIAL_ENGINEERING, UNWANTED_SOFTWARE, POTENTIALLY_HARMFUL_APPLICATION
    """
    if not settings.GOOGLE_SAFE_BROWSING_KEY:
        return {"hit": False, "reason": "no_api_key"}

    cache_key = f"gsb:{url}"
    if cache_key in _cache:
        return _cache[cache_key]

    try:
        payload = {
            "client": {
                "clientId": "kavach-scam-detector",
                "clientVersion": "2.0.0",
            },
            "threatInfo": {
                "threatTypes": [
                    "MALWARE",
                    "SOCIAL_ENGINEERING",
                    "UNWANTED_SOFTWARE",
                    "POTENTIALLY_HARMFUL_APPLICATION",
                ],
                "platformTypes": ["ANY_PLATFORM"],
                "threatEntryTypes": ["URL"],
                "threatEntries": [{"url": url}],
            },
        }

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{GSB_API_URL}?key={settings.GOOGLE_SAFE_BROWSING_KEY}",
                json=payload,
            )

        if resp.status_code != 200:
            return {"hit": False}

        data = resp.json()
        matches = data.get("matches", [])

        if matches:
            threats = list(set(m.get("threatType", "UNKNOWN") for m in matches))
            logger.warning(f"🚨 Google Safe Browsing HIT: {url} — threats={threats}")
            result = {"hit": True, "threats": threats}
        else:
            result = {"hit": False}

        _cache[cache_key] = result
        return result

    except Exception as exc:
        logger.warning(f"Google Safe Browsing check failed for {url}: {exc}")
        return {"hit": False}
