"""
Offline logic tests for the v3 enhancements. No network, no real SDKs.

Run:  python tests/test_enhancements_offline.py
Stubs the heavy third-party SDKs so the pure Kavach logic (policy engine, agentic
investigator control flow, educator gating, reputation keys, models) can be
exercised without installing anything.
"""
import asyncio
import json
import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Env the config module needs ──────────────────────────────────────────────
os.environ.update({
    "LLM_PROVIDER": "groq", "GROQ_API_KEY": "test", "EMBED_PROVIDER": "openai",
    "AZURE_OPENAI_API_KEY": "test", "AZURE_OPENAI_ENDPOINT": "https://t.openai.azure.com/",
    "SUPABASE_URL": "https://t.supabase.co", "SUPABASE_ANON_KEY": "a", "SUPABASE_SERVICE_KEY": "s",
    "TWILIO_ACCOUNT_SID": "AC0", "TWILIO_AUTH_TOKEN": "t",
    "MOTHER_PHONE": "whatsapp:+910000000000", "NATU_PHONE": "whatsapp:+910000000001",
})

# ── Stub third-party SDKs ────────────────────────────────────────────────────
def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m

class _FakeChatCompletions:
    """Returns whatever the current test queued via set_script()."""
    script = []
    idx = 0
    @classmethod
    def reset(cls, script): cls.script, cls.idx = script, 0
    async def create(self, **kwargs):
        resp = _FakeChatCompletions.script[_FakeChatCompletions.idx]
        _FakeChatCompletions.idx += 1
        return resp

class _FakeAsyncAzureOpenAI:
    def __init__(self, *a, **k):
        self.chat = types.SimpleNamespace(completions=_FakeChatCompletions())
        self.embeddings = types.SimpleNamespace(
            create=lambda **kw: asyncio.sleep(0, result=types.SimpleNamespace(
                data=[types.SimpleNamespace(embedding=[0.0] * 1536)]))
        )

_mod("openai", AsyncAzureOpenAI=_FakeAsyncAzureOpenAI, AsyncOpenAI=_FakeAsyncAzureOpenAI)

class _TTLCache(dict):
    def __init__(self, *a, **k): super().__init__()
_mod("cachetools", TTLCache=_TTLCache)

def _retry(*a, **k):
    def deco(fn): return fn
    return deco
_mod("tenacity", retry=_retry, stop_after_attempt=lambda *a, **k: None,
     wait_exponential=lambda *a, **k: None)

_mod("supabase", create_client=lambda *a, **k: None, Client=object)
_mod("httpx", AsyncClient=object, Client=object)
_mod("numpy")
rf = _mod("rapidfuzz", fuzz=types.SimpleNamespace(ratio=lambda *a, **k: 0),
         process=types.SimpleNamespace(extractOne=lambda *a, **k: None))
_mod("rapidfuzz.distance", Levenshtein=types.SimpleNamespace(distance=lambda *a, **k: 9))
tw = _mod("twilio")
_mod("twilio.rest", Client=object)
_mod("bs4", BeautifulSoup=object)

# ── Now import Kavach ────────────────────────────────────────────────────────
from app.models import (  # noqa: E402
    AgentStep, StageTrace, ReputationResult, ComplaintDraft, DoerResult, DoerTaskType,
    InvestigatorResult, InvestigatorMode, AuditEvent, TriageResult, TriageEntities,
    Category, ImmunityCheckResult,
)
from app.policy.engine import evaluate_policy  # noqa: E402

PASS = 0
FAIL = 0

def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")


def _triage(cat, conf=0.9, action=None, urls=None, amount=None):
    return TriageResult(
        category=cat, confidence=conf,
        entities=TriageEntities(urls=urls or [], action_demanded=action, amount=amount),
        reasoning="t",
    )

_NO_IMM = ImmunityCheckResult(matched=False)


async def test_models():
    print("\n[models]")
    check("AgentStep", AgentStep(kind="thought", content="x").kind == "thought")
    check("StageTrace", StageTrace(stage="triage", duration_ms=5).duration_ms == 5)
    check("ReputationResult repeat flag default", ReputationResult(sender_key="phone:9").is_repeat_offender is False)
    cd = ComplaintDraft(victim_name="Ma", message_verbatim="hi")
    check("ComplaintDraft default channel", cd.channel == "chakshu")
    check("ComplaintDraft status", cd.status == "prepared")
    dr = DoerResult(task_type=DoerTaskType.COMPLAINT_PREFILL, complaint=cd)
    check("DoerResult holds complaint", dr.complaint.victim_name == "Ma")
    ir = InvestigatorResult(verdict="scam", confidence=0.9, reasoning="r",
                            mode=InvestigatorMode.AGENTIC,
                            agent_trace=[AgentStep(kind="verdict", content="v")], llm_calls=2)
    check("InvestigatorResult agentic fields", ir.mode == InvestigatorMode.AGENTIC and ir.llm_calls == 2)
    ev = AuditEvent(message_id="m", member_id="x", member_name="X", raw_text="hi",
                    source="sms", received_at=__import__("datetime").datetime.utcnow(),
                    reputation=ReputationResult(sender_key="phone:9"),
                    education_note="be careful", stage_traces=[StageTrace(stage="policy")])
    check("AuditEvent new fields round-trip", json.loads(ev.model_dump_json())["education_note"] == "be careful")


async def test_policy_reputation():
    print("\n[policy — sender reputation]")
    repeat = ReputationResult(sender_key="phone:9", seen_count=5, scam_count=3, is_repeat_offender=True)
    one_prior = ReputationResult(sender_key="phone:9", seen_count=2, scam_count=1)
    clean = ReputationResult(sender_key="phone:9")

    d = await evaluate_policy(_triage(Category.SCAM, 0.8), _NO_IMM, None, repeat)
    check("repeat offender + scam => silent_kill", d.action.value == "silent_kill")
    check("repeat offender rule id", d.rule_matched == "repeat_offender_silent_kill")

    d = await evaluate_policy(_triage(Category.PERSONAL, 0.9), _NO_IMM, None, repeat)
    check("repeat offender + benign => escalate_natu", d.action.value == "escalate_natu")

    d = await evaluate_policy(_triage(Category.SCAM, 0.75), _NO_IMM, None, one_prior)
    check("one prior scam => escalate_natu", d.action.value == "escalate_natu"
          and d.rule_matched == "sender_prior_scam")

    d = await evaluate_policy(_triage(Category.ROUTINE_ADMIN, 0.95, amount=None), _NO_IMM, None, clean)
    check("clean sender + routine => act", d.action.value == "act")

    d = await evaluate_policy(_triage(Category.SCAM, 0.9, action="share_otp", amount=1),
                              _NO_IMM, None, clean)
    check("otp demand still escalates urgently without reputation", d.action.value == "escalate_natu_urgent")

    check("signals populated", isinstance(d.signals, list) and len(d.signals) > 0)


async def test_policy_regression():
    print("\n[policy — no-reputation regression]")
    # reputation omitted entirely — must behave like before
    d = await evaluate_policy(_triage(Category.SCAM, 0.85, urls=["http://x.co"]), _NO_IMM, None)
    check("scam+url no reputation => escalate_natu", d.action.value == "escalate_natu")
    imm = ImmunityCheckResult(matched=True, similarity_score=0.9, match_vector="canonical")
    d = await evaluate_policy(_triage(Category.UNKNOWN, 0.5), imm, None)
    check("immunity match => silent_kill", d.action.value == "silent_kill")
    check("risk score >= 70 on immunity", d.risk_score >= 70)


def _msg(role="assistant", content="", tool_calls=None):
    tc_objs = None
    if tool_calls:
        tc_objs = [types.SimpleNamespace(
            id=f"c{i}", function=types.SimpleNamespace(name=n, arguments=json.dumps(a)))
            for i, (n, a) in enumerate(tool_calls)]
    return types.SimpleNamespace(choices=[types.SimpleNamespace(
        message=types.SimpleNamespace(content=content, tool_calls=tc_objs))])


async def test_agentic_loop():
    print("\n[agentic investigator — control flow]")
    import app.agents.investigator_agent as ia
    from app.agents.investigator_agent import run_investigator_agentic

    calls = []
    async def fake_dispatch(name, args, state):
        calls.append(name)
        if name == "check_threat_intelligence":
            di = state.domain("bad.xyz")
            di.phishtank_hit = True
            state.tools_used.add(name)
            return {"known_malicious": True, "phishtank_hit": True}, 12
        return {"ok": True}, 5
    ia.dispatch = fake_dispatch

    # Round 1: model calls threat intel. Round 2: model finishes.
    _FakeChatCompletions.reset([
        _msg(content="Let me check threat intel.",
             tool_calls=[("check_threat_intelligence", {"url": "http://bad.xyz/kyc"})]),
        _msg(content="Confirmed.", tool_calls=[
            ("finish_investigation", {"verdict": "scam", "confidence": 0.97,
                                      "threat_type": "phishing", "reasoning": "PhishTank hit"})]),
    ])

    res = await run_investigator_agentic(
        "Your KYC expired http://bad.xyz/kyc", TriageEntities(urls=["http://bad.xyz/kyc"]))
    check("verdict = scam", res.verdict == "scam")
    check("confidence carried through", res.confidence == 0.97)
    check("mode = agentic", res.mode == InvestigatorMode.AGENTIC)
    check("llm_calls = 2", res.llm_calls == 2)
    check("only ran the tool it needed", calls == ["check_threat_intelligence"])
    kinds = [s.kind for s in res.agent_trace]
    check("trace has thought/tool_call/observation/verdict",
          {"thought", "tool_call", "observation", "verdict"}.issubset(set(kinds)))
    check("domain_intel populated from state", any(d.phishtank_hit for d in res.domain_intel))

    # No URLs => short-circuit, no LLM
    _FakeChatCompletions.reset([])
    res2 = await run_investigator_agentic("call me back", TriageEntities())
    check("no-url => uncertain, no llm calls", res2.verdict == "uncertain" and res2.llm_calls == 0)


async def test_educator_gate():
    print("\n[educator — gating]")
    import app.agents.educator as edu
    _FakeChatCompletions.reset([_msg(content="This was a fake KYC message. Real banks never ask you to click a link to keep your account open.")])
    from app.models import PolicyDecision, PolicyAction
    note = await edu.write_education_note(
        "KYC expired click here", _triage(Category.SCAM, 0.9, action="click_link"),
        None, PolicyDecision(action=PolicyAction.SILENT_KILL, reason="r", rule_matched="x"))
    check("educates on scam", note and "KYC" in note)

    _FakeChatCompletions.reset([_msg(content="SHOULD NOT BE CALLED")])
    note2 = await edu.write_education_note(
        "Your OTP is 123456", _triage(Category.ROUTINE_ADMIN, 0.98),
        None, PolicyDecision(action=PolicyAction.ACT, reason="r", rule_matched="x"))
    check("skips benign routine_admin", note2 is None)


async def main():
    await test_models()
    await test_policy_reputation()
    await test_policy_regression()
    await test_agentic_loop()
    await test_educator_gate()
    print(f"\n{'='*50}\n{PASS} passed, {FAIL} failed\n{'='*50}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
