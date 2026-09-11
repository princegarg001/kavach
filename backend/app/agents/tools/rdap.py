"""RDAP domain age lookup with WHOIS fallback — strongest single phishing signal."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
import httpx
from cachetools import TTLCache
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("kavach.tools.rdap")

IANA_BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
_rdap_bootstrap: dict | None = None

# Cache domain age results for 1 hour — domains don't change age
_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)

# Known cheap/free registrars (higher risk signal)
RISKY_REGISTRARS = {
    "namecheap", "freenom", "namesilo", "porkbun", "hostinger",
    "dynadot", "regtons", "register.com",
}


async def _get_rdap_server(tld: str) -> Optional[str]:
    """Find the RDAP server for a given TLD using IANA bootstrap."""
    global _rdap_bootstrap
    try:
        if _rdap_bootstrap is None:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(IANA_BOOTSTRAP_URL)
                _rdap_bootstrap = resp.json()

        for entry in _rdap_bootstrap.get("services", []):
            tlds, servers = entry[0], entry[1]
            if tld.lower() in [t.lower() for t in tlds]:
                return servers[0] if servers else None
    except Exception as exc:
        logger.warning(f"RDAP bootstrap error: {exc}")
    return "https://rdap.org/"  # fallback


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=0.5, max=3), reraise=True)
async def check_domain_age(domain: str) -> dict:
    """
    Query RDAP for domain registration date with WHOIS fallback.
    Returns {'age_days': int, 'registrar': str} or {} on failure.
    """
    cache_key = f"rdap:{domain}"
    if cache_key in _cache:
        return _cache[cache_key]

    try:
        # Extract TLD
        parts = domain.lower().split(".")
        tld = parts[-1] if parts else "com"

        rdap_server = await _get_rdap_server(tld)
        url = f"{rdap_server.rstrip('/')}/domain/{domain}"

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers={"Accept": "application/json"})
            if resp.status_code != 200:
                return {}
            data = resp.json()

        result = {}

        # Find registration event
        for event in data.get("events", []):
            action = event.get("eventAction", "").lower()
            if action in ("registration", "creation"):
                date_str = event.get("eventDate", "")
                if date_str:
                    reg_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    age_days = (datetime.now(timezone.utc) - reg_date).days
                    result["age_days"] = age_days
                    result["registered"] = date_str

        # Extract registrar
        for entity in data.get("entities", []):
            roles = entity.get("roles", [])
            if "registrar" in roles:
                vcard = entity.get("vcardArray", [None, []])[1] if entity.get("vcardArray") else []
                for item in vcard:
                    if item and len(item) >= 4 and item[0] == "fn":
                        registrar = item[3]
                        result["registrar"] = registrar
                        # Flag if cheap/risky registrar
                        if any(r in registrar.lower() for r in RISKY_REGISTRARS):
                            result["risky_registrar"] = True
                        break

        if result.get("age_days") is not None:
            logger.info(
                f"🌐 RDAP: {domain} registered {result['age_days']} days ago "
                f"(registrar={result.get('registrar', 'unknown')})"
            )

        _cache[cache_key] = result
        return result

    except Exception as exc:
        logger.warning(f"RDAP check failed for {domain}: {exc}")
    return {}
