"""
Kavach Investigator Agent — deterministic fixed pipeline (the final fallback).

The live path is the Strands / hand-rolled agentic loop in investigator_agent.py;
run_investigator() below is just the dispatcher + this deterministic pipeline.

Runs on unknown / low-confidence messages.
Orchestrates 10 link investigation tools and produces a verdict with composite threat scoring.

Tools:
  1. RDAP domain age + registrar
  2. TLS cert age + issuer + SANs
  3. Redirect chain + cloaking detection
  4. URLhaus (URL + host lookup)
  5. PhishTank
  6. Google Safe Browsing
  7. VirusTotal
  8. Typosquat + homograph detection
  9. HTML content analysis (credential harvesting)
  10. Playwright browser screenshot
"""
import asyncio
import json
import logging
import re
import time

from app.config import settings
from app.llm import get_chat_client as get_client, SMART_MODEL
from app.models import (
    InvestigatorResult, DomainIntel, HTMLAnalysis, TriageEntities, ThreatType,
    InvestigatorMode,
)
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

logger = logging.getLogger("kavach.investigator")

# URL extraction regex for finding URLs the triage might have missed
URL_PATTERN = re.compile(r'https?://[^\s<>"\']+|www\.[^\s<>"\']+')


SYSTEM_PROMPT = """You are the Investigator Agent for Kavach, a scam detection system protecting Indian families.

You receive a suspicious message and comprehensive intelligence from 10 security tools.
Your job is to produce a final verdict with confidence and detailed reasoning.

Tool signal weights (most important to least):
1. URLhaus / PhishTank / Google Safe Browsing / VirusTotal hits: CONFIRMED malicious — verdict=scam, confidence≥0.95
2. HTML credential harvesting score > 0.5: STRONG phishing signal — forms asking for password/OTP/Aadhaar/PAN
3. Domain age < 7 days + free TLS cert: VERY STRONG phishing signal
4. Domain age < 30 days: Strong phishing signal
5. Typosquat distance ≤ 2 OR homograph attack detected: VERY STRONG phishing signal
6. Multiple redirect hops (>3) ending at a login page: Strong phishing signal
7. Cloaking detected (different content for mobile vs bot): STRONG evasion signal
8. Free TLS cert (Let's Encrypt) issued < 7 days ago: Moderate signal
9. Risky registrar (Freenom, NameCheap free tiers): Weak signal

Combined signals amplify each other:
- Young domain + credential harvesting + typosquat = near-certain phishing
- Any threat intel hit alone = confirmed malicious

Respond ONLY with valid JSON:
{
  "verdict": "scam|safe|uncertain",
  "confidence": <0.0 to 1.0>,
  "threat_type": "phishing|malware|social_engineering|credential_harvesting|financial_fraud|unknown",
  "reasoning": "<detailed explanation of what signals led to this verdict>"
}"""


async def investigate_url(url: str) -> tuple[DomainIntel, HTMLAnalysis | None, list[str], list[str]]:
    """Run all tool checks on a single URL concurrently. Returns (DomainIntel, HTMLAnalysis, tools_used, tools_failed)."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path.split("/")[0]
    except Exception:
        domain = url

    tools_used = []
    tools_failed = []

    # Run all tools concurrently
    results = await asyncio.gather(
        check_domain_age(domain),        # 0
        check_tls_cert(domain),          # 1
        resolve_redirect_chain(url),     # 2
        check_urlhaus(url),              # 3
        check_phishtank(url),            # 4
        check_typosquat(domain),         # 5
        check_google_safe_browsing(url), # 6
        check_virustotal(url),           # 7
        analyze_html(url),               # 8
        return_exceptions=True,
    )

    tool_names = ["rdap", "tls_cert", "redirect", "urlhaus", "phishtank", "typosquat", "google_safe_browsing", "virustotal", "html_analyzer"]

    # Extract results, tracking successes and failures
    processed = []
    for i, (result, name) in enumerate(zip(results, tool_names)):
        if isinstance(result, Exception):
            tools_failed.append(name)
            processed.append({})
        else:
            tools_used.append(name)
            processed.append(result)

    rdap_data = processed[0]
    tls_data = processed[1]
    redirect_data = processed[2]
    urlhaus_data = processed[3]
    phishtank_data = processed[4]
    typosquat_data = processed[5]
    gsb_data = processed[6]
    vt_data = processed[7]
    html_result = processed[8]

    # Build DomainIntel
    domain_intel = DomainIntel(
        domain=domain,
        age_days=rdap_data.get("age_days") if isinstance(rdap_data, dict) else None,
        registrar=rdap_data.get("registrar") if isinstance(rdap_data, dict) else None,
        tls_age_days=tls_data.get("age_days") if isinstance(tls_data, dict) else None,
        tls_issuer=tls_data.get("issuer") if isinstance(tls_data, dict) else None,
        tls_san_domains=tls_data.get("san_domains", []) if isinstance(tls_data, dict) else [],
        redirect_final_url=redirect_data.get("final_url") if isinstance(redirect_data, dict) else None,
        redirect_hops=redirect_data.get("hops", 0) if isinstance(redirect_data, dict) else 0,
        redirect_chain=redirect_data.get("chain", []) if isinstance(redirect_data, dict) else [],
        urlhaus_hit=urlhaus_data.get("hit", False) if isinstance(urlhaus_data, dict) else False,
        urlhaus_threat=urlhaus_data.get("threat") if isinstance(urlhaus_data, dict) else None,
        phishtank_hit=phishtank_data.get("hit", False) if isinstance(phishtank_data, dict) else False,
        phishtank_verified=phishtank_data.get("verified", False) if isinstance(phishtank_data, dict) else False,
        google_safe_browsing_hit=gsb_data.get("hit", False) if isinstance(gsb_data, dict) else False,
        google_safe_browsing_threats=gsb_data.get("threats", []) if isinstance(gsb_data, dict) else [],
        virustotal_positives=vt_data.get("positives", 0) if isinstance(vt_data, dict) else 0,
        virustotal_total=vt_data.get("total", 0) if isinstance(vt_data, dict) else 0,
        typosquat_target=typosquat_data.get("target") if isinstance(typosquat_data, dict) else None,
        typosquat_distance=typosquat_data.get("distance") if isinstance(typosquat_data, dict) else None,
        homograph_detected=typosquat_data.get("homograph", False) if isinstance(typosquat_data, dict) else False,
    )

    # HTML analysis result
    html_analysis = html_result if isinstance(html_result, HTMLAnalysis) else None

    return domain_intel, html_analysis, tools_used, tools_failed


def _compute_threat_score(
    domain_intel_list: list[DomainIntel],
    html_analysis: HTMLAnalysis | None,
) -> int:
    """
    Compute composite threat score (0-100) from all tool signals.
    This is the deterministic fallback when the LLM is unavailable.
    """
    score = 0

    for d in domain_intel_list:
        # Threat intelligence hits — immediate high score
        if d.urlhaus_hit:
            score += 40
        if d.phishtank_hit:
            score += 35
        if d.google_safe_browsing_hit:
            score += 40
        if d.virustotal_positives > 0:
            score += min(30, d.virustotal_positives * 5)

        # Domain age signals
        age = d.age_days
        if age is not None:
            if age < 7:
                score += 25
            elif age < 30:
                score += 15
            elif age < 90:
                score += 5

        # TLS signals
        tls_age = d.tls_age_days
        if tls_age is not None and tls_age < 7:
            score += 10

        # Typosquat signals
        if d.homograph_detected:
            score += 30
        elif d.typosquat_distance is not None and d.typosquat_distance <= 2:
            score += 20

        # Redirect signals
        if d.redirect_hops > 3:
            score += 10

    # HTML analysis signals
    if html_analysis:
        score += int(html_analysis.credential_harvest_score * 25)

    return min(100, score)


def _determine_threat_type(
    domain_intel_list: list[DomainIntel],
    html_analysis: HTMLAnalysis | None,
) -> ThreatType:
    """Determine the primary threat type from signals."""
    for d in domain_intel_list:
        if d.urlhaus_hit and d.urlhaus_threat == "malware_download":
            return ThreatType.MALWARE
        if d.google_safe_browsing_hit and "MALWARE" in d.google_safe_browsing_threats:
            return ThreatType.MALWARE

    if html_analysis and html_analysis.credential_harvest_score > 0.3:
        return ThreatType.CREDENTIAL_HARVESTING

    for d in domain_intel_list:
        if d.phishtank_hit or d.typosquat_target or d.homograph_detected:
            return ThreatType.PHISHING

    if any(d.google_safe_browsing_hit for d in domain_intel_list):
        return ThreatType.SOCIAL_ENGINEERING

    return ThreatType.UNKNOWN


async def run_investigator(
    raw_text: str,
    entities: TriageEntities,
) -> InvestigatorResult:
    """
    Dispatcher with a fallback ladder:

        strands  →  agentic (hand-rolled loop)  →  fixed (deterministic fan-out)

    AGENT_RUNTIME=strands + INVESTIGATOR_MODE=agentic  → start at 'strands'.
    INVESTIGATOR_MODE=fixed                            → skip straight to 'fixed'.
    Any exception at a rung drops to the next and tags the result 'fallback'.
    """
    mode = (settings.INVESTIGATOR_MODE or "agentic").lower()
    if mode == "fixed":
        return await run_investigator_fixed(raw_text, entities)

    use_strands = (settings.AGENT_RUNTIME or "strands").lower() == "strands"

    if use_strands:
        try:
            from app.strands_runtime import strands_available
            if strands_available():
                from app.agents.investigator_agent import run_investigator_strands
                return await run_investigator_strands(raw_text, entities)
        except Exception as exc:
            logger.error(f"Strands investigator failed ({exc}); falling back to hand-rolled loop")

    try:
        from app.agents.investigator_agent import run_investigator_agentic
        result = await run_investigator_agentic(raw_text, entities)
        if use_strands:
            result.mode = InvestigatorMode.FALLBACK
        return result
    except Exception as exc:
        logger.error(f"Agentic investigator failed ({exc}); falling back to fixed pipeline")
        result = await run_investigator_fixed(raw_text, entities)
        result.mode = InvestigatorMode.FALLBACK
        return result


async def run_investigator_fixed(
    raw_text: str,
    entities: TriageEntities,
) -> InvestigatorResult:
    """
    Deterministic investigation pipeline:
    1. Parallel tool execution on all URLs (10 tools per URL)
    2. HTML content analysis for credential harvesting
    3. Playwright screenshot of the most suspicious URL
    4. Composite threat score computation
    5. GPT-4o synthesis into final verdict
    """
    t0 = time.monotonic()

    # Extract URLs from entities AND from raw text (catch what triage missed)
    urls = list(set(entities.urls or []) | set(URL_PATTERN.findall(raw_text)))

    if not urls:
        return InvestigatorResult(
            verdict="uncertain",
            confidence=0.4,
            domain_intel=[],
            reasoning="No URLs found in message; cannot perform link forensics.",
            investigation_time_ms=int((time.monotonic() - t0) * 1000),
        )

    # Investigate all URLs in parallel (max 3)
    logger.info(f"🔬 Investigating {len(urls)} URL(s) with 10 tools each: {urls[:3]}")
    investigation_results = await asyncio.gather(
        *[investigate_url(url) for url in urls[:3]],
        return_exceptions=True,
    )

    domain_intel_list = []
    html_analysis = None
    all_tools_used = set()
    all_tools_failed = set()

    for result in investigation_results:
        if isinstance(result, Exception):
            logger.error(f"URL investigation failed: {result}")
            continue
        domain_intel, html_result, tools_used, tools_failed = result
        domain_intel_list.append(domain_intel)
        if html_result and (html_analysis is None or html_result.credential_harvest_score > (html_analysis.credential_harvest_score if html_analysis else 0)):
            html_analysis = html_result
        all_tools_used.update(tools_used)
        all_tools_failed.update(tools_failed)

    # Compute composite threat score
    threat_score = _compute_threat_score(domain_intel_list, html_analysis)
    threat_type = _determine_threat_type(domain_intel_list, html_analysis)

    # Screenshot the first (most suspicious) URL
    screenshot_path = None
    if urls:
        try:
            screenshot_path = await capture_screenshot(urls[0])
            if screenshot_path:
                all_tools_used.add("browser_screenshot")
                logger.info(f"📸 Screenshot captured: {len(screenshot_path)} bytes b64")
        except Exception as exc:
            all_tools_failed.add("browser_screenshot")
            logger.warning(f"Screenshot failed: {exc}")

    # Build tool summary for the LLM
    tool_summary = []
    for intel in domain_intel_list:
        summary = {
            "domain": intel.domain,
            "age_days": intel.age_days,
            "registrar": intel.registrar,
            "tls_age_days": intel.tls_age_days,
            "tls_issuer": intel.tls_issuer,
            "tls_san_count": len(intel.tls_san_domains),
            "redirect_final_url": intel.redirect_final_url,
            "redirect_hops": intel.redirect_hops,
            "urlhaus_hit": intel.urlhaus_hit,
            "urlhaus_threat": intel.urlhaus_threat,
            "phishtank_hit": intel.phishtank_hit,
            "phishtank_verified": intel.phishtank_verified,
            "google_safe_browsing_hit": intel.google_safe_browsing_hit,
            "google_safe_browsing_threats": intel.google_safe_browsing_threats,
            "virustotal_positives": intel.virustotal_positives,
            "virustotal_total": intel.virustotal_total,
            "typosquat_target": intel.typosquat_target,
            "typosquat_distance": intel.typosquat_distance,
            "homograph_detected": intel.homograph_detected,
        }
        tool_summary.append(summary)

    # Add HTML analysis
    html_summary = {}
    if html_analysis:
        html_summary = {
            "credential_harvest_score": html_analysis.credential_harvest_score,
            "has_password_field": html_analysis.has_password_field,
            "has_otp_field": html_analysis.has_otp_field,
            "has_aadhaar_field": html_analysis.has_aadhaar_field,
            "has_pan_field": html_analysis.has_pan_field,
            "has_card_field": html_analysis.has_card_field,
            "has_hidden_iframe": html_analysis.has_hidden_iframe,
            "suspicious_js": html_analysis.suspicious_js,
            "form_count": html_analysis.form_count,
            "page_title": html_analysis.page_title,
        }

    # LLM synthesis
    client = get_client()
    user_content = (
        f"Suspicious message:\n{raw_text}\n\n"
        f"Tool investigation results:\n{json.dumps(tool_summary, indent=2)}\n\n"
        f"HTML analysis:\n{json.dumps(html_summary, indent=2)}\n\n"
        f"Composite threat score: {threat_score}/100"
    )

    try:
        response = await client.chat.completions.create(
            model=SMART_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            max_tokens=512,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        verdict = data.get("verdict", "uncertain")
        confidence = float(data.get("confidence", 0.5))
        reasoning = data.get("reasoning", "")
        llm_threat_type = data.get("threat_type", "unknown")
        try:
            threat_type = ThreatType(llm_threat_type)
        except ValueError:
            pass  # Keep the heuristic threat type
    except Exception as exc:
        logger.error(f"Investigator LLM synthesis error: {exc}")
        # Fallback: use composite threat score for deterministic verdict
        if threat_score >= 60:
            verdict = "scam"
            confidence = min(0.95, 0.5 + threat_score / 200)
        elif threat_score >= 30:
            verdict = "uncertain"
            confidence = 0.4 + threat_score / 250
        else:
            verdict = "safe"
            confidence = 0.6
        reasoning = f"Deterministic verdict (LLM unavailable). Threat score: {threat_score}/100"

    investigation_time_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"🔬 Investigation done in {investigation_time_ms}ms | "
        f"verdict={verdict} conf={confidence:.2f} threat_score={threat_score} "
        f"tools_ok={len(all_tools_used)} tools_fail={len(all_tools_failed)}"
    )

    return InvestigatorResult(
        verdict=verdict,
        confidence=confidence,
        threat_score=threat_score,
        threat_type=threat_type,
        domain_intel=domain_intel_list,
        html_analysis=html_analysis,
        screenshot_path=screenshot_path,
        reasoning=reasoning,
        investigation_time_ms=investigation_time_ms,
        tools_used=sorted(all_tools_used),
        tools_failed=sorted(all_tools_failed),
        mode=InvestigatorMode.FIXED,
        llm_calls=1,
    )
