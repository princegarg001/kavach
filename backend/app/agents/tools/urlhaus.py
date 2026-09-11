"""URLhaus threat intelligence lookup with domain search and caching."""
import logging
import httpx
from cachetools import TTLCache
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("kavach.tools.urlhaus")

URLHAUS_URL_API = "https://urlhaus-api.abuse.ch/v1/url/"
URLHAUS_HOST_API = "https://urlhaus-api.abuse.ch/v1/host/"
TIMEOUT = 10

_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=0.5, max=3), reraise=True)
async def check_urlhaus(url: str) -> dict:
    """
    Check URL and its host against URLhaus malware database.
    First checks exact URL, then falls back to host/domain lookup.
    Returns {'hit': bool, 'threat': str, 'tags': list, 'source': str}.
    """
    cache_key = f"urlhaus:{url}"
    if cache_key in _cache:
        return _cache[cache_key]

    result = {"hit": False}

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            # ── Exact URL check ────────────────────────────────────────────────
            resp = await client.post(
                URLHAUS_URL_API,
                data={"url": url},
                headers={"User-Agent": "kavach-scam-detector/2.0"},
            )

            if resp.status_code == 200:
                data = resp.json()
                if data.get("query_status") == "is_listed":
                    threat = data.get("threat", "unknown")
                    tags = data.get("tags") or []
                    logger.warning(f"🚨 URLhaus URL HIT: {url} — threat={threat} tags={tags}")
                    result = {"hit": True, "threat": threat, "tags": tags, "source": "url_match"}
                    _cache[cache_key] = result
                    return result

            # ── Host/domain fallback check ─────────────────────────────────────
            from urllib.parse import urlparse
            try:
                parsed = urlparse(url)
                host = parsed.netloc or parsed.path.split("/")[0]
            except Exception:
                host = url

            if host:
                host_resp = await client.post(
                    URLHAUS_HOST_API,
                    data={"host": host},
                    headers={"User-Agent": "kavach-scam-detector/2.0"},
                )

                if host_resp.status_code == 200:
                    host_data = host_resp.json()
                    url_count = host_data.get("url_count", 0)
                    if url_count and int(url_count) > 0:
                        urls_listed = host_data.get("urls", [])
                        tags = set()
                        for u in urls_listed[:5]:
                            if u.get("tags"):
                                tags.update(u["tags"].split(",") if isinstance(u["tags"], str) else u["tags"])
                        logger.warning(
                            f"🚨 URLhaus HOST HIT: {host} — "
                            f"{url_count} URLs listed, tags={list(tags)}"
                        )
                        result = {
                            "hit": True,
                            "threat": "host_known_bad",
                            "tags": list(tags),
                            "url_count": int(url_count),
                            "source": "host_match",
                        }

    except Exception as exc:
        logger.warning(f"URLhaus check failed for {url}: {exc}")

    _cache[cache_key] = result
    return result
