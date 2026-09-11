"""PhishTank phishing URL lookup with caching and retries."""
import logging
import httpx
from cachetools import TTLCache
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("kavach.tools.phishtank")

PHISHTANK_API = "https://checkurl.phishtank.com/checkurl/"
TIMEOUT = 10

_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=0.5, max=3), reraise=True)
async def check_phishtank(url: str) -> dict:
    """
    Check URL against PhishTank database.
    Returns {'hit': bool, 'verified': bool, 'phish_id': str} or {'hit': False}.
    """
    cache_key = f"phishtank:{url}"
    if cache_key in _cache:
        return _cache[cache_key]

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                PHISHTANK_API,
                data={"url": url, "format": "json"},
                headers={"User-Agent": "phishtank/kavach-scam-detector"},
            )

        if resp.status_code != 200:
            return {"hit": False}

        data = resp.json()
        results = data.get("results", {})
        in_database = results.get("in_database", False)
        valid = results.get("valid", False)
        verified = results.get("verified", False)
        phish_id = results.get("phish_id", "")

        if in_database and valid:
            logger.warning(f"🎣 PhishTank HIT: {url} (verified={verified}, id={phish_id})")
            result = {"hit": True, "verified": verified, "phish_id": str(phish_id)}
        else:
            result = {"hit": False}

        _cache[cache_key] = result
        return result

    except Exception as exc:
        logger.warning(f"PhishTank check failed for {url}: {exc}")
        return {"hit": False}
