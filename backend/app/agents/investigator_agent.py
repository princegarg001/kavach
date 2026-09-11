"""
Agentic Investigator.

Two implementations, same contract:
  - run_investigator_strands : a strands.Agent (Strands Agents SDK) with 7 @tool
    forensic tools. This is the default.
  - run_investigator_agentic : a hand-rolled OpenAI-compatible function-calling
    ReAct loop. Fallback when the Strands SDK isn't usable.

Both decide which tools to run from what they've learned and stop early. Every
thought / tool call / observation is captured as an AgentStep for the dashboard.
The deterministic fixed pipeline (investigator.py) is the final fallback.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextvars import ContextVar

from app.config import settings
from app.llm import get_chat_client as get_client, SMART_MODEL
from app.models import (
    AgentStep,
    InvestigatorMode,
    InvestigatorResult,
    ThreatType,
    TriageEntities,
)
from app.agents.tools.registry import TOOL_SCHEMAS, InvestigationState, dispatch

logger = logging.getLogger("kavach.investigator.agent")

# tool-name -> last wall-clock ms, per investigation (for the trace)
_tool_durations: ContextVar[dict] = ContextVar("_tool_durations", default={})

_VERDICT_RE = re.compile(r"\{[^{}]*\"verdict\"[^{}]*\}", re.S)

URL_PATTERN = re.compile(r'https?://[^\s<>"\']+|www\.[^\s<>"\']+')


SYSTEM_PROMPT = """You are the Investigator Agent for Kavach, a scam-protection system for Indian families.

You are given a suspicious message and a list of URLs it contains. You have forensic tools.
Your job: reach a verdict (scam / safe / uncertain) with the FEWEST tool calls needed.

Strategy:
1. Start with `check_threat_intelligence` on the primary URL. If it comes back known_malicious, call `finish_investigation` immediately with confidence >= 0.95.
2. Otherwise check `lookup_domain_age` and `check_brand_impersonation` on the domain. A domain < 30 days old OR impersonating a bank/UPI/govt brand is a very strong signal.
3. If still unclear, use `analyze_landing_page` to look for credential-harvesting forms, and `resolve_redirects` / `check_tls_certificate` as needed.
4. Call `screenshot_page` at most once, only when you already believe it is phishing (it feeds the dashboard).
5. Call `finish_investigation` as soon as the picture is clear. Do not run tools you do not need.

Signal weights (strong -> weak): threat-intel hit > credential-harvesting form > domain age < 7d + free cert > typosquat/homograph > many redirects to a login page > cloaking > free cert alone.
Combined weak signals (young domain + harvest form + typosquat) = near-certain phishing.

Be decisive. A confident verdict in 2 tool calls is better than an exhaustive one in 8."""


def _running_threat_score(state: InvestigationState) -> int:
    """Deterministic score from whatever intel has accumulated so far."""
    from app.agents.investigator import _compute_threat_score
    return _compute_threat_score(list(state.domains.values()), state.html)


async def run_investigator_agentic(
    raw_text: str,
    entities: TriageEntities,
) -> InvestigatorResult:
    t0 = time.monotonic()
    urls = list(dict.fromkeys((entities.urls or []) + URL_PATTERN.findall(raw_text)))

    if not urls:
        return InvestigatorResult(
            verdict="uncertain",
            confidence=0.4,
            domain_intel=[],
            reasoning="No URLs in message; link forensics not applicable.",
            investigation_time_ms=int((time.monotonic() - t0) * 1000),
            mode=InvestigatorMode.AGENTIC,
        )

    state = InvestigationState()
    trace: list[AgentStep] = []
    client = get_client()

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Suspicious message:\n{raw_text}\n\n"
                f"URLs found: {json.dumps(urls[:5])}\n"
                f"Primary URL to investigate: {urls[0]}"
            ),
        },
    ]

    verdict = "uncertain"
    confidence = 0.5
    reasoning = ""
    threat_type = ThreatType.UNKNOWN
    llm_calls = 0
    finished = False

    for step in range(settings.INVESTIGATOR_MAX_STEPS):
        force_finish = step == settings.INVESTIGATOR_MAX_STEPS - 1
        # "auto" is supported on every Azure OpenAI API version; the system prompt
        # already pushes hard toward tool use, and the loop handles a prose reply.
        tool_choice = (
            {"type": "function", "function": {"name": "finish_investigation"}}
            if force_finish else "auto"
        )
        try:
            resp = await client.chat.completions.create(
                model=SMART_MODEL,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice=tool_choice,
                temperature=0.1,
                max_tokens=700,
            )
        except Exception as exc:
            logger.error(f"agentic LLM call failed on step {step}: {exc}")
            raise  # caller falls back to the fixed pipeline

        llm_calls += 1
        choice = resp.choices[0].message
        tool_calls = choice.tool_calls or []

        if choice.content:
            trace.append(AgentStep(kind="thought", content=choice.content.strip()[:600]))

        if not tool_calls:
            # Model replied in prose without calling a tool. Nudge it once to use
            # finish_investigation; only give up if it refuses again.
            if messages[-1].get("role") == "user" and "[system] Call finish_investigation" in str(messages[-1].get("content", "")):
                reasoning = (choice.content or "No structured verdict returned.").strip()[:800]
                break
            messages.append({"role": "assistant", "content": choice.content or ""})
            messages.append({
                "role": "user",
                "content": "[system] Call finish_investigation with your verdict now, or call a tool if you need more evidence.",
            })
            continue

        messages.append({
            "role": "assistant",
            "content": choice.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls
            ],
        })

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            # gpt-oss models emit a synthetic `json` tool call for their final
            # structured answer — treat it like finish_investigation.
            if name in ("finish_investigation", "json"):
                verdict = args.get("verdict", "uncertain")
                confidence = float(args.get("confidence", 0.5))
                reasoning = args.get("reasoning", "")
                try:
                    threat_type = ThreatType(args.get("threat_type", "unknown"))
                except ValueError:
                    threat_type = ThreatType.UNKNOWN
                trace.append(AgentStep(
                    kind="verdict",
                    content=f"{verdict.upper()} ({confidence:.0%}) — {reasoning}",
                ))
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": json.dumps({"acknowledged": True})})
                finished = True
                continue

            trace.append(AgentStep(kind="tool_call", tool_name=name, tool_args=args))
            result, dur = await dispatch(name, args, state)
            trace.append(AgentStep(
                kind="observation", tool_name=name,
                content=json.dumps(result, default=str)[:600], duration_ms=dur,
            ))
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, default=str)})

        if finished:
            break

        # Early-stop nudge: strong deterministic signal already present
        score = _running_threat_score(state)
        if score >= settings.INVESTIGATOR_EARLY_STOP_SCORE:
            messages.append({
                "role": "user",
                "content": (
                    f"[system] Accumulated threat score is {score}/100 — a strong signal is "
                    f"already present. Call finish_investigation now unless a tool result "
                    f"actively contradicts it."
                ),
            })

    # ── Reconcile with the deterministic score ────────────────────────────────
    from app.agents.investigator import _compute_threat_score, _determine_threat_type
    domain_intel_list = list(state.domains.values())
    threat_score = _compute_threat_score(domain_intel_list, state.html)
    if threat_type == ThreatType.UNKNOWN:
        threat_type = _determine_threat_type(domain_intel_list, state.html)

    if not finished:
        # loop exhausted without an explicit verdict — derive one
        if threat_score >= 60:
            verdict, confidence = "scam", min(0.95, 0.55 + threat_score / 200)
        elif threat_score >= 30:
            verdict, confidence = "uncertain", 0.45 + threat_score / 250
        else:
            verdict, confidence = "safe", 0.6
        reasoning = reasoning or f"Derived from accumulated intel (threat score {threat_score}/100)."
        trace.append(AgentStep(kind="verdict", content=f"{verdict.upper()} ({confidence:.0%}) — {reasoning}"))

    elapsed = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"🧠 Agentic investigation: verdict={verdict} conf={confidence:.2f} "
        f"score={threat_score} llm_calls={llm_calls} tools={sorted(state.tools_used)} in {elapsed}ms"
    )

    return InvestigatorResult(
        verdict=verdict,
        confidence=confidence,
        threat_score=threat_score,
        threat_type=threat_type,
        domain_intel=domain_intel_list,
        html_analysis=state.html,
        screenshot_path=state.screenshot_b64,
        reasoning=reasoning,
        investigation_time_ms=elapsed,
        tools_used=sorted(state.tools_used),
        tools_failed=sorted(state.tools_failed),
        mode=InvestigatorMode.AGENTIC,
        agent_trace=trace,
        llm_calls=llm_calls,
    )


# ════════════════════════════════════════════════════════════════════════════
#  Strands Agents SDK implementation
# ════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT_STRANDS = SYSTEM_PROMPT + (
    "\n\nWhen you have enough evidence, call `submit_verdict` with "
    "verdict (scam|safe|uncertain), confidence (0-1), a one-line reasoning, and "
    "threat_type. Do not keep calling forensic tools after that."
)

_investigator_tools = None


def _build_investigator_tools():
    """Lazily build the 7 @tool wrappers (needs `strands` importable)."""
    global _investigator_tools
    if _investigator_tools is not None:
        return _investigator_tools

    from strands import tool
    from app.strands_runtime import current_investigation

    async def _run(tool_name: str, args: dict) -> str:
        state = current_investigation.get()
        if state is None:
            return json.dumps({"error": "no investigation context"})
        result, dur = await dispatch(tool_name, args, state)
        d = _tool_durations.get()
        d[tool_name] = d.get(tool_name, 0) + dur
        return json.dumps(result, default=str)

    @tool
    async def check_threat_intelligence(url: str) -> str:
        """Look the URL up in URLhaus, PhishTank, Google Safe Browsing and VirusTotal at once. ANY hit = confirmed malicious; stop and give your verdict."""
        return await _run("check_threat_intelligence", {"url": url})

    @tool
    async def lookup_domain_age(domain: str) -> str:
        """RDAP: when the domain was registered and its registrar. A domain younger than 30 days is the strongest phishing signal."""
        return await _run("lookup_domain_age", {"domain": domain})

    @tool
    async def check_brand_impersonation(domain: str) -> str:
        """Check whether the domain is a typosquat or IDN-homograph of a known Indian bank / UPI app / government / brand."""
        return await _run("check_brand_impersonation", {"domain": domain})

    @tool
    async def analyze_landing_page(url: str) -> str:
        """Fetch the page HTML and score it for credential harvesting (password / OTP / Aadhaar / PAN / card fields, hidden iframes, obfuscated JS)."""
        return await _run("analyze_landing_page", {"url": url})

    @tool
    async def resolve_redirects(url: str) -> str:
        """Follow the URL's redirect chain to its real landing page and detect cloaking (different destination for bots vs mobile)."""
        return await _run("resolve_redirects", {"url": url})

    @tool
    async def check_tls_certificate(domain: str) -> str:
        """TLS certificate age, issuer and SAN count. A free cert issued in the last few days on a banking-looking domain is suspicious."""
        return await _run("check_tls_certificate", {"domain": domain})

    @tool
    async def screenshot_page(url: str) -> str:
        """Render the URL in a sandboxed headless browser and store a screenshot for the dashboard. Use once, on the most suspicious URL, when you already believe it is phishing."""
        return await _run("screenshot_page", {"url": url})

    @tool
    async def submit_verdict(verdict: str, confidence: float, reasoning: str,
                             threat_type: str = "unknown") -> str:
        """Record your FINAL verdict and end the investigation. verdict: scam|safe|uncertain."""
        state = current_investigation.get()
        if state is not None:
            state.verdict = {"verdict": verdict, "confidence": confidence,
                             "reasoning": reasoning, "threat_type": threat_type}
        return "verdict recorded"

    # gpt-oss models emit their structured answer as a synthetic tool named `json`.
    @tool(name="json")
    async def _json_verdict(verdict: str = "uncertain", confidence: float = 0.5,
                            reasoning: str = "", threat_type: str = "unknown") -> str:
        """Return your final structured verdict."""
        state = current_investigation.get()
        if state is not None:
            state.verdict = {"verdict": verdict, "confidence": confidence,
                             "reasoning": reasoning, "threat_type": threat_type}
        return "verdict recorded"

    _investigator_tools = [
        check_threat_intelligence, lookup_domain_age, check_brand_impersonation,
        analyze_landing_page, resolve_redirects, check_tls_certificate, screenshot_page,
        submit_verdict, _json_verdict,
    ]
    return _investigator_tools


def _parse_verdict(text: str) -> dict | None:
    m = _VERDICT_RE.search(text or "")
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


async def run_investigator_strands(
    raw_text: str,
    entities: TriageEntities,
) -> InvestigatorResult:
    """Investigator as a strands.Agent with 7 forensic tools."""
    from app import strands_runtime as sr
    from app.agents.investigator import _compute_threat_score, _determine_threat_type

    t0 = time.monotonic()
    urls = list(dict.fromkeys((entities.urls or []) + URL_PATTERN.findall(raw_text)))
    if not urls:
        return InvestigatorResult(
            verdict="uncertain", confidence=0.4, domain_intel=[],
            reasoning="No URLs in message; link forensics not applicable.",
            investigation_time_ms=int((time.monotonic() - t0) * 1000),
            mode=InvestigatorMode.STRANDS,
        )

    state = InvestigationState()
    tok_state = sr.current_investigation.set(state)
    tok_dur = _tool_durations.set({})
    try:
        agent = sr.build_agent(
            SYSTEM_PROMPT_STRANDS, tools=_build_investigator_tools(),
            kind="smart", max_tokens=900,
        )
        prompt = (
            f"Suspicious message:\n{raw_text}\n\n"
            f"URLs: {json.dumps(urls[:5])}\nPrimary URL: {urls[0]}\n"
            f"Investigate with the fewest tool calls, then give your JSON verdict."
        )
        final_text = await sr.run_agent(agent, prompt)
        trace = sr.extract_trace(agent, _tool_durations.get())
        llm_calls = sr.count_llm_calls(agent)
    finally:
        sr.current_investigation.reset(tok_state)
        _tool_durations.reset(tok_dur)

    domain_intel_list = list(state.domains.values())
    threat_score = _compute_threat_score(domain_intel_list, state.html)
    threat_type = _determine_threat_type(domain_intel_list, state.html)

    v = state.verdict or _parse_verdict(final_text)
    if v:
        verdict = v.get("verdict", "uncertain")
        confidence = float(v.get("confidence", 0.5))
        reasoning = v.get("reasoning", final_text[:400])
        try:
            threat_type = ThreatType(v.get("threat_type", threat_type.value))
        except ValueError:
            pass
    else:
        if threat_score >= 60:
            verdict, confidence = "scam", min(0.95, 0.55 + threat_score / 200)
        elif threat_score >= 30:
            verdict, confidence = "uncertain", 0.45 + threat_score / 250
        else:
            verdict, confidence = "safe", 0.6
        reasoning = (final_text or "").strip()[:400] or f"Derived from accumulated intel ({threat_score}/100)."
    trace.append(AgentStep(kind="verdict", content=f"{verdict.upper()} ({confidence:.0%}) — {reasoning}"))

    elapsed = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"🪢 Strands investigation: verdict={verdict} conf={confidence:.2f} score={threat_score} "
        f"llm_calls={llm_calls} tools={sorted(state.tools_used)} in {elapsed}ms"
    )
    return InvestigatorResult(
        verdict=verdict, confidence=confidence, threat_score=threat_score, threat_type=threat_type,
        domain_intel=domain_intel_list, html_analysis=state.html,
        screenshot_path=state.screenshot_b64, reasoning=reasoning,
        investigation_time_ms=elapsed,
        tools_used=sorted(state.tools_used), tools_failed=sorted(state.tools_failed),
        mode=InvestigatorMode.STRANDS, agent_trace=trace, llm_calls=llm_calls,
    )
