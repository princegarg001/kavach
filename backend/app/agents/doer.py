"""
Kavach Doer Agent — performs routine admin tasks end-to-end (not just flagging).

Routine task types (run on category == routine_admin / personal):
1. bill_due_date       — extract amount, biller, due date; set a reminder
2. bank_alert_rewrite  — cryptic bank SMS -> plain language
3. otp_explainer       — "this is a login code for X, never share it"
4. appointment_reminder — extract title, datetime, location; set a reminder

Safety task (run on confirmed fraud, held for one-tap human approval):
5. complaint_prefill   — draft a Chakshu / 1930 cyber-fraud complaint. Never auto-submitted.
"""
import json
import logging
import re
from datetime import datetime, timezone

from app.config import settings
from app.llm import get_chat_client as get_client, FAST_MODEL
from app.models import (
    ComplaintDraft, DoerResult, DoerTaskType, TriageResult, Category, TriageEntities,
    InvestigatorResult,
)

logger = logging.getLogger("kavach.doer")


BILL_PROMPT = """Extract bill due date information from this message.
Return ONLY valid JSON:
{
  "biller": "<company name>",
  "amount": <number or null>,
  "due_date": "<YYYY-MM-DD or human readable date>",
  "summary": "<one friendly line, e.g. 'Airtel bill of Rs599 is due 15 Sep'>"
}"""

BANK_REWRITE_PROMPT = """Rewrite this bank/payment SMS in simple plain English a non-technical person understands.
Be friendly and concise. Use the Rs symbol. State the exact amount, what it was for if known, and timing.
Return ONLY valid JSON: {"plain_text": "<clear plain version>", "summary": "<same, one line>"}"""

OTP_PROMPT = """A family member received a one-time password (OTP) / verification code message.
Explain in one warm sentence what service it is for and remind them to never share it with anyone, including bank staff.
Return ONLY valid JSON: {"plain_text": "<the explanation>", "summary": "<one line>"}"""

APPOINTMENT_PROMPT = """Extract appointment/meeting details from this message.
Return ONLY valid JSON:
{
  "appointment_title": "<what it is>",
  "appointment_datetime": "<YYYY-MM-DD HH:MM or human readable>",
  "appointment_location": "<place or null>",
  "summary": "<one friendly line>"
}"""

COMPLAINT_PROMPT = """You are drafting a cyber-fraud complaint narrative for India's Chakshu / 1930 portal.
Write a clear first-person paragraph (4-6 sentences) describing: what message was received, on what date, what the sender wanted, which number/link was used, and that no money was lost / money was lost (use the fact given).
Plain factual English. Return ONLY valid JSON: {"narrative": "<the paragraph>"}"""


BANK_KEYWORDS = ("debited", "credited", "upi", "a/c xx", "account ending", "neft", "imps", "txn", "avl bal")
OTP_RE = re.compile(r"\b(?:otp|one[- ]time password|verification code|security code)\b", re.I)
APPT_KEYWORDS = ("appointment", "meeting", "scheduled for", "confirmed for", "visit us", "your slot", "consultation", "reschedule")


def _is_bill_message(triage: TriageResult) -> bool:
    e = triage.entities
    return e.biller is not None and e.amount is not None and not e.urls and e.action_demanded is None


def _is_bank_alert(raw_text: str) -> bool:
    t = raw_text.lower()
    return any(k in t for k in BANK_KEYWORDS)


def _is_otp(raw_text: str) -> bool:
    return bool(OTP_RE.search(raw_text)) and bool(re.search(r"\b\d{4,8}\b", raw_text))


def _is_appointment(raw_text: str) -> bool:
    t = raw_text.lower()
    return any(k in t for k in APPT_KEYWORDS)


_JSON_RE = re.compile(r"\{.*\}", re.S)


async def _llm_json(system: str, user: str, max_tokens: int = 256) -> dict:
    """Run a Doer task as a strands.Agent when enabled, else a direct JSON call."""
    if (settings.AGENT_RUNTIME or "strands").lower() == "strands":
        try:
            from app.strands_runtime import strands_available, build_agent, run_agent
            if strands_available():
                agent = build_agent(system, kind="fast", max_tokens=max_tokens)
                text = await run_agent(agent, user)
                m = _JSON_RE.search(text or "")
                if m:
                    return json.loads(m.group(0))
                logger.warning("strands doer returned no JSON; using direct call")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"strands doer failed ({exc}); using direct call")

    resp = await get_client().chat.completions.create(
        model=FAST_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=max_tokens,
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


async def run_doer(raw_text: str, triage: TriageResult) -> DoerResult | None:
    """Pick and run the right routine task. Returns None if nothing applies."""
    if triage.category not in (Category.ROUTINE_ADMIN, Category.PERSONAL):
        return None

    try:
        if _is_otp(raw_text):
            data = await _llm_json(OTP_PROMPT, raw_text, 160)
            return DoerResult(
                task_type=DoerTaskType.OTP_EXPLAINER,
                plain_text=data.get("plain_text"), original_text=raw_text,
                summary=data.get("summary"),
            )

        if _is_bill_message(triage):
            data = await _llm_json(BILL_PROMPT, raw_text)
            return DoerResult(
                task_type=DoerTaskType.BILL_DUE,
                biller=data.get("biller"), amount=data.get("amount"),
                due_date=data.get("due_date"),
                reminder_set_for=data.get("due_date"),
                summary=data.get("summary"),
            )

        if _is_appointment(raw_text):
            data = await _llm_json(APPOINTMENT_PROMPT, raw_text)
            return DoerResult(
                task_type=DoerTaskType.APPOINTMENT,
                appointment_title=data.get("appointment_title"),
                appointment_datetime=data.get("appointment_datetime"),
                appointment_location=data.get("appointment_location"),
                reminder_set_for=data.get("appointment_datetime"),
                summary=data.get("summary"),
            )

        if _is_bank_alert(raw_text):
            data = await _llm_json(BANK_REWRITE_PROMPT, raw_text)
            return DoerResult(
                task_type=DoerTaskType.BANK_REWRITE,
                plain_text=data.get("plain_text"), original_text=raw_text,
                summary=data.get("summary"),
            )

        return None
    except Exception as exc:
        logger.error(f"Doer agent error: {exc}")
        return None


async def prepare_complaint(
    raw_text: str,
    entities: TriageEntities,
    investigation: InvestigatorResult | None,
    victim_name: str,
    victim_phone: str,
    money_lost: bool = False,
) -> DoerResult:
    """
    Draft a Chakshu / 1930 complaint. PREPARED ONLY — held for one-tap human approval,
    never auto-filed. Government complaints at volume are a real harm.
    """
    suspicious_number = entities.phone_numbers[0] if entities.phone_numbers else None
    urls = list(entities.urls or [])
    if investigation:
        for d in investigation.domain_intel:
            if d.redirect_final_url and d.redirect_final_url not in urls:
                urls.append(d.redirect_final_url)

    narrative = ""
    try:
        facts = (
            f"Message received: {raw_text[:500]}\n"
            f"Date: {datetime.now(timezone.utc).strftime('%d %B %Y')}\n"
            f"Suspicious number: {suspicious_number or 'unknown'}\n"
            f"Suspicious links: {', '.join(urls) or 'none'}\n"
            f"Money lost: {'yes' if money_lost else 'no'}"
        )
        data = await _llm_json(COMPLAINT_PROMPT, facts, 400)
        narrative = data.get("narrative", "")
    except Exception as exc:
        logger.warning(f"complaint narrative failed: {exc}")
        narrative = (
            f"On {datetime.now(timezone.utc).strftime('%d %B %Y')} I received a fraudulent message "
            f"from {suspicious_number or 'an unknown sender'}. "
            f"The message read: \"{raw_text[:300]}\". "
            f"{'A malicious link was included: ' + urls[0] if urls else ''} "
            f"{'Money was lost.' if money_lost else 'No money was lost.'}"
        )

    draft = ComplaintDraft(
        channel="cybercrime_1930" if money_lost else "chakshu",
        victim_name=victim_name,
        victim_phone=victim_phone,
        incident_datetime=datetime.now(timezone.utc).isoformat(),
        suspicious_number=suspicious_number,
        suspicious_urls=urls[:5],
        amount_involved=entities.amount,
        money_lost=money_lost,
        message_verbatim=raw_text[:1000],
        narrative=narrative,
        portal_url="https://sancharsaathi.gov.in/sfc/" if not money_lost else "https://cybercrime.gov.in/",
    )
    logger.info(f"📋 Complaint prefilled ({draft.channel}) for {victim_name}")
    return DoerResult(
        task_type=DoerTaskType.COMPLAINT_PREFILL,
        complaint=draft,
        summary=f"{draft.channel} complaint drafted — awaiting one-tap approval",
    )
