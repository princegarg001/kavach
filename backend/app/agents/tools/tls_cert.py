"""TLS certificate age, issuer, and SAN check with caching."""
import logging
import ssl
import socket
from datetime import datetime, timezone
from typing import Optional
from cachetools import TTLCache

logger = logging.getLogger("kavach.tools.tls")

# Cache TLS results for 1 hour
_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)


async def check_tls_cert(domain: str) -> dict:
    """
    Check TLS certificate age, issuer, and Subject Alternative Names.
    Returns {'age_days': int, 'issuer': str, 'san_domains': list} or {} on failure.
    """
    cache_key = f"tls:{domain}"
    if cache_key in _cache:
        return _cache[cache_key]

    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _check_cert_sync, domain)

    if result:
        _cache[cache_key] = result
    return result


def _check_cert_sync(domain: str) -> dict:
    """Synchronous TLS cert check (runs in thread pool)."""
    try:
        ctx = ssl.create_default_context()
        # Allow self-signed certs — we WANT to see them
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_OPTIONAL

        with socket.create_connection((domain, 443), timeout=8) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()

        if not cert:
            return {}

        result = {}

        # Parse issue date
        not_before_str = cert.get("notBefore", "")
        if not_before_str:
            not_before = datetime.strptime(not_before_str, "%b %d %H:%M:%S %Y %Z")
            not_before = not_before.replace(tzinfo=timezone.utc)
            result["age_days"] = (datetime.now(timezone.utc) - not_before).days

        # Parse expiry date
        not_after_str = cert.get("notAfter", "")
        if not_after_str:
            not_after = datetime.strptime(not_after_str, "%b %d %H:%M:%S %Y %Z")
            not_after = not_after.replace(tzinfo=timezone.utc)
            result["expires_days"] = (not_after - datetime.now(timezone.utc)).days

        # Extract issuer O (organization)
        issuer_dict = {}
        for item in cert.get("issuer", []):
            for k, v in item:
                issuer_dict[k] = v
        issuer = issuer_dict.get("organizationName", issuer_dict.get("O", "Unknown"))
        result["issuer"] = issuer

        # Check if it's a free/automated cert (higher phishing risk)
        free_issuers = {"Let's Encrypt", "ZeroSSL", "Buypass", "SSL.com"}
        result["free_cert"] = any(fi.lower() in issuer.lower() for fi in free_issuers)

        # Extract Subject Alternative Names (SANs)
        san_domains = []
        for san_type, san_value in cert.get("subjectAltName", []):
            if san_type == "DNS":
                san_domains.append(san_value)
        result["san_domains"] = san_domains[:20]  # Limit

        # Suspicious SANs: too many unrelated domains on one cert
        result["suspicious_san_count"] = len(san_domains) > 10

        logger.info(
            f"🔒 TLS: {domain} cert issued by {issuer}, "
            f"{result.get('age_days', '?')} days ago, "
            f"free={result.get('free_cert', False)}, "
            f"SANs={len(san_domains)}"
        )
        return result

    except Exception as exc:
        logger.warning(f"TLS check failed for {domain}: {exc}")
        return {}
