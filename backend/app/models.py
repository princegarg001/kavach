"""
Kavach Pydantic models — shared across all agents and API layers.
"""
from __future__ import annotations
from pydantic import BaseModel, Field, computed_field
from typing import Optional, Literal, List, Dict, Any
from datetime import datetime
from enum import Enum


# ── Enums ──────────────────────────────────────────────────────────────────────

class Category(str, Enum):
    SCAM = "scam"
    ROUTINE_ADMIN = "routine_admin"
    PERSONAL = "personal"
    UNKNOWN = "unknown"


class PolicyAction(str, Enum):
    SILENT_KILL = "silent_kill"
    ACT = "act"
    PREPARE = "prepare"
    ESCALATE_MOTHER = "escalate_mother"
    ESCALATE_NATU = "escalate_natu"
    ESCALATE_NATU_URGENT = "escalate_natu_urgent"


class VerdictSource(str, Enum):
    IMMUNITY = "immunity_ledger"
    TRIAGE = "triage"
    INVESTIGATOR = "investigator"
    POLICY = "policy"


class ThreatType(str, Enum):
    """Types of threats detected by intelligence tools."""
    PHISHING = "phishing"
    MALWARE = "malware"
    SOCIAL_ENGINEERING = "social_engineering"
    CREDENTIAL_HARVESTING = "credential_harvesting"
    FINANCIAL_FRAUD = "financial_fraud"
    UNKNOWN = "unknown"


class InvestigatorMode(str, Enum):
    """How the investigator ran."""
    STRANDS = "strands"       # strands.Agent ReAct loop (Strands Agents SDK)
    AGENTIC = "agentic"       # hand-rolled tool-calling ReAct loop
    FIXED = "fixed"           # deterministic fan-out — all tools, always
    FALLBACK = "fallback"     # a higher mode failed, a lower one took over


# ── Agent reasoning trace ─────────────────────────────────────────────────────

class AgentStep(BaseModel):
    """One step in an agent's reasoning loop — rendered on the dashboard."""
    kind: Literal["thought", "tool_call", "observation", "verdict"]
    content: str = ""                     # thought text / verdict summary
    tool_name: Optional[str] = None       # for kind == tool_call / observation
    tool_args: Dict[str, Any] = {}        # arguments the model passed
    duration_ms: int = 0                  # wall time for tool_call steps


class StageTrace(BaseModel):
    """Latency of one pipeline stage, for the audit timeline."""
    stage: str                           # triage | immunity | reputation | investigator | doer | policy | educator
    duration_ms: int = 0
    detail: Optional[str] = None


# ── Inbound message ────────────────────────────────────────────────────────────

class InboundMessage(BaseModel):
    member_id: str                       # phone number or member slug
    member_name: str = "Unknown"
    raw_text: str
    source: Literal["whatsapp", "sms", "email"] = "whatsapp"
    received_at: datetime = Field(default_factory=datetime.utcnow)
    message_id: Optional[str] = None


# ── Triage output ──────────────────────────────────────────────────────────────

class TriageEntities(BaseModel):
    amount: Optional[float] = None
    sender_id: Optional[str] = None
    urls: List[str] = []
    deadline: Optional[str] = None
    action_demanded: Optional[str] = None
    biller: Optional[str] = None
    phone_numbers: List[str] = []        # extracted phone numbers
    upi_ids: List[str] = []              # extracted UPI IDs


class TriageResult(BaseModel):
    category: Category
    entities: TriageEntities
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
    language: str = "en"                 # en, hi, hinglish
    dlt_verified: bool = False           # True if sender matches DLT pattern
    urgency_signals: List[str] = []      # e.g. ["within 24 hours", "account will be blocked"]


# ── Investigator output ────────────────────────────────────────────────────────

class DomainIntel(BaseModel):
    domain: str
    age_days: Optional[int] = None
    registrar: Optional[str] = None      # cheap registrars = risk signal
    tls_age_days: Optional[int] = None
    tls_issuer: Optional[str] = None
    tls_san_domains: List[str] = []      # Subject Alternative Names
    redirect_final_url: Optional[str] = None
    redirect_hops: int = 0
    redirect_chain: List[str] = []
    urlhaus_hit: bool = False
    urlhaus_threat: Optional[str] = None
    phishtank_hit: bool = False
    phishtank_verified: bool = False
    google_safe_browsing_hit: bool = False
    google_safe_browsing_threats: List[str] = []
    virustotal_positives: int = 0
    virustotal_total: int = 0
    typosquat_target: Optional[str] = None
    typosquat_distance: Optional[int] = None
    homograph_detected: bool = False


class HTMLAnalysis(BaseModel):
    """Results from HTML content analysis of a landing page."""
    has_password_field: bool = False
    has_otp_field: bool = False
    has_aadhaar_field: bool = False
    has_pan_field: bool = False
    has_card_field: bool = False
    has_hidden_iframe: bool = False
    form_count: int = 0
    input_fields: List[Dict[str, str]] = []  # name, type pairs
    page_title: Optional[str] = None
    favicon_url: Optional[str] = None
    suspicious_js: bool = False
    credential_harvest_score: float = 0.0  # 0-1 score


class InvestigatorResult(BaseModel):
    verdict: Literal["scam", "safe", "uncertain"]
    confidence: float = Field(ge=0.0, le=1.0)
    threat_score: int = Field(default=0, ge=0, le=100)  # composite 0-100
    threat_type: ThreatType = ThreatType.UNKNOWN
    domain_intel: List[DomainIntel] = []
    html_analysis: Optional[HTMLAnalysis] = None
    screenshot_path: Optional[str] = None
    reasoning: str
    investigation_time_ms: int = 0
    tools_used: List[str] = []           # which tools actually ran
    tools_failed: List[str] = []         # which tools failed
    mode: InvestigatorMode = InvestigatorMode.FIXED
    agent_trace: List[AgentStep] = []    # the ReAct loop, step by step
    llm_calls: int = 0                   # how many model round-trips it took


# ── Immunity ledger ────────────────────────────────────────────────────────────

class ScamSignature(BaseModel):
    group_id: str
    message_hash: str
    embedding: List[float]
    canonical_embedding: List[float] = []  # embedding of the pitch pattern without identifiers
    sender_pattern: Optional[str] = None
    domain_fingerprint: Optional[str] = None
    semantic_shape: str              # e.g. "urgency + KYC expiry + credential link"
    cluster_id: Optional[str] = None     # groups similar signatures
    original_text_snippet: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    source_member_id: str
    hit_count: int = 0                   # how many times this signature was re-matched


class ImmunityCheckResult(BaseModel):
    matched: bool
    similarity_score: float = 0.0
    matched_signature_id: Optional[str] = None
    matched_semantic_shape: Optional[str] = None
    hit_count: int = 0                   # how popular this signature is
    check_time_ms: int = 0
    match_vector: Optional[Literal["raw", "canonical"]] = None  # which embedding matched


# ── Sender reputation ──────────────────────────────────────────────────────────

class ReputationResult(BaseModel):
    """What the group already knows about this sender."""
    sender_key: str                      # normalized sender id / phone / domain
    seen_count: int = 0                   # total prior messages from this sender
    scam_count: int = 0                   # prior messages confirmed as scam
    last_verdict: Optional[str] = None    # scam | safe | unknown
    first_seen: Optional[datetime] = None
    is_repeat_offender: bool = False      # scam_count >= threshold
    lookup_ms: int = 0


# ── Doer output ────────────────────────────────────────────────────────────────

class DoerTaskType(str, Enum):
    BILL_DUE = "bill_due_date"
    BANK_REWRITE = "bank_alert_rewrite"
    APPOINTMENT = "appointment_reminder"
    OTP_EXPLAINER = "otp_explainer"
    COMPLAINT_PREFILL = "complaint_prefill"


class ComplaintDraft(BaseModel):
    """A pre-filled Chakshu / 1930 cyber-fraud complaint. Prepared, never auto-submitted."""
    channel: Literal["chakshu", "cybercrime_1930"] = "chakshu"
    category: str = "Phishing / Fraudulent Message"
    victim_name: str = ""
    victim_phone: str = ""
    incident_datetime: Optional[str] = None
    suspicious_number: Optional[str] = None
    suspicious_urls: List[str] = []
    amount_involved: Optional[float] = None
    money_lost: bool = False
    message_verbatim: str = ""
    narrative: str = ""                   # auto-written description of what happened
    portal_url: str = "https://sancharsaathi.gov.in/sfc/"
    status: Literal["prepared", "submitted", "discarded"] = "prepared"


class DoerResult(BaseModel):
    task_type: DoerTaskType
    # Bill due date
    biller: Optional[str] = None
    amount: Optional[float] = None
    due_date: Optional[str] = None
    # Bank alert rewrite / OTP explainer
    plain_text: Optional[str] = None
    original_text: Optional[str] = None
    # Appointment reminder
    appointment_title: Optional[str] = None
    appointment_datetime: Optional[str] = None
    appointment_location: Optional[str] = None
    # Complaint prefill
    complaint: Optional[ComplaintDraft] = None
    # Common
    reminder_set_for: Optional[str] = None
    summary: Optional[str] = None         # one-line human summary for the digest


# ── Policy decision ────────────────────────────────────────────────────────────

class PolicyDecision(BaseModel):
    action: PolicyAction
    reason: str
    rule_matched: str
    rules_evaluated: List[str] = []      # audit trail — all rules checked
    escalation_target: Optional[Literal["mother", "natu"]] = None
    chakshu_prefilled: bool = False
    risk_score: int = 0                  # composite 0-100
    signals: List[str] = []              # human-readable signals that drove the score


# ── Member & Activity ─────────────────────────────────────────────────────────

class MemberActivity(BaseModel):
    member_id: str
    member_name: str
    enrolled: bool = True
    total_messages: int = 0
    scams_blocked: int = 0
    escalations: int = 0
    last_active: Optional[datetime] = None


# ── Daily Digest ───────────────────────────────────────────────────────────────

class DigestMessage(BaseModel):
    period_start: datetime
    period_end: datetime
    total_messages: int = 0
    scams_blocked: int = 0
    immunity_hits: int = 0
    escalations: int = 0
    doer_actions: int = 0
    highlights: List[str] = []           # notable events


# ── Full pipeline result (stored as audit event) ───────────────────────────────

class AuditEvent(BaseModel):
    id: Optional[str] = None
    message_id: str
    member_id: str
    member_name: str
    raw_text: str
    source: str
    received_at: datetime
    triage: Optional[TriageResult] = None
    immunity: Optional[ImmunityCheckResult] = None
    reputation: Optional[ReputationResult] = None
    investigation: Optional[InvestigatorResult] = None
    doer: Optional[DoerResult] = None
    policy: Optional[PolicyDecision] = None
    education_note: Optional[str] = None          # plain-language "why" for the protected person
    stage_traces: List[StageTrace] = []           # per-stage latency breakdown
    human_decision: Optional[Literal["block", "safe", "report"]] = None
    total_time_ms: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
