"""
Kavach Evaluation Runner — measures triage accuracy on labeled dataset.

Metrics:
  - Precision, Recall, F1 per category
  - Overall accuracy
  - False positive rate on legitimate bank SMS (the metric that matters most)
  - Confusion matrix
  - Per-sample results for debugging

Usage:
  cd backend
  python -m eval.eval_runner
"""
import asyncio
import json
import time
import sys
import os
from pathlib import Path
from datetime import datetime

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.agents.triage import run_triage


DATASET_DIR = Path(__file__).parent / "dataset"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


def load_dataset() -> list[dict]:
    """Load all labeled messages from dataset directory."""
    messages = []

    scam_path = DATASET_DIR / "scam_messages.json"
    legit_path = DATASET_DIR / "legit_messages.json"

    if scam_path.exists():
        with open(scam_path) as f:
            scam_data = json.load(f)
            messages.extend(scam_data)
            print(f"📊 Loaded {len(scam_data)} scam messages")

    if legit_path.exists():
        with open(legit_path) as f:
            legit_data = json.load(f)
            messages.extend(legit_data)
            print(f"📊 Loaded {len(legit_data)} legitimate messages")

    print(f"📊 Total: {len(messages)} messages")
    return messages


async def evaluate_single(msg: dict, index: int, total: int) -> dict:
    """Run triage on a single message and compare with ground truth."""
    text = msg["text"]
    expected = msg["category"]
    sub_type = msg.get("sub_type", "unknown")

    t0 = time.monotonic()
    try:
        result = await run_triage(text)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        predicted = result.category.value
        confidence = result.confidence
        correct = predicted == expected

        status = "✅" if correct else "❌"
        print(
            f"  [{index+1}/{total}] {status} "
            f"expected={expected:15s} predicted={predicted:15s} "
            f"conf={confidence:.2f} time={elapsed_ms}ms "
            f"| {text[:60]}..."
        )

        return {
            "text": text[:100],
            "expected": expected,
            "predicted": predicted,
            "confidence": confidence,
            "correct": correct,
            "reasoning": result.reasoning,
            "sub_type": sub_type,
            "elapsed_ms": elapsed_ms,
            "language": result.language,
            "dlt_verified": result.dlt_verified,
        }
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        print(f"  [{index+1}/{total}] ⚠️  ERROR: {exc} | {text[:60]}...")
        return {
            "text": text[:100],
            "expected": expected,
            "predicted": "error",
            "confidence": 0.0,
            "correct": False,
            "reasoning": f"Error: {str(exc)[:100]}",
            "sub_type": sub_type,
            "elapsed_ms": elapsed_ms,
        }


def compute_metrics(results: list[dict]) -> dict:
    """Compute precision, recall, F1 per category + overall metrics."""
    categories = ["scam", "routine_admin", "personal", "unknown"]

    metrics = {"overall": {}, "per_category": {}, "confusion_matrix": {}}

    total = len(results)
    correct = sum(1 for r in results if r["correct"])
    metrics["overall"]["accuracy"] = round(correct / total, 4) if total > 0 else 0
    metrics["overall"]["total"] = total
    metrics["overall"]["correct"] = correct

    # Average time
    times = [r["elapsed_ms"] for r in results]
    metrics["overall"]["avg_time_ms"] = int(sum(times) / len(times)) if times else 0
    metrics["overall"]["max_time_ms"] = max(times) if times else 0
    metrics["overall"]["min_time_ms"] = min(times) if times else 0

    # Per-category metrics
    for cat in categories:
        tp = sum(1 for r in results if r["expected"] == cat and r["predicted"] == cat)
        fp = sum(1 for r in results if r["expected"] != cat and r["predicted"] == cat)
        fn = sum(1 for r in results if r["expected"] == cat and r["predicted"] != cat)
        tn = sum(1 for r in results if r["expected"] != cat and r["predicted"] != cat)

        precision = round(tp / (tp + fp), 4) if (tp + fp) > 0 else 0
        recall = round(tp / (tp + fn), 4) if (tp + fn) > 0 else 0
        f1 = round(2 * precision * recall / (precision + recall), 4) if (precision + recall) > 0 else 0

        metrics["per_category"][cat] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "support": tp + fn,
        }

    # Confusion matrix
    for expected_cat in categories:
        metrics["confusion_matrix"][expected_cat] = {}
        for predicted_cat in categories + ["unknown", "error"]:
            count = sum(
                1 for r in results
                if r["expected"] == expected_cat and r["predicted"] == predicted_cat
            )
            if count > 0:
                metrics["confusion_matrix"][expected_cat][predicted_cat] = count

    # Critical metric: False Positive Rate on legitimate bank SMS
    bank_messages = [r for r in results if r["expected"] == "routine_admin"]
    bank_false_positives = [r for r in bank_messages if r["predicted"] == "scam"]
    metrics["critical"] = {
        "bank_false_positive_rate": round(
            len(bank_false_positives) / len(bank_messages), 4
        ) if bank_messages else 0,
        "bank_false_positives": len(bank_false_positives),
        "bank_total": len(bank_messages),
    }

    return metrics


def format_report(metrics: dict, results: list[dict]) -> str:
    """Generate a human-readable markdown report."""
    lines = [
        "# Kavach Triage Evaluation Report",
        f"\n_Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_\n",
        "## Overall Metrics\n",
        f"| Metric | Value |",
        f"|---|---|",
        f"| **Accuracy** | **{metrics['overall']['accuracy']:.1%}** |",
        f"| Total Messages | {metrics['overall']['total']} |",
        f"| Correct | {metrics['overall']['correct']} |",
        f"| Avg Time | {metrics['overall']['avg_time_ms']}ms |",
        f"| Max Time | {metrics['overall']['max_time_ms']}ms |",
        "",
        "## ⚠️  Critical: Bank False Positive Rate\n",
        f"| Metric | Value |",
        f"|---|---|",
        f"| **False Positive Rate** | **{metrics['critical']['bank_false_positive_rate']:.1%}** |",
        f"| False Positives | {metrics['critical']['bank_false_positives']} / {metrics['critical']['bank_total']} |",
        "",
        "> This is the metric that matters most. A high rate means legitimate bank OTPs",
        "> and debit alerts are incorrectly flagged as scams.\n",
        "## Per-Category Metrics\n",
        "| Category | Precision | Recall | F1 | Support |",
        "|---|---|---|---|---|",
    ]

    for cat, m in metrics["per_category"].items():
        lines.append(
            f"| {cat} | {m['precision']:.1%} | {m['recall']:.1%} | "
            f"{m['f1']:.1%} | {m['support']} |"
        )

    # Errors
    errors = [r for r in results if not r["correct"]]
    if errors:
        lines.extend([
            "\n## Misclassifications\n",
            "| Expected | Predicted | Confidence | Message |",
            "|---|---|---|---|",
        ])
        for e in errors[:30]:
            text = e["text"][:60].replace("|", "\\|")
            lines.append(
                f"| {e['expected']} | {e['predicted']} | {e['confidence']:.2f} | {text}... |"
            )

    return "\n".join(lines)


async def main():
    print("=" * 70)
    print("🛡️  Kavach Triage Evaluation Runner")
    print("=" * 70)

    messages = load_dataset()
    if not messages:
        print("❌ No messages found in dataset/")
        return

    print(f"\n🔬 Running triage on {len(messages)} messages...\n")

    results = []
    for i, msg in enumerate(messages):
        result = await evaluate_single(msg, i, len(messages))
        results.append(result)
        # Small delay to avoid rate limiting
        await asyncio.sleep(0.1)

    print("\n" + "=" * 70)
    print("📊 Computing metrics...\n")

    metrics = compute_metrics(results)
    report = format_report(metrics, results)

    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    with open(RESULTS_DIR / f"eval_{timestamp}.json", "w") as f:
        json.dump({"metrics": metrics, "results": results}, f, indent=2)

    with open(RESULTS_DIR / f"eval_{timestamp}.md", "w") as f:
        f.write(report)

    # Also save latest
    with open(RESULTS_DIR / "latest.json", "w") as f:
        json.dump({"metrics": metrics, "results": results}, f, indent=2)

    with open(RESULTS_DIR / "latest.md", "w") as f:
        f.write(report)

    # Print summary
    print(report)
    print(f"\n📁 Results saved to eval/results/eval_{timestamp}.json")
    print(f"📁 Report saved to eval/results/eval_{timestamp}.md")


if __name__ == "__main__":
    asyncio.run(main())
