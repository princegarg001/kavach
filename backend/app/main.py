"""
Kavach — FastAPI main application.

Endpoints:
  POST /whatsapp      — Twilio webhook (WhatsApp messages)
  POST /whatsapp/reply — Twilio webhook for responses (BLOCK/SAFE/REPORT)
  POST /ingest        — Direct ingest (SMS forwarder + testing)
  GET  /events        — SSE stream for dashboard live updates
  GET  /audit         — Paginated audit log
  GET  /health        — Railway/Render health check
  POST /enroll        — Member enrollment (consent)
  POST /unenroll      — Kill switch
  GET  /stats         — Dashboard summary stats
  GET  /members       — List enrolled members + activity
  GET  /immunity/stats — Immunity ledger statistics
  POST /test/message  — Dev-only endpoint for quick testing
"""
import asyncio
import json
import logging
import time
import uuid
import re
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import AsyncGenerator

from fastapi import FastAPI, Form, Request, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

from app.config import settings
from app.models import InboundMessage, AuditEvent
from app.db.database import get_supabase
from app.db.events import save_audit_event, get_audit_events
from app.agents.graph import run_kavach_pipeline

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("kavach")

# ── Rate limiting state ────────────────────────────────────────────────────────
_rate_counts: dict[str, list[float]] = defaultdict(list)

# ── Dedup + intake telemetry ──────────────────────────────────────────────────
_seen_messages: dict[str, float] = {}          # message_id -> monotonic ts
_intake_stats = {
    "last_message_at": None,                    # ISO string
    "by_source": defaultdict(int),              # source -> count (session)
    "recent": deque(maxlen=200),                # monotonic ts of recent messages
}


def _is_duplicate(message_id: str) -> bool:
    """True if we've processed this message_id within DEDUP_WINDOW_SECONDS."""
    now = time.monotonic()
    cutoff = now - settings.DEDUP_WINDOW_SECONDS
    for mid, ts in list(_seen_messages.items()):
        if ts < cutoff:
            del _seen_messages[mid]
    if message_id in _seen_messages:
        return True
    _seen_messages[message_id] = now
    return False


def _record_intake(source: str):
    _intake_stats["last_message_at"] = datetime.utcnow().isoformat() + "Z"
    _intake_stats["by_source"][source] += 1
    _intake_stats["recent"].append(time.monotonic())


def _require_ingest_token(request: Request) -> None:
    """Enforce X-Kavach-Token when a secret is configured (always in production)."""
    secret = settings.INGEST_SECRET
    if not secret:
        if settings.APP_ENV == "production":
            raise HTTPException(status_code=503, detail="INGEST_SECRET not configured")
        return  # dev convenience
    if request.headers.get("X-Kavach-Token") != secret:
        raise HTTPException(status_code=401, detail="bad or missing X-Kavach-Token")


async def _validate_twilio(request: Request, form: dict) -> None:
    """
    Validate X-Twilio-Signature. No-op unless TWILIO_VALIDATE_SIGNATURE and creds set.

    Twilio signs the exact public URL it was configured to call. Behind a proxy
    (Render's Cloudflare edge, etc.) the ASGI-reconstructed `request.url` can end
    up with the wrong scheme/host even with --proxy-headers, so we try several
    candidate URLs and accept if ANY of them validates. This doesn't weaken
    security — the attacker still needs a signature valid for one of the URLs
    Twilio could plausibly have used; it only avoids false-negative rejections
    from proxy URL-reconstruction quirks.
    """
    if not (settings.TWILIO_VALIDATE_SIGNATURE and settings.TWILIO_AUTH_TOKEN):
        return
    sig = request.headers.get("X-Twilio-Signature", "")
    if not sig:
        raise HTTPException(status_code=403, detail="missing X-Twilio-Signature")

    candidates: list[str] = []
    seen: set[str] = set()

    def _add(u: str | None):
        if u and u not in seen:
            seen.add(u)
            candidates.append(u)

    _add(str(request.url))
    _add(str(request.url).replace("http://", "https://", 1))

    fwd_proto = request.headers.get("x-forwarded-proto", "https").split(",")[0].strip()
    fwd_host = request.headers.get("x-forwarded-host", "").split(",")[0].strip() or request.headers.get("host", "")
    if fwd_host:
        _add(f"{fwd_proto}://{fwd_host}{request.url.path}")
        _add(f"https://{fwd_host}{request.url.path}")

    if settings.PUBLIC_BASE_URL:
        _add(settings.PUBLIC_BASE_URL.rstrip("/") + request.url.path)

    try:
        from twilio.request_validator import RequestValidator
        validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
        if any(validator.validate(u, form, sig) for u in candidates):
            return
        logger.warning(f"Twilio signature mismatch against all candidates: {candidates}")
        raise HTTPException(status_code=403, detail="invalid Twilio signature")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"Twilio signature check errored (allowing): {exc}")


def _check_rate_limit(member_id: str) -> bool:
    """Returns True if the member is within rate limits."""
    now = time.monotonic()
    window = 60.0  # 1 minute window
    # Clean old entries
    _rate_counts[member_id] = [
        t for t in _rate_counts[member_id] if now - t < window
    ]
    if len(_rate_counts[member_id]) >= settings.RATE_LIMIT_PER_MEMBER:
        return False
    _rate_counts[member_id].append(now)
    return True


# ── SSE broadcast queue ────────────────────────────────────────────────────────
sse_clients: list[asyncio.Queue] = []


async def broadcast_event(event: dict):
    """Push an event to all connected SSE dashboard clients."""
    dead = []
    for q in sse_clients:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        sse_clients.remove(q)


# ── Enrollment check ──────────────────────────────────────────────────────────
def _is_enrolled(member_id: str) -> bool:
    """Check if a member is enrolled (consented) in Kavach."""
    try:
        db = get_supabase()
        result = db.table("enrolled_members").select("enrolled").eq(
            "member_id", member_id
        ).execute()
        if result.data and len(result.data) > 0:
            return result.data[0].get("enrolled", False)
    except Exception:
        pass
    # Default: allow (for demo, assume enrolled)
    return True


# ── Lifespan ───────────────────────────────────────────────────────────────────
_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    logger.info("🛡️  Kavach starting up…")

    # Seed the hardcoded demo group into enrolled_members so the consent panel +
    # kill switch work out of the box. Never downgrades an existing row.
    try:
        db = get_supabase()
        existing = {r["member_id"] for r in (db.table("enrolled_members").select("member_id").execute().data or [])}
        for mid, name in ((settings.MOTHER_PHONE, "Mother (protected)"), (settings.NATU_PHONE, "Natu (decides)")):
            if mid and "0000000000" not in mid and mid not in existing:
                row = {"member_id": mid, "member_name": name, "enrolled": True,
                       "enrolled_at": datetime.utcnow().isoformat() + "Z",
                       "consent_scope": DEFAULT_CONSENT_SCOPE}
                try:
                    db.table("enrolled_members").upsert(row).execute()
                except Exception:
                    row.pop("consent_scope", None)
                    db.table("enrolled_members").upsert(row).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"could not seed enrolled_members: {exc}")

    if settings.DIGEST_ENABLED or settings.EMAIL_INTAKE_ENABLED:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.cron import CronTrigger
            from apscheduler.triggers.interval import IntervalTrigger
            _scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
            if settings.DIGEST_ENABLED:
                from app.digest import send_daily_digest
                _scheduler.add_job(send_daily_digest, CronTrigger(hour=settings.DIGEST_HOUR, minute=0),
                                   id="daily_digest", replace_existing=True)
                logger.info(f"⏰ Daily digest scheduled for {settings.DIGEST_HOUR:02d}:00 IST")
            if settings.EMAIL_INTAKE_ENABLED:
                from app.email_intake import poll_once
                _scheduler.add_job(lambda: poll_once(process_message),
                                   IntervalTrigger(seconds=settings.EMAIL_POLL_SECONDS),
                                   id="email_poll", replace_existing=True, next_run_time=datetime.utcnow())
                logger.info(f"📧 Email intake polling every {settings.EMAIL_POLL_SECONDS}s ({settings.IMAP_USER})")
            _scheduler.start()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"scheduler not started: {exc}")
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)
    logger.info("🛡️  Kavach shutting down…")


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Kavach API",
    description="Autonomous scam protection agent for family groups — herd immunity for a family",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helper ─────────────────────────────────────────────────────────────────────
def _apply_result(event: AuditEvent, result: dict) -> None:
    """
    Copy a pipeline result onto the AuditEvent. Values may be pydantic models
    (PIPELINE_MODE=local) or plain dicts (PIPELINE_MODE=agentcore) — coerce dicts.
    """
    from app.models import (
        TriageResult, ImmunityCheckResult, ReputationResult,
        InvestigatorResult, DoerResult, PolicyDecision, StageTrace,
    )

    def _as(model, v):
        if v is None or isinstance(v, model):
            return v
        try:
            return model(**v) if isinstance(v, dict) else v
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"could not hydrate {model.__name__}: {exc}")
            return None

    event.triage = _as(TriageResult, result.get("triage"))
    event.immunity = _as(ImmunityCheckResult, result.get("immunity"))
    event.reputation = _as(ReputationResult, result.get("reputation"))
    event.investigation = _as(InvestigatorResult, result.get("investigation"))
    event.doer = _as(DoerResult, result.get("doer"))
    event.policy = _as(PolicyDecision, result.get("policy"))
    event.education_note = result.get("education_note")
    traces = result.get("stage_traces") or []
    event.stage_traces = [t if isinstance(t, StageTrace) else StageTrace(**t) for t in traces]


async def process_message(msg: InboundMessage):
    """
    Run the full Kavach agent pipeline for one message.
    Results are saved to the audit log and broadcast over SSE.
    """
    t0 = time.monotonic()
    correlation_id = msg.message_id or str(uuid.uuid4())
    _preview = msg.raw_text[:40] if settings.APP_ENV == "production" else msg.raw_text[:80]
    logger.info(f"📨 [{correlation_id}] Processing from {msg.member_id}: {_preview}")

    # Drop exact re-deliveries (SMS forwarders and Twilio both retry).
    if _is_duplicate(correlation_id):
        logger.info(f"🔁 [{correlation_id}] Duplicate — skipping")
        return None
    _record_intake(msg.source)

    # Check enrollment (consent / kill switch)
    if not _is_enrolled(msg.member_id):
        logger.info(f"⏭️  [{correlation_id}] Member {msg.member_id} not enrolled, skipping")
        return None

    # Rate limit check
    if not _check_rate_limit(msg.member_id):
        logger.warning(f"🚫 [{correlation_id}] Rate limit exceeded for {msg.member_id}")
        return None

    event = AuditEvent(
        message_id=correlation_id,
        member_id=msg.member_id,
        member_name=msg.member_name,
        raw_text=msg.raw_text,
        source=msg.source,
        received_at=msg.received_at,
    )

    try:
        if settings.PIPELINE_MODE == "agentcore":
            from app.runtime_client import invoke_pipeline_remote
            result = await invoke_pipeline_remote(msg)
        else:
            result = await run_kavach_pipeline(msg)

        _apply_result(event, result)
        event.total_time_ms = int((time.monotonic() - t0) * 1000)

        logger.info(
            f"✅ [{correlation_id}] Pipeline done in {event.total_time_ms}ms | "
            f"policy={event.policy.action if event.policy else 'none'}"
        )
    except Exception as exc:
        logger.exception(f"❌ [{correlation_id}] Pipeline error for {msg.member_id}: {exc}")

    # Save to Supabase
    saved = await save_audit_event(event)
    if saved:
        event.id = saved.get("id")

    # Broadcast to dashboard
    await broadcast_event({
        "type": "audit_event",
        "data": json.loads(event.model_dump_json()),
    })

    return event


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Render/Railway health check."""
    return {
        "status": "ok",
        "service": "kavach",
        "version": "2.0.0",
        "uptime": "running",
    }


@app.get("/debug/request-info")
async def debug_request_info(request: Request):
    """Temporary — shows how the ASGI layer sees this request, and non-secret
    fingerprints of the config actually loaded (lengths/prefixes only, never values)."""
    tok = settings.TWILIO_AUTH_TOKEN or ""
    sid = settings.TWILIO_ACCOUNT_SID or ""
    return {
        "url": str(request.url),
        "scope_scheme": request.scope.get("scheme"),
        "scope_server": request.scope.get("server"),
        "headers": {k: v for k, v in request.headers.items()
                    if k.lower() in ("host", "x-forwarded-proto", "x-forwarded-host",
                                      "x-forwarded-for", "x-forwarded-port", "cf-visitor")},
        "config": {
            "PUBLIC_BASE_URL": settings.PUBLIC_BASE_URL,
            "TWILIO_VALIDATE_SIGNATURE": settings.TWILIO_VALIDATE_SIGNATURE,
            "TWILIO_AUTH_TOKEN_len": len(tok),
            "TWILIO_AUTH_TOKEN_prefix": tok[:4],
            "TWILIO_AUTH_TOKEN_suffix": tok[-4:],
            "TWILIO_AUTH_TOKEN_has_whitespace": tok != tok.strip(),
            "TWILIO_ACCOUNT_SID_len": len(sid),
            "TWILIO_ACCOUNT_SID_prefix": sid[:6],
        },
    }


@app.post("/whatsapp")
async def whatsapp_webhook(
    background_tasks: BackgroundTasks,
    request: Request,
    Body: str = Form(default=""),
    From: str = Form(default=""),
    To: str = Form(default=""),
    MessageSid: str = Form(default=""),
):
    """
    Twilio WhatsApp webhook.
    Twilio POSTs form-encoded data; we return TwiML.
    Heavy processing is done in background so Twilio doesn't time out.
    """
    try:
        _form = {k: v for k, v in (await request.form()).items()}
    except Exception:
        _form = {"Body": Body, "From": From, "To": To, "MessageSid": MessageSid}
    await _validate_twilio(request, _form)

    if not Body.strip():
        return StreamingResponse(
            iter(["<Response></Response>"]),
            media_type="text/xml",
        )

    body_stripped = Body.strip().upper()

    # Check if this is a reply to an escalation
    if body_stripped in ("YES", "NO", "BLOCK", "SAFE", "REPORT"):
        background_tasks.add_task(
            _handle_reply, From, body_stripped, MessageSid
        )
        return StreamingResponse(
            iter(["<Response></Response>"]),
            media_type="text/xml",
        )

    msg = InboundMessage(
        member_id=From,
        member_name=From.replace("whatsapp:+", "+"),
        raw_text=Body.strip(),
        source="whatsapp",
        message_id=MessageSid or str(uuid.uuid4()),
    )
    background_tasks.add_task(process_message, msg)

    # Return empty TwiML immediately so Twilio doesn't retry
    return StreamingResponse(
        iter(["<Response></Response>"]),
        media_type="text/xml",
    )


async def _handle_reply(from_number: str, reply: str, message_sid: str):
    """
    Process replies from Mother/Natu:
    YES/SAFE  → mark as false positive
    NO/BLOCK  → write to immunity ledger
    REPORT    → prepare Chakshu complaint
    """
    logger.info(f"📩 Reply received from {from_number}: {reply}")

    db = get_supabase()

    # Correlate the reply to the exact escalation we sent this person.
    try:
        from app.db.escalations import resolve_latest
        pending = await resolve_latest(from_number)

        if pending and pending.get("message_id"):
            lookup = (
                db.table("audit_events")
                .select("*")
                .eq("message_id", pending["message_id"])
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
        else:
            logger.info("No pending escalation row — falling back to most-recent event")
            lookup = (
                db.table("audit_events")
                .select("*")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )

        if not lookup.data:
            logger.warning("No event found for reply")
            return

        event_data = lookup.data[0]
        event_id = event_data.get("id")

        if reply in ("YES", "SAFE"):
            # Mark as safe / false positive
            db.table("audit_events").update({
                "human_decision": "safe"
            }).eq("id", event_id).execute()
            logger.info(f"✅ Event {event_id} marked SAFE by {from_number}")

        elif reply in ("NO", "BLOCK"):
            # Mark as confirmed scam + write to immunity + bump sender reputation
            db.table("audit_events").update({
                "human_decision": "block"
            }).eq("id", event_id).execute()
            logger.info(f"🛡️  Event {event_id} BLOCKED by {from_number}")

            raw_text = event_data.get("raw_text", "")
            if raw_text:
                from app.memory.immunity_ledger import write_immunity
                from app.memory.reputation import mark_confirmed_scam, derive_sender_key
                from app.models import TriageEntities
                triage_json = event_data.get("triage_json", {}) or {}
                entities_data = triage_json.get("entities", {})
                entities = TriageEntities(**entities_data) if entities_data else TriageEntities()
                await write_immunity(
                    raw_text=raw_text,
                    group_id=settings.GROUP_ID,
                    member_id=event_data.get("member_id", "unknown"),
                    entities=entities,
                    investigation=None,
                )
                # Feedback loop: this sender is now a known offender for the group.
                sender_key = derive_sender_key(entities, event_data.get("member_id", "unknown"))
                await mark_confirmed_scam(sender_key, settings.GROUP_ID)

        elif reply == "REPORT":
            db.table("audit_events").update({
                "human_decision": "report"
            }).eq("id", event_id).execute()
            logger.info(f"📋 Event {event_id} flagged for REPORT by {from_number}")

            # Prepare (do NOT submit) a Chakshu / 1930 complaint and store it on the event.
            raw_text = event_data.get("raw_text", "")
            if raw_text:
                try:
                    from app.agents.doer import prepare_complaint
                    from app.models import TriageEntities
                    triage_json = event_data.get("triage_json", {}) or {}
                    entities = TriageEntities(**(triage_json.get("entities", {}) or {}))
                    complaint_result = await prepare_complaint(
                        raw_text=raw_text,
                        entities=entities,
                        investigation=None,
                        victim_name=event_data.get("member_name", "Family member"),
                        victim_phone=str(event_data.get("member_id", "")).replace("whatsapp:", ""),
                        money_lost=True,
                    )
                    db.table("audit_events").update({
                        "doer_json": complaint_result.model_dump()
                    }).eq("id", event_id).execute()
                    logger.info(f"📋 Complaint prefilled for event {event_id} (held for approval)")
                except Exception as exc:
                    logger.error(f"complaint prefill on REPORT failed: {exc}")

        # Broadcast update to dashboard
        await broadcast_event({
            "type": "human_decision",
            "data": {
                "event_id": event_id,
                "decision": reply.lower(),
                "decided_by": from_number,
            },
        })

    except Exception as exc:
        logger.error(f"Reply handling error: {exc}")


@app.post("/ingest")
async def ingest(msg: InboundMessage, background_tasks: BackgroundTasks, request: Request):
    """
    Direct ingest endpoint for SMS forwarder apps.
    Requires header `X-Kavach-Token: <INGEST_SECRET>` when a secret is configured
    (mandatory in production). Returns 202 immediately; pipeline runs in background.
    """
    _require_ingest_token(request)
    if not msg.message_id:
        msg.message_id = str(uuid.uuid4())
    background_tasks.add_task(process_message, msg)
    return JSONResponse(
        status_code=202,
        content={"status": "accepted", "message_id": msg.message_id},
    )


@app.post("/ingest/email")
async def ingest_email(background_tasks: BackgroundTasks, request: Request):
    """
    Inbound-email webhook for the filter-forward path (Cloudflare Email Routing,
    SendGrid Inbound Parse, Mailgun routes). Accepts either JSON
    {from,subject,text,message_id} or form-encoded (SendGrid/Mailgun style).
    """
    _require_ingest_token(request)
    ct = request.headers.get("content-type", "")
    if ct.startswith("application/json"):
        data = await request.json()
    else:
        data = {k: v for k, v in (await request.form()).items()}

    sender = data.get("from") or data.get("sender") or data.get("From") or "Email"
    subject = data.get("subject") or data.get("Subject") or ""
    text = data.get("text") or data.get("body-plain") or data.get("stripped-text") or data.get("html") or ""
    mid = data.get("message_id") or data.get("Message-Id") or str(uuid.uuid4())

    msg = InboundMessage(
        member_id=settings.EMAIL_MEMBER_ID or settings.IMAP_USER or "email-inbox",
        member_name=str(sender)[:80],
        raw_text=f"Subject: {subject}\nFrom: {sender}\n\n{text}".strip()[:4000],
        source="email",
        message_id=str(mid).strip("<> "),
    )
    background_tasks.add_task(process_message, msg)
    return JSONResponse(status_code=202, content={"status": "accepted", "message_id": msg.message_id})


@app.get("/intake/status")
async def intake_status():
    """Health of the automatic-intake channels — powers the dashboard 'INTAKE' pill."""
    now = time.monotonic()
    recent = [t for t in _intake_stats["recent"] if now - t < 3600]
    last_iso = _intake_stats["last_message_at"]
    seconds_since = None
    if recent:
        seconds_since = int(now - _intake_stats["recent"][-1])
    return {
        "connected": bool(recent),
        "last_message_at": last_iso,
        "seconds_since_last": seconds_since,
        "messages_last_hour": len(recent),
        "by_source": dict(_intake_stats["by_source"]),
        "pipeline_mode": settings.PIPELINE_MODE,
        "agent_runtime": settings.AGENT_RUNTIME,
    }


@app.get("/events")
async def sse_events(request: Request):
    """
    Server-Sent Events stream for the React dashboard.
    Each connected client gets its own queue.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    sse_clients.append(queue)

    async def event_generator() -> AsyncGenerator[str, None]:
        # Send a heartbeat immediately so the browser doesn't time out
        yield 'data: {"type": "connected"}\n\n'
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat keep-alive
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if queue in sse_clients:
                sse_clients.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/audit")
async def audit_log(limit: int = 50, offset: int = 0, member_id: str = None):
    """Paginated audit log for the dashboard."""
    events = await get_audit_events(limit=limit, offset=offset, member_id=member_id)
    return {"events": events, "limit": limit, "offset": offset}


@app.get("/stats")
async def stats():
    """Summary stats for dashboard header cards."""
    db = get_supabase()
    try:
        total = db.table("audit_events").select("id", count="exact").execute()
        scams = db.table("audit_events").select("id", count="exact").eq("policy_action", "silent_kill").execute()
        escalations = db.table("audit_events").select("id", count="exact").in_(
            "policy_action", ["escalate_mother", "escalate_natu", "escalate_natu_urgent"]
        ).execute()
        immunity_hits = db.table("audit_events").select("id", count="exact").eq("immunity_matched", True).execute()

        # Average response time
        recent = db.table("audit_events").select("total_time_ms").order(
            "created_at", desc=True
        ).limit(100).execute()
        avg_time = 0
        if recent.data:
            times = [r.get("total_time_ms", 0) for r in recent.data if r.get("total_time_ms")]
            avg_time = int(sum(times) / len(times)) if times else 0

        return {
            "total_messages": total.count or 0,
            "scams_blocked": scams.count or 0,
            "escalations": escalations.count or 0,
            "immunity_hits": immunity_hits.count or 0,
            "avg_response_ms": avg_time,
        }
    except Exception:
        return {
            "total_messages": 0,
            "scams_blocked": 0,
            "escalations": 0,
            "immunity_hits": 0,
            "avg_response_ms": 0,
        }


@app.get("/members")
async def list_members():
    """List all enrolled members with activity stats."""
    db = get_supabase()
    try:
        members = db.table("enrolled_members").select("*").execute()
        result = []
        for m in (members.data or []):
            member_id = m.get("member_id")
            # Get activity stats
            msg_count = db.table("audit_events").select("id", count="exact").eq(
                "member_id", member_id
            ).execute()
            scam_count = db.table("audit_events").select("id", count="exact").eq(
                "member_id", member_id
            ).eq("policy_action", "silent_kill").execute()

            result.append({
                "member_id": member_id,
                "member_name": m.get("member_name", "Unknown"),
                "enrolled": m.get("enrolled", True),
                "enrolled_at": m.get("enrolled_at"),
                "total_messages": msg_count.count or 0,
                "scams_blocked": scam_count.count or 0,
            })
        return {"members": result}
    except Exception as exc:
        logger.error(f"Failed to list members: {exc}")
        return {"members": []}


@app.get("/immunity/stats")
async def immunity_stats():
    """Immunity ledger statistics for dashboard."""
    db = get_supabase()
    try:
        signatures = db.table("immunity_signatures").select("*").order(
            "created_at", desc=True
        ).limit(50).execute()

        total_count = db.table("immunity_signatures").select("id", count="exact").execute()

        # Immunity timeline data: group by day
        hits = db.table("audit_events").select(
            "created_at,total_time_ms,immunity_matched"
        ).eq("immunity_matched", True).order(
            "created_at", desc=True
        ).limit(100).execute()

        return {
            "total_signatures": total_count.count or 0,
            "recent_signatures": [
                {
                    "id": s.get("id"),
                    "semantic_shape": s.get("semantic_shape"),
                    "domain_fingerprint": s.get("domain_fingerprint"),
                    "created_at": s.get("created_at"),
                    "source_member_id": s.get("source_member_id"),
                }
                for s in (signatures.data or [])
            ],
            "recent_hits": [
                {
                    "created_at": h.get("created_at"),
                    "total_time_ms": h.get("total_time_ms"),
                }
                for h in (hits.data or [])
            ],
        }
    except Exception as exc:
        logger.error(f"Failed to get immunity stats: {exc}")
        return {"total_signatures": 0, "recent_signatures": [], "recent_hits": []}


@app.get("/reputation")
async def reputation_list(limit: int = 25):
    """Known senders and how the family has classified them."""
    db = get_supabase()
    try:
        rows = (
            db.table("sender_reputation")
            .select("*")
            .eq("group_id", settings.GROUP_ID)
            .order("scam_count", desc=True)
            .limit(limit)
            .execute()
        )
        return {"senders": rows.data or []}
    except Exception as exc:
        logger.error(f"Failed to list reputation: {exc}")
        return {"senders": []}


@app.get("/complaint/{message_id}")
async def get_complaint(message_id: str):
    """
    Return the pre-filled Chakshu / 1930 complaint for an event, if one was drafted.
    This is the 'held for one-tap' artifact — Kavach never submits it.
    """
    db = get_supabase()
    try:
        res = (
            db.table("audit_events")
            .select("id,member_name,raw_text,doer_json,policy_json,created_at")
            .eq("message_id", message_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="event not found")
        row = res.data[0]
        doer = row.get("doer_json") or {}
        if doer.get("task_type") != "complaint_prefill" or not doer.get("complaint"):
            return {"status": "no_complaint", "message_id": message_id}
        return {
            "status": "prepared",
            "message_id": message_id,
            "victim": row.get("member_name"),
            "created_at": row.get("created_at"),
            "complaint": doer["complaint"],
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to fetch complaint: {exc}")
        raise HTTPException(status_code=500, detail="lookup failed")


DEFAULT_CONSENT_SCOPE = (
    "Kavach may read messages this person forwards or that are auto-forwarded from "
    "their SMS/WhatsApp, investigate links they contain, and notify the family "
    "contact when a decision is needed. It never sends money, never files "
    "complaints without a human tap, and keeps a full audit log."
)


@app.post("/enroll")
async def enroll_member(member_id: str, member_name: str, consent_scope: str = DEFAULT_CONSENT_SCOPE):
    """Explicit consent enrollment — records who consented, when, and to what."""
    db = get_supabase()
    now = datetime.utcnow().isoformat() + "Z"
    row = {"member_id": member_id, "member_name": member_name, "enrolled": True,
           "enrolled_at": now, "consent_scope": consent_scope, "paused_at": None}
    try:
        db.table("enrolled_members").upsert(row).execute()
    except Exception:
        row.pop("consent_scope", None); row.pop("paused_at", None)
        db.table("enrolled_members").upsert(row).execute()
    await broadcast_event({"type": "member_enrolled", "data": {"member_id": member_id, "member_name": member_name}})
    return {"status": "enrolled", "member_id": member_id, "enrolled_at": now}


@app.post("/unenroll")
async def unenroll_member(member_id: str):
    """Kill switch — Kavach immediately stops reading this member's messages."""
    db = get_supabase()
    now = datetime.utcnow().isoformat() + "Z"
    try:
        db.table("enrolled_members").update({"enrolled": False, "paused_at": now}).eq("member_id", member_id).execute()
    except Exception:
        db.table("enrolled_members").update({"enrolled": False}).eq("member_id", member_id).execute()
    await broadcast_event({"type": "member_unenrolled", "data": {"member_id": member_id}})
    return {"status": "paused", "member_id": member_id}


@app.get("/consent")
async def consent_status():
    """Consent + kill-switch state for every member — powers the dashboard control panel."""
    db = get_supabase()
    try:
        rows = db.table("enrolled_members").select("*").execute().data or []
    except Exception:
        rows = []
    members = [{
        "member_id": r.get("member_id"),
        "member_name": r.get("member_name", "Unknown"),
        "enrolled": r.get("enrolled", True),
        "enrolled_at": r.get("enrolled_at"),
        "paused_at": r.get("paused_at"),
        "consent_scope": r.get("consent_scope") or DEFAULT_CONSENT_SCOPE,
    } for r in rows]
    return {
        "members": members,
        "monitoring_active": all(m["enrolled"] for m in members) if members else True,
        "paused_members": [m["member_name"] for m in members if not m["enrolled"]],
    }


@app.post("/digest/send")
async def digest_send():
    """Send the daily digest now (demo / on-demand)."""
    from app.digest import build_digest, send_daily_digest
    preview = await build_digest(24)
    sent = await send_daily_digest()
    return {"sent": sent, "summary": preview.model_dump(mode="json")}


@app.post("/test/message")
async def test_message(raw_text: str, member_name: str = "Test User"):
    """
    Dev-only convenience endpoint: submit a test message and get the result inline.
    """
    if settings.APP_ENV == "production":
        raise HTTPException(status_code=404, detail="Not found")

    msg = InboundMessage(
        member_id="test-member",
        member_name=member_name,
        raw_text=raw_text,
        source="sms",
        message_id=str(uuid.uuid4()),
    )
    event = await process_message(msg)
    if event:
        return json.loads(event.model_dump_json())
    return {"status": "skipped", "reason": "enrollment or rate limit"}
