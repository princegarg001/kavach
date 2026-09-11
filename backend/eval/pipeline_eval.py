"""
Kavach full-pipeline evaluation — measures end-to-end decisions, not just triage.

For every labelled message it runs triage -> (investigator if needed) -> policy,
exactly as the graph does, but with no DB writes and no WhatsApp sends. It then
scores the final POLICY ACTION against what a human would want, and reports how
efficient the agentic investigator was (tool calls, LLM round-trips, latency).

Usage:
  cd backend
  python -m eval.pipeline_eval                 # full dataset
  python -m eval.pipeline_eval --limit 40      # quick pass
  python -m eval.pipeline_eval --immunity      # + immunity propagation benchmark
"""
import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.agents.triage import run_triage
from app.agents.investigator import run_investigator
from app.policy.engine import evaluate_policy
from app.models import Category, ImmunityCheckResult

DATASET_DIR = Path(__file__).parent / "dataset"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# What a good outcome looks like per ground-truth label.
GOOD_ACTIONS = {
    "scam": {"silent_kill", "escalate_natu", "escalate_natu_urgent", "escalate_mother"},
    "routine_admin": {"act"},
    "personal": {"act"},
}
# Actions that are actively wrong for a legit message (the embarrassing failure).
HARMFUL_FOR_LEGIT = {"silent_kill", "escalate_natu_urgent"}


def load_dataset(limit: int | None):
    msgs = []
    for name in ("scam_messages.json", "legit_messages.json"):
        p = DATASET_DIR / name
        if p.exists():
            msgs.extend(json.loads(p.read_text(encoding="utf-8")))
    if limit:
        # interleave so a short run still covers both classes
        scam = [m for m in msgs if m["category"] == "scam"][: limit // 2]
        rest = [m for m in msgs if m["category"] != "scam"][: limit - len(scam)]
        msgs = scam + rest
    return msgs


async def run_one(msg: dict) -> dict:
    text = msg["text"]
    expected = msg["category"]
    t0 = time.monotonic()

    triage = await run_triage(text)
    investigation = None
    if triage.category in (Category.SCAM, Category.UNKNOWN) or triage.confidence < 0.75:
        investigation = await run_investigator(text, triage.entities)

    policy = await evaluate_policy(
        triage=triage,
        immunity=ImmunityCheckResult(matched=False),
        investigation=investigation,
        reputation=None,
    )
    elapsed = int((time.monotonic() - t0) * 1000)

    action = policy.action.value
    good = action in GOOD_ACTIONS.get(expected, set())
    harmful = expected != "scam" and action in HARMFUL_FOR_LEGIT

    return {
        "text": text[:90],
        "expected": expected,
        "sub_type": msg.get("sub_type"),
        "triage_category": triage.category.value,
        "action": action,
        "rule": policy.rule_matched,
        "risk_score": policy.risk_score,
        "good": good,
        "harmful_for_legit": harmful,
        "investigated": investigation is not None,
        "inv_mode": investigation.mode.value if investigation else None,
        "inv_llm_calls": investigation.llm_calls if investigation else 0,
        "inv_tools": len(investigation.tools_used) if investigation else 0,
        "inv_ms": investigation.investigation_time_ms if investigation else 0,
        "total_ms": elapsed,
    }


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    good = sum(r["good"] for r in rows)
    legit = [r for r in rows if r["expected"] != "scam"]
    scam = [r for r in rows if r["expected"] == "scam"]
    inv = [r for r in rows if r["investigated"]]

    def avg(xs):
        return round(sum(xs) / len(xs), 1) if xs else 0

    return {
        "total": n,
        "action_accuracy": round(good / n, 4) if n else 0,
        "scam_caught": round(sum(r["good"] for r in scam) / len(scam), 4) if scam else 0,
        "legit_preserved": round(sum(r["good"] for r in legit) / len(legit), 4) if legit else 0,
        "harmful_on_legit": sum(r["harmful_for_legit"] for r in legit),
        "legit_total": len(legit),
        "investigator": {
            "ran": len(inv),
            "avg_llm_calls": avg([r["inv_llm_calls"] for r in inv]),
            "avg_tools_used": avg([r["inv_tools"] for r in inv]),
            "avg_ms": avg([r["inv_ms"] for r in inv]),
            "modes": {m: sum(1 for r in inv if r["inv_mode"] == m) for m in {r["inv_mode"] for r in inv}},
        },
        "avg_total_ms": avg([r["total_ms"] for r in rows]),
    }


async def immunity_benchmark():
    """Fresh scam vs reworded variant — the herd-immunity money shot."""
    from app.config import settings
    from app.memory.immunity_ledger import check_immunity, write_immunity

    original = ("Dear Customer, your SBI account KYC has expired. Verify now at "
               "http://sbi-kyc-verify.xyz/login within 24 hours or your account will be blocked.")
    variant = ("SBI alert: KYC verification pending. Update immediately via "
              "http://sbi-secure-kyc.info/renew or account access will be suspended today.")

    print("\n── Immunity propagation benchmark ───────────────────────────────")
    t = time.monotonic()
    triage = await run_triage(original)
    inv = await run_investigator(original, triage.entities)
    member1_ms = int((time.monotonic() - t) * 1000)
    print(f"  Member 1 (fresh): investigate={inv.investigation_time_ms}ms total={member1_ms}ms verdict={inv.verdict}")

    await write_immunity(original, settings.GROUP_ID, "member-1", triage.entities, inv)
    await asyncio.sleep(1.0)  # let the write land

    t = time.monotonic()
    hit = await check_immunity(variant, settings.GROUP_ID)
    member2_ms = int((time.monotonic() - t) * 1000)
    print(f"  Member 2 (variant): immunity_matched={hit.matched} "
          f"vector={hit.match_vector} similarity={hit.similarity_score:.3f} in {member2_ms}ms")
    if hit.matched and member1_ms:
        print(f"  ⚡ {member1_ms / max(member2_ms, 1):.0f}x faster — variant killed without investigation")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--immunity", action="store_true")
    args = ap.parse_args()

    msgs = load_dataset(args.limit)
    print(f"🛡️  Full-pipeline eval on {len(msgs)} messages\n")

    rows = []
    for i, m in enumerate(msgs):
        r = await run_one(m)
        rows.append(r)
        flag = "✅" if r["good"] else ("⚠️ " if r["harmful_for_legit"] else "❌")
        print(f"  [{i+1}/{len(msgs)}] {flag} {r['expected']:13s} -> {r['action']:20s} "
              f"({r['rule']}) inv={r['inv_llm_calls']}llm/{r['inv_tools']}t {r['total_ms']}ms")
        await asyncio.sleep(0.05)

    summary = summarize(rows)
    print("\n" + "=" * 64)
    print(json.dumps(summary, indent=2))
    print("=" * 64)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    (RESULTS_DIR / f"pipeline_{ts}.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    (RESULTS_DIR / "pipeline_latest.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    print(f"\n📁 eval/results/pipeline_{ts}.json")

    if args.immunity:
        await immunity_benchmark()


if __name__ == "__main__":
    asyncio.run(main())
