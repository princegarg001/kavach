"""
Tool registry for the agentic Investigator.

Wraps the raw investigation tools (rdap, tls, redirect, threat-intel, typosquat,
html, browser) as OpenAI function-calling tools with JSON schemas, and provides a
single dispatch entry point that also folds results into shared accumulators
(DomainIntel / HTMLAnalysis) so the final verdict object still has structured
intel for the dashboard.

This is the seam where a Strands Agents SDK adapter would plug in: the same
callables + schemas map directly onto Strands `@tool` definitions.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict
from urllib.parse import urlparse

from app.models import DomainIntel, HTMLAnalysis
from app.agents.tools.rdap import check_domain_age
from app.agents.tools.tls_cert import check_tls_cert
from app.agents.tools.redirect import resolve_redirect_chain
from app.agents.tools.urlhaus import check_urlhaus
from app.agents.tools.phishtank import check_phishtank
from app.agents.tools.typosquat import check_typosquat
from app.agents.tools.browser import capture_screenshot
from app.agents.tools.google_safe_browsing import check_google_safe_browsing
from app.agents.tools.virustotal import check_virustotal
from app.agents.tools.html_analyzer import analyze_html

logger = logging.getLogger("kavach.tools.registry")


def _domain_of(url_or_domain: str) -> str:
    s = url_or_domain.strip()
    if "://" not in s and not s.startswith("www."):
        # already looks like a bare domain
        if "/" not in s:
            return s
    try:
        parsed = urlparse(s if "://" in s else f"http://{s}")
        return parsed.netloc or parsed.path.split("/")[0]
    except Exception:
        return s


class InvestigationState:
    """Mutable accumulator shared across a single investigation run."""

    def __init__(self) -> None:
        self.domains: Dict[str, DomainIntel] = {}
        self.html: HTMLAnalysis | None = None
        self.screenshot_b64: str | None = None
        self.tools_used: set[str] = set()
        self.tools_failed: set[str] = set()
        # Filled by the terminal verdict tool in the Strands path.
        self.verdict: dict | None = None

    def domain(self, name: str) -> DomainIntel:
        if name not in self.domains:
            self.domains[name] = DomainIntel(domain=name)
        return self.domains[name]


# ── Individual tool implementations (return compact dicts for the model) ────────

async def _t_domain_age(state: InvestigationState, domain: str = "", **_: Any) -> dict:
    d = _domain_of(domain)
    data = await check_domain_age(d)
    di = state.domain(d)
    di.age_days = data.get("age_days")
    di.registrar = data.get("registrar")
    return {
        "domain": d,
        "age_days": data.get("age_days"),
        "registrar": data.get("registrar"),
        "risky_registrar": data.get("risky_registrar", False),
        "note": "domains registered < 30 days ago are a strong phishing signal",
    }


async def _t_tls(state: InvestigationState, domain: str = "", **_: Any) -> dict:
    d = _domain_of(domain)
    data = await check_tls_cert(d)
    di = state.domain(d)
    di.tls_age_days = data.get("age_days")
    di.tls_issuer = data.get("issuer")
    di.tls_san_domains = data.get("san_domains", [])
    return {
        "domain": d,
        "tls_age_days": data.get("age_days"),
        "issuer": data.get("issuer"),
        "free_cert": data.get("free_cert", False),
        "san_count": len(data.get("san_domains", [])),
    }


async def _t_redirects(state: InvestigationState, url: str = "", **_: Any) -> dict:
    data = await resolve_redirect_chain(url)
    di = state.domain(_domain_of(url))
    di.redirect_final_url = data.get("final_url")
    di.redirect_hops = data.get("hops", 0)
    di.redirect_chain = data.get("chain", [])
    return {
        "final_url": data.get("final_url"),
        "hops": data.get("hops", 0),
        "cloaking_detected": data.get("cloaking_detected", False),
    }


async def _t_threat_intel(state: InvestigationState, url: str = "", **_: Any) -> dict:
    """Batched 'is this a known-bad URL' check across 4 feeds."""
    uh, pt, gsb, vt = await asyncio.gather(
        check_urlhaus(url), check_phishtank(url),
        check_google_safe_browsing(url), check_virustotal(url),
        return_exceptions=True,
    )
    uh = uh if isinstance(uh, dict) else {}
    pt = pt if isinstance(pt, dict) else {}
    gsb = gsb if isinstance(gsb, dict) else {}
    vt = vt if isinstance(vt, dict) else {}

    di = state.domain(_domain_of(url))
    di.urlhaus_hit = uh.get("hit", False)
    di.urlhaus_threat = uh.get("threat")
    di.phishtank_hit = pt.get("hit", False)
    di.phishtank_verified = pt.get("verified", False)
    di.google_safe_browsing_hit = gsb.get("hit", False)
    di.google_safe_browsing_threats = gsb.get("threats", [])
    di.virustotal_positives = vt.get("positives", 0)
    di.virustotal_total = vt.get("total", 0)

    any_hit = di.urlhaus_hit or di.phishtank_hit or di.google_safe_browsing_hit or di.virustotal_positives > 0
    return {
        "known_malicious": any_hit,
        "urlhaus_hit": di.urlhaus_hit,
        "phishtank_hit": di.phishtank_hit,
        "google_safe_browsing_hit": di.google_safe_browsing_hit,
        "virustotal": f"{di.virustotal_positives}/{di.virustotal_total}",
        "note": "any single hit here = confirmed malicious, you can finish immediately",
    }


async def _t_brand(state: InvestigationState, domain: str = "", **_: Any) -> dict:
    d = _domain_of(domain)
    data = await check_typosquat(d)
    di = state.domain(d)
    di.typosquat_target = data.get("target")
    di.typosquat_distance = data.get("distance")
    di.homograph_detected = data.get("homograph", False)
    return {
        "domain": d,
        "impersonates": data.get("target"),
        "edit_distance": data.get("distance"),
        "homograph_attack": data.get("homograph", False),
        "subdomain_impersonation": data.get("subdomain_impersonation", False),
    }


async def _t_landing_page(state: InvestigationState, url: str = "", **_: Any) -> dict:
    analysis = await analyze_html(url)
    if analysis and (state.html is None or analysis.credential_harvest_score > state.html.credential_harvest_score):
        state.html = analysis
    return {
        "credential_harvest_score": analysis.credential_harvest_score,
        "password_field": analysis.has_password_field,
        "otp_field": analysis.has_otp_field,
        "aadhaar_field": analysis.has_aadhaar_field,
        "pan_field": analysis.has_pan_field,
        "card_field": analysis.has_card_field,
        "hidden_iframe": analysis.has_hidden_iframe,
        "page_title": analysis.page_title,
        "note": "harvest_score > 0.5 with a password/OTP/Aadhaar field = phishing",
    }


async def _t_screenshot(state: InvestigationState, url: str = "", **_: Any) -> dict:
    shot = await capture_screenshot(url)
    if shot:
        state.screenshot_b64 = shot
        return {"captured": True, "bytes_b64": len(shot), "note": "screenshot stored for the dashboard"}
    return {"captured": False}


# ── Registry ──────────────────────────────────────────────────────────────────

_IMPLS: Dict[str, Callable] = {
    "lookup_domain_age": _t_domain_age,
    "check_tls_certificate": _t_tls,
    "resolve_redirects": _t_redirects,
    "check_threat_intelligence": _t_threat_intel,
    "check_brand_impersonation": _t_brand,
    "analyze_landing_page": _t_landing_page,
    "screenshot_page": _t_screenshot,
}

# OpenAI tool schemas
TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "lookup_domain_age",
            "description": "RDAP lookup of when a domain was registered + its registrar. Young domains (< 30 days) are the single strongest phishing signal.",
            "parameters": {
                "type": "object",
                "properties": {"domain": {"type": "string", "description": "bare domain, e.g. sbi-kyc-verify.xyz"}},
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_tls_certificate",
            "description": "TLS cert age, issuer and SAN count. A free cert (Let's Encrypt/ZeroSSL) issued in the last few days on a banking-looking domain is suspicious.",
            "parameters": {
                "type": "object",
                "properties": {"domain": {"type": "string"}},
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_redirects",
            "description": "Follow the redirect chain of a URL to its real landing page and detect cloaking (different destination for bots vs mobile).",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_threat_intelligence",
            "description": "Look the URL up in URLhaus, PhishTank, Google Safe Browsing and VirusTotal at once. ANY hit means confirmed malicious — finish the investigation right after.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_brand_impersonation",
            "description": "Check if a domain is a typosquat or IDN-homograph of a known Indian bank / UPI app / govt / brand.",
            "parameters": {
                "type": "object",
                "properties": {"domain": {"type": "string"}},
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_landing_page",
            "description": "Fetch the page HTML and score it for credential harvesting (password / OTP / Aadhaar / PAN / card fields, hidden iframes, obfuscated JS).",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot_page",
            "description": "Render the URL in a sandboxed headless browser and store a screenshot for the dashboard. Use once, on the most suspicious URL, when you are fairly sure it is phishing.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_investigation",
            "description": "End the investigation and return the final verdict. Call this as soon as you have enough evidence — do not run every tool for its own sake.",
            "parameters": {
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": ["scam", "safe", "uncertain"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "threat_type": {
                        "type": "string",
                        "enum": ["phishing", "malware", "social_engineering", "credential_harvesting", "financial_fraud", "unknown"],
                    },
                    "reasoning": {"type": "string", "description": "which signals led to this verdict"},
                },
                "required": ["verdict", "confidence", "reasoning"],
            },
        },
    },
]


async def dispatch(name: str, args: dict, state: InvestigationState) -> tuple[dict, int]:
    """Run a registered tool. Returns (result_dict, duration_ms)."""
    impl = _IMPLS.get(name)
    if impl is None:
        return {"error": f"unknown tool {name}"}, 0
    t0 = time.monotonic()
    try:
        result = await impl(state, **(args or {}))
        state.tools_used.add(name)
        return result, int((time.monotonic() - t0) * 1000)
    except Exception as exc:  # noqa: BLE001 — tools must never crash the loop
        state.tools_failed.add(name)
        logger.warning(f"tool {name} failed: {exc}")
        return {"error": str(exc)[:200]}, int((time.monotonic() - t0) * 1000)
