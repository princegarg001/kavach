"""
Kavach Policy Engine v2 — deterministic YAML rule evaluator with audit trail.
"The model investigates. Code decides."

No LLM calls here. Pure rule evaluation against structured data.

v2 enhancements:
- Rule evaluation audit trail (which rules checked, which matched)
- Hot reload function
- Composite risk score computation
- Priority-based logging
"""
import logging
from pathlib import Path
from typing import Optional
import yaml

from app.models import (
    TriageResult,
    ImmunityCheckResult,
    InvestigatorResult,
    ReputationResult,
    PolicyDecision,
    PolicyAction,
    Category,
)

logger = logging.getLogger("kavach.policy")

# Load rules at module import time
_RULES_PATH = Path(__file__).parent / "rules.yaml"
_RULES: list[dict] = []


def _load_rules():
    global _RULES
    with open(_RULES_PATH) as f:
        data = yaml.safe_load(f)
    _RULES = data.get("rules", [])
    logger.info(f"📋 Loaded {len(_RULES)} policy rules")


_load_rules()


def hot_reload():
    """Reload rules from YAML without restarting the server."""
    _load_rules()
    logger.info("🔄 Policy rules hot-reloaded")


def _matches(
    rule: dict,
    triage: TriageResult,
    immunity: ImmunityCheckResult,
    investigation: Optional[InvestigatorResult],
    reputation: Optional[ReputationResult] = None,
) -> bool:
    """
    Check if a rule's conditions match the current pipeline state.
    Returns True if ALL conditions are satisfied.
    """
    conds = rule.get("conditions", {})
    if not conds:
        return True  # Empty conditions = always match (fallback)

    # Sender reputation conditions
    if "sender_repeat_offender" in conds:
        actual = bool(reputation and reputation.is_repeat_offender)
        if actual != conds["sender_repeat_offender"]:
            return False

    if "sender_prior_scam_count_gte" in conds:
        actual = reputation.scam_count if reputation else 0
        if actual < conds["sender_prior_scam_count_gte"]:
            return False

    # Immunity conditions
    if "immunity_matched" in conds:
        if immunity.matched != conds["immunity_matched"]:
            return False

    if "immunity_similarity_gte" in conds:
        if immunity.similarity_score < conds["immunity_similarity_gte"]:
            return False

    # Triage conditions
    if "triage_category" in conds:
        if triage.category.value != conds["triage_category"]:
            return False

    if "triage_confidence_gte" in conds:
        if triage.confidence < conds["triage_confidence_gte"]:
            return False

    if "triage_confidence_lt" in conds:
        if triage.confidence >= conds["triage_confidence_lt"]:
            return False

    if "no_amount" in conds and conds["no_amount"]:
        if triage.entities.amount is not None:
            return False

    if "no_action_demanded" in conds and conds["no_action_demanded"]:
        if triage.entities.action_demanded is not None:
            return False

    if "no_credential_action" in conds and conds["no_credential_action"]:
        cred_actions = {"share_otp", "personal_info", "click_link"}
        if triage.entities.action_demanded in cred_actions:
            return False

    if "has_urls" in conds and conds["has_urls"]:
        if not triage.entities.urls:
            return False

    if "has_amount" in conds and conds["has_amount"]:
        if triage.entities.amount is None:
            return False

    if "action_demanded_in" in conds:
        if triage.entities.action_demanded not in conds["action_demanded_in"]:
            return False

    # Investigation conditions
    if "investigation_verdict" in conds:
        if investigation is None:
            return False
        if investigation.verdict != conds["investigation_verdict"]:
            return False

    if "investigation_confidence_gte" in conds:
        if investigation is None:
            return False
        if investigation.confidence < conds["investigation_confidence_gte"]:
            return False

    if "has_urlhaus_or_phishtank_hit" in conds and conds["has_urlhaus_or_phishtank_hit"]:
        if investigation is None:
            return False
        has_hit = any(
            d.urlhaus_hit or d.phishtank_hit or d.google_safe_browsing_hit
            for d in investigation.domain_intel
        )
        if not has_hit:
            return False

    # any_of condition
    if "any_of" in conds:
        sub_conditions = conds["any_of"]
        any_matched = False
        for sub in sub_conditions:
            # Create a temporary rule with just this sub-condition
            temp_rule = {"conditions": sub}
            if _matches(temp_rule, triage, immunity, investigation, reputation):
                any_matched = True
                break
        if not any_matched:
            return False

    return True


def _compute_risk_score(
    triage: TriageResult,
    immunity: ImmunityCheckResult,
    investigation: Optional[InvestigatorResult],
    reputation: Optional[ReputationResult] = None,
) -> tuple[int, list[str]]:
    """
    Compute a composite risk score (0-100) from all pipeline signals.
    Returns (score, human-readable signal list) for dashboard display and logging.
    """
    score = 0
    signals: list[str] = []

    # Triage signals
    if triage.category == Category.SCAM:
        score += int(triage.confidence * 30)
        signals.append(f"triage=scam ({triage.confidence:.0%})")
    elif triage.category == Category.UNKNOWN:
        score += 15
        signals.append("triage=unknown")

    # Urgency signals
    if triage.urgency_signals:
        score += min(15, len(triage.urgency_signals) * 5)
        signals.append(f"{len(triage.urgency_signals)} urgency phrase(s)")

    # Credential demand signals
    cred_actions = {"share_otp": 20, "personal_info": 15, "click_link": 10, "pay_money": 20, "call_number": 5}
    if triage.entities.action_demanded in cred_actions:
        score += cred_actions[triage.entities.action_demanded]
        signals.append(f"demands {triage.entities.action_demanded}")

    # Investigation signals
    if investigation:
        score += int(investigation.threat_score * 0.3)
        if investigation.threat_score:
            signals.append(f"investigator threat {investigation.threat_score}/100")

    # Sender reputation
    if reputation and reputation.scam_count > 0:
        score += min(25, 10 + reputation.scam_count * 5)
        signals.append(f"sender has {reputation.scam_count} prior scam(s)")

    # Immunity (already known)
    if immunity.matched:
        score = max(score, 70)
        signals.append(f"immunity match ({immunity.match_vector or 'raw'}, {immunity.similarity_score:.0%})")

    return min(100, score), signals


async def evaluate_policy(
    triage: TriageResult,
    immunity: ImmunityCheckResult,
    investigation: Optional[InvestigatorResult],
    reputation: Optional[ReputationResult] = None,
) -> PolicyDecision:
    """
    Evaluate all rules top-to-bottom. Return the first match.
    Includes audit trail of all rules evaluated.
    """
    rules_evaluated = []
    risk_score, signals = _compute_risk_score(triage, immunity, investigation, reputation)

    for rule in _RULES:
        rule_id = rule.get("id", "unknown")
        matched = _matches(rule, triage, immunity, investigation, reputation)
        rules_evaluated.append(rule_id)

        if matched:
            action_str = rule.get("action", "escalate_mother")
            priority = rule.get("priority", 99)

            logger.info(
                f"⚖️  Policy: rule='{rule_id}' (priority={priority}) "
                f"action={action_str} | risk_score={risk_score} | "
                f"reason='{rule.get('reason', '')}' | "
                f"evaluated {len(rules_evaluated)} rules"
            )

            return PolicyDecision(
                action=PolicyAction(action_str),
                reason=rule.get("reason", ""),
                rule_matched=rule_id,
                rules_evaluated=rules_evaluated,
                escalation_target=rule.get("escalation_target"),
                chakshu_prefilled=rule.get("prepare_chakshu", False),
                risk_score=risk_score,
                signals=signals,
            )

    # Should never reach here (default_fallback always matches)
    return PolicyDecision(
        action=PolicyAction.ESCALATE_MOTHER,
        reason="No rule matched — defaulting to escalate.",
        rule_matched="none",
        rules_evaluated=rules_evaluated,
        risk_score=risk_score,
        signals=signals,
    )
