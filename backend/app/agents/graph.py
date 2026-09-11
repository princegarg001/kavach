"""
Kavach Agent Graph — orchestrates the full pipeline.

Flow:
  InboundMessage
      │
      ├──[parallel]──▶ ① Triage    ② Immunity check    ③ Sender reputation
      │
      ├── if immunity match → Policy → (educate) → DONE
      │
      ├── if scam/unknown/low-confidence → ④ Investigator (Strands agent, tool ReAct loop)
      │
      ├── if routine_admin/personal → ⑤ Doer
      │
      ├──▶ Policy Engine (deterministic) → Action
      │
      ├── if action prepares a complaint → ⑤ Doer.prepare_complaint (held, not filed)
      │
      └──▶ ⑥ Educator note + reputation feedback + stage traces
"""
import asyncio
import logging
import time
from typing import Any, Dict

from app.config import settings
from app.models import (
    Category, InboundMessage, PolicyAction, StageTrace,
)
from app.agents.triage import run_triage
from app.agents.investigator import run_investigator
from app.agents.doer import run_doer, prepare_complaint
from app.agents.educator import write_education_note
from app.memory.immunity_ledger import check_immunity, write_immunity
from app.memory.reputation import check_reputation, record_sighting, derive_sender_key
from app.policy.engine import evaluate_policy
from app.notifier.whatsapp import send_escalation

logger = logging.getLogger("kavach.graph")

_COMPLAINT_ACTIONS = {PolicyAction.ESCALATE_NATU_URGENT}


async def _maybe_educate(traces: list, result: dict, msg: InboundMessage) -> None:
    """Attach a plain-language education note (best-effort, time-boxed)."""
    triage = result.get("triage")
    policy = result.get("policy")
    if not triage or not policy:
        return
    t = time.monotonic()
    try:
        note = await asyncio.wait_for(
            write_education_note(msg.raw_text, triage, result.get("investigation"), policy),
            timeout=8.0,
        )
        if note:
            result["education_note"] = note
    except Exception as exc:  # noqa: BLE001 — educator must never break the pipeline
        logger.debug(f"educator skipped: {exc}")
    traces.append(StageTrace(stage="educator", duration_ms=int((time.monotonic() - t) * 1000)))


async def run_kavach_pipeline(msg: InboundMessage) -> Dict[str, Any]:
    """
    Execute the full multi-agent Kavach pipeline.
    Returns a dict with keys:
      triage, immunity, reputation, investigation, doer, policy, education_note, stage_traces
    """
    t_start = time.monotonic()
    result: Dict[str, Any] = {}
    traces: list[StageTrace] = []

    # ── Stage 1: Triage + Immunity + Reputation (parallel) ─────────────────────
    logger.info("🚀 Stage 1: Triage + Immunity + Reputation (parallel)")
    t1 = time.monotonic()

    # We need entities for the reputation key, but reputation only needs a coarse
    # sender key — derive it from a cheap pre-parse rather than waiting on triage.
    from app.agents.triage import _pre_classify
    from app.models import TriageEntities
    pre = _pre_classify(msg.raw_text)
    prelim_entities = TriageEntities(
        sender_id=None,
        urls=pre["extracted_urls"],
        phone_numbers=pre["extracted_phones"],
        upi_ids=pre["extracted_upi_ids"],
    )
    sender_key = derive_sender_key(prelim_entities, msg.member_id)

    triage_result, immunity_result, reputation_result = await asyncio.gather(
        run_triage(msg.raw_text),
        check_immunity(msg.raw_text, group_id=settings.GROUP_ID),
        check_reputation(sender_key, group_id=settings.GROUP_ID),
        return_exceptions=True,
    )

    if isinstance(triage_result, Exception):
        logger.error(f"Triage failed: {triage_result}")
        from app.models import TriageResult
        triage_result = TriageResult(
            category=Category.UNKNOWN, entities=TriageEntities(),
            confidence=0.0, reasoning="Triage error",
        )
    if isinstance(immunity_result, Exception):
        logger.error(f"Immunity check failed: {immunity_result}")
        from app.models import ImmunityCheckResult
        immunity_result = ImmunityCheckResult(matched=False, similarity_score=0.0, check_time_ms=0)
    if isinstance(reputation_result, Exception):
        logger.error(f"Reputation check failed: {reputation_result}")
        from app.models import ReputationResult
        reputation_result = ReputationResult(sender_key=sender_key)

    # Refine the sender key now that triage may have found a DLT sender id.
    if triage_result.entities.sender_id:
        refined = derive_sender_key(triage_result.entities, msg.member_id)
        if refined != sender_key and refined != "unknown":
            reputation_result = await check_reputation(refined, group_id=settings.GROUP_ID)
            sender_key = refined

    result["triage"] = triage_result
    result["immunity"] = immunity_result
    result["reputation"] = reputation_result
    traces.append(StageTrace(
        stage="stage1_parallel", duration_ms=int((time.monotonic() - t1) * 1000),
        detail=f"category={triage_result.category.value} immunity={immunity_result.matched} "
               f"repeat_offender={reputation_result.is_repeat_offender}",
    ))
    logger.info(
        f"⚡ Stage 1 done | category={triage_result.category.value} "
        f"immunity_match={immunity_result.matched} ({immunity_result.similarity_score:.3f}) "
        f"sender={sender_key} scams={reputation_result.scam_count}"
    )

    # ── Stage 2: Immunity short-circuit ───────────────────────────────────────
    if immunity_result.matched and immunity_result.similarity_score >= 0.80:
        logger.info(f"🛡️  IMMUNITY HIT in {immunity_result.check_time_ms}ms — skipping investigator")
        tp = time.monotonic()
        policy = await evaluate_policy(
            triage=triage_result, immunity=immunity_result,
            investigation=None, reputation=reputation_result,
        )
        result["policy"] = policy
        traces.append(StageTrace(stage="policy", duration_ms=int((time.monotonic() - tp) * 1000)))
        await _maybe_educate(traces, result, msg)
        asyncio.create_task(record_sighting(sender_key, settings.GROUP_ID, "scam"))
        result["stage_traces"] = traces
        return result

    # ── Stage 3: Investigator (scam / unknown / low confidence) ───────────────
    needs_investigation = (
        triage_result.category in (Category.SCAM, Category.UNKNOWN)
        or triage_result.confidence < 0.75
    )
    investigation_result = None
    if needs_investigation:
        _rt = "strands→agentic→fixed" if (settings.AGENT_RUNTIME or "strands").lower() == "strands" else settings.INVESTIGATOR_MODE
        logger.info(f"🔬 Stage 3: Investigator ({_rt})")
        ti = time.monotonic()
        investigation_result = await run_investigator(
            raw_text=msg.raw_text, entities=triage_result.entities,
        )
        result["investigation"] = investigation_result
        traces.append(StageTrace(
            stage="investigator", duration_ms=int((time.monotonic() - ti) * 1000),
            detail=f"mode={investigation_result.mode.value} verdict={investigation_result.verdict} "
                   f"llm_calls={investigation_result.llm_calls} tools={len(investigation_result.tools_used)}",
        ))

    # ── Stage 4: Doer (routine admin / personal) ──────────────────────────────
    if triage_result.category in (Category.ROUTINE_ADMIN, Category.PERSONAL):
        logger.info("🤖 Stage 4: Doer Agent")
        td = time.monotonic()
        doer_result = await run_doer(msg.raw_text, triage_result)
        if doer_result:
            result["doer"] = doer_result
        traces.append(StageTrace(stage="doer", duration_ms=int((time.monotonic() - td) * 1000)))

    # ── Stage 5: Policy engine ────────────────────────────────────────────────
    logger.info("⚖️  Stage 5: Policy Engine")
    tp = time.monotonic()
    policy = await evaluate_policy(
        triage=triage_result, immunity=immunity_result,
        investigation=investigation_result, reputation=reputation_result,
    )
    result["policy"] = policy
    traces.append(StageTrace(
        stage="policy", duration_ms=int((time.monotonic() - tp) * 1000),
        detail=f"action={policy.action.value} rule={policy.rule_matched} risk={policy.risk_score}",
    ))

    # ── Stage 5a: Write to the immunity ledger on a confirmed scam ────────────
    _scam_confirmed = (
        (investigation_result and investigation_result.verdict == "scam")
        or (triage_result.category == Category.SCAM and triage_result.confidence >= 0.85
            and policy.action in (PolicyAction.SILENT_KILL, PolicyAction.ESCALATE_NATU,
                                  PolicyAction.ESCALATE_NATU_URGENT))
    )
    if _scam_confirmed and not immunity_result.matched:
        logger.info("✍️  Writing scam signature to the immunity ledger")
        asyncio.create_task(write_immunity(
            raw_text=msg.raw_text, group_id=settings.GROUP_ID,
            member_id=msg.member_id, entities=triage_result.entities,
            investigation=investigation_result,
        ))

    # ── Stage 5b: Prepare complaint (held for one-tap approval, never filed) ───
    if policy.chakshu_prefilled or policy.action in _COMPLAINT_ACTIONS:
        logger.info("📋 Stage 5b: Prefilling Chakshu / 1930 complaint")
        tc = time.monotonic()
        try:
            complaint_result = await prepare_complaint(
                raw_text=msg.raw_text,
                entities=triage_result.entities,
                investigation=investigation_result,
                victim_name=msg.member_name,
                victim_phone=msg.member_id.replace("whatsapp:", ""),
                money_lost=False,
            )
            result["doer"] = complaint_result  # mutually exclusive with routine doer here
        except Exception as exc:
            logger.warning(f"complaint prefill failed: {exc}")
        traces.append(StageTrace(stage="complaint_prefill", duration_ms=int((time.monotonic() - tc) * 1000)))

    # ── Stage 6: Execute policy action ───────────────────────────────────────
    if policy.escalation_target:
        asyncio.create_task(send_escalation(
            msg=msg, policy=policy, triage=triage_result, investigation=investigation_result,
        ))

    # ── Stage 7: Educator + reputation feedback ──────────────────────────────
    await _maybe_educate(traces, result, msg)

    verdict_for_reputation = (
        "scam" if (triage_result.category == Category.SCAM
                   or (investigation_result and investigation_result.verdict == "scam")
                   or policy.action == PolicyAction.SILENT_KILL)
        else "safe" if triage_result.category in (Category.ROUTINE_ADMIN, Category.PERSONAL)
        else "unknown"
    )
    asyncio.create_task(record_sighting(sender_key, settings.GROUP_ID, verdict_for_reputation))

    result["stage_traces"] = traces
    total_ms = int((time.monotonic() - t_start) * 1000)
    logger.info(
        f"✅ Pipeline complete in {total_ms}ms | action={policy.action.value} "
        f"rule={policy.rule_matched} | educated={'education_note' in result}"
    )
    return result
