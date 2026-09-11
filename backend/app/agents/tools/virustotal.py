"""VirusTotal URL scan — 70+ antivirus engines."""
import logging
import httpx
from cachetools import TTLCache
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger("kavach.tools.virustotal")

# Cache results for 1 hour
_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)

VT_API_URL = "https://www.virustotal.com/api/v3/urls"


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=5), reraise=True)
async def check_virustotal(url: str) -> dict:
    """
    Check URL against VirusTotal.
    Returns {'hit': bool, 'positives': int, 'total': int, 'engines': list}.

    Free tier: 4 requests/minute, 500 requests/day.
    """
    if not settings.VIRUSTOTAL_API_KEY:
        return {"hit": False, "reason": "no_api_key"}

    cache_key = f"vt:{url}"
    if cache_key in _cache:
        return _cache[cache_key]

    try:
        import base64
        # VT uses base64-encoded URL as the identifier
        url_id = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")

        async with httpx.AsyncClient(timeout=15) as client:
            # First, submit the URL for scanning
            resp = await client.post(
                VT_API_URL,
                headers={"x-apikey": settings.VIRUSTOTAL_API_KEY},
                data={"url": url},
            )

            if resp.status_code == 200:
                # Then get the analysis results
                analysis_resp = await client.get(
                    f"{VT_API_URL}/{url_id}",
                    headers={"x-apikey": settings.VIRUSTOTAL_API_KEY},
                )

                if analysis_resp.status_code == 200:
                    data = analysis_resp.json()
                    attrs = data.get("data", {}).get("attributes", {})
                    stats = attrs.get("last_analysis_stats", {})
                    malicious = stats.get("malicious", 0)
                    suspicious = stats.get("suspicious", 0)
                    total = sum(stats.values()) if stats else 0

                    positives = malicious + suspicious
                    hit = positives > 0

                    if hit:
                        # Get which engines flagged it
                        results = attrs.get("last_analysis_results", {})
                        flagging_engines = [
                            name for name, result in results.items()
                            if result.get("category") in ("malicious", "suspicious")
                        ]
                        logger.warning(
                            f"🚨 VirusTotal HIT: {url} — "
                            f"{positives}/{total} engines flagged"
                        )
                        result = {
                            "hit": True,
                            "positives": positives,
                            "total": total,
                            "engines": flagging_engines[:10],
                        }
                    else:
                        result = {"hit": False, "positives": 0, "total": total}

                    _cache[cache_key] = result
                    return result

        return {"hit": False}

    except Exception as exc:
        logger.warning(f"VirusTotal check failed for {url}: {exc}")
        return {"hit": False}
