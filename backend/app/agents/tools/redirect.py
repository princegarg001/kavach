"""Redirect chain resolution with cloaking detection."""
import logging
import httpx
from cachetools import TTLCache

logger = logging.getLogger("kavach.tools.redirect")

MAX_REDIRECTS = 10
TIMEOUT = 10

_cache: TTLCache = TTLCache(maxsize=300, ttl=1800)  # 30 min cache


async def resolve_redirect_chain(url: str) -> dict:
    """
    Follow redirects and return the final landing URL.
    Also checks for cloaking by comparing mobile vs desktop user agents.
    Returns {'final_url': str, 'hops': int, 'chain': list, 'cloaking_detected': bool}.
    """
    cache_key = f"redir:{url}"
    if cache_key in _cache:
        return _cache[cache_key]

    mobile_result = await _resolve_with_ua(url, user_agent=(
        "Mozilla/5.0 (Linux; Android 13; SM-A515F) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/112.0.0.0 Mobile Safari/537.36"
    ))

    # Check for cloaking: also resolve with a bot-like UA
    bot_result = await _resolve_with_ua(url, user_agent="kavach-bot/2.0")

    cloaking_detected = False
    if mobile_result.get("final_url") and bot_result.get("final_url"):
        from urllib.parse import urlparse
        mobile_domain = urlparse(mobile_result["final_url"]).netloc
        bot_domain = urlparse(bot_result["final_url"]).netloc
        if mobile_domain != bot_domain:
            cloaking_detected = True
            logger.warning(
                f"🕵️ CLOAKING detected: {url} → "
                f"mobile={mobile_domain} vs bot={bot_domain}"
            )

    mobile_result["cloaking_detected"] = cloaking_detected
    mobile_result["bot_final_url"] = bot_result.get("final_url")

    _cache[cache_key] = mobile_result
    return mobile_result


async def _resolve_with_ua(url: str, user_agent: str) -> dict:
    """Follow redirects with a specific user agent."""
    try:
        chain = [url]
        async with httpx.AsyncClient(
            timeout=TIMEOUT,
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            headers={"User-Agent": user_agent},
        ) as client:
            response = await client.get(url)
            # Collect history with status codes
            for r in response.history:
                location = r.headers.get("location")
                if location:
                    chain.append(str(location))
            final_url = str(response.url)
            if final_url not in chain:
                chain.append(final_url)

        hops = len(chain) - 1
        logger.info(f"🔗 Redirect: {url} → {final_url} ({hops} hops)")
        return {
            "final_url": final_url,
            "hops": hops,
            "chain": chain,
            "status_code": response.status_code,
        }

    except Exception as exc:
        logger.warning(f"Redirect resolution failed for {url}: {exc}")
        return {"final_url": url, "hops": 0, "chain": [url]}
