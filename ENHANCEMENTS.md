# Kavach v3 — "the most advanced agent" enhancements

This round turns Kavach from a strong fixed pipeline into a **reasoning agent** that
chooses its own actions, remembers senders, teaches the people it protects, and
closes the loop on human feedback — without changing the model backend (still
Azure OpenAI) and without destabilising the working demo path.

Every new capability is isolated and **fails safe**: if a new table, column, or the
agentic loop is unavailable, the pipeline degrades to exactly the previous v2
behaviour.

---

## 0. Built on the Strands Agents SDK

`app/strands_runtime.py` + per-agent Strands paths

All four reasoning agents run as `strands.Agent`:

| Agent | Strands usage |
|---|---|
| **Triage** | `agent.structured_output_async(_TriageLLMOut, …)` — typed classification |
| **Investigator** | `strands.Agent(model, tools=[@tool ×7])` — a real ReAct loop; the model picks tools, stops early |
| **Doer** | `strands.Agent` per task (bill / bank-rewrite / OTP / appointment / complaint) |
| **Educator** | `strands.Agent` completion — the plain-language "why" note |

The model provider is **Groq** (free) through Strands' OpenAI-compatible provider:
`openai/gpt-oss-120b` for the investigator, `openai/gpt-oss-20b` for the rest.
`LLM_PROVIDER` also accepts `openai` and `azure`; `LLM_FAST_MODEL` / `LLM_SMART_MODEL`
override the model ids. AgentCore/Bedrock are not used (not required by the brief).

**Verified end-to-end** (Groq + Supabase + local `fastembed`): triage classifies
via `structured_output_async`; the investigator runs a real `strands.Agent` tool
loop (7 tools, trace captured from `agent.messages`); a reworded scam variant with
a rotated URL is caught by the **canonical** immunity vector in ~5 s and
silent-killed while member 1's original took ~35 s with full investigation.

> gpt-oss note: these models emit their final answer as a synthetic `json` tool
> call — handled in both agent paths (`@tool(name="json")` / `submit_verdict`).
> `reasoning_effort=low` is set so short structured answers aren't truncated.

**Fallback ladder — never crashes:** `AGENT_RUNTIME=strands` is the default; if the
SDK can't import or an agent call throws, that agent silently drops to the
hand-rolled direct path. The investigator ladder is
`strands → agentic (hand-rolled loop) → fixed (deterministic fan-out)`, each rung
tagged `mode=fallback` on the audit event.

The **policy engine stays deterministic** — no agent, no LLM. *The agents
investigate; code decides.*

## 1. The Investigator ReAct loop

`app/agents/investigator_agent.py` + `app/agents/tools/registry.py`

The old investigator ran **all 10 tools on every URL, every time**. Now the agent
decides which tool to run next from what it's learned and stops the moment the
picture is clear.

- 7 forensic tools exposed as Strands `@tool`s (`check_threat_intelligence`
  batches the 4 blocklist feeds). Shared `InvestigationState` accumulator via a
  `ContextVar` so the dashboard still gets structured `DomainIntel`.
- Early-stop: a threat-intel hit ends the loop in **one** tool call. A young domain
  + credential-harvesting form ends it in three.
- Every `thought → tool_call → observation → verdict` step is recorded as an
  `AgentStep` (extracted from `agent.messages`) and rendered on the dashboard.
- Deterministic threat score still computed from accumulated intel and used as the
  verdict if parsing the agent's JSON verdict fails.

Result: fewer API calls, lower latency, and a verdict you can *watch the agent
reach* — the difference between "a classifier" and "an agent".

## 2. Per-stage tracing

`StageTrace` on every `AuditEvent` — wall-clock for `stage1_parallel`,
`investigator`, `doer`, `policy`, `complaint_prefill`, `educator`. The investigator
also reports `llm_calls` and the tools it actually used. Surfaced in the dashboard
event card next to the existing timing waterfall.

## 3. Sender reputation memory

`app/memory/reputation.py` + `sender_reputation` table

The `repeat_offender` rule used to fire on *any* scam. Now it's backed by real
memory: a normalised `sender_key` (`dlt:VM-HDFCBK` / `phone:98…` / `host:foo.xyz` /
`upi:x@y`) with `seen_count` / `scam_count` per group.

- Checked **in parallel** with triage and immunity (Stage 1).
- New policy conditions: `sender_repeat_offender`, `sender_prior_scam_count_gte`.
- `repeat_offender_silent_kill` — a sender the family already confirmed as a
  scammer 2+ times gets blocked automatically.
- Feedback loop: pressing **BLOCK** on WhatsApp calls `mark_confirmed_scam`, so the
  sender is a known offender for *everyone* from then on.

## 4. Doer expansion + real Chakshu / 1930

`app/agents/doer.py`

Two → five task types: `bill_due_date`, `bank_alert_rewrite`, **`otp_explainer`**,
**`appointment_reminder`**, **`complaint_prefill`**.

`prepare_complaint(...)` replaces the old `# TODO: Pre-fill Chakshu complaint`. It
drafts a structured `ComplaintDraft` (channel, victim, suspicious number/URLs,
auto-written narrative, portal link) and attaches it to the event. It is
**prepared, never submitted** — Kavach holds it for one human tap. Fired
automatically on `escalate_natu_urgent`, and on a `REPORT` reply.
New endpoint: `GET /complaint/{message_id}`.

## 5. Human replies correlated to the right escalation

`app/db/escalations.py` + `pending_escalations` table

The reply handler used to grab *"the most recent audit event in the whole
system"* — racy and cross-member. Now each outbound escalation records
`(to_number, message_id)`, and `BLOCK/SAFE/REPORT` resolves back to the exact
message. Falls back to the old behaviour if the table doesn't exist.

## 6. Educator agent

`app/agents/educator.py`

After a risky verdict, a cheap Haiku call writes a **2-sentence, plain-language
explanation in the recipient's own language** (English / Hindi / Hinglish) — what
the scam was and the one tell-tale sign. Rides on the event and the daily digest.
Time-boxed to 8s, never blocks. This is the "for humans" part: the protected
person learns the pattern.

## 7. Dual-vector immunity

`app/memory/immunity_ledger.py` + `match_immunity_canonical` RPC

The `canonical_embedding` (identifiers stripped → pitch-only) was designed in v2
but never searched. Now `check_immunity` runs **two** vector searches — raw and
canonical (higher bar, `IMMUNITY_CANONICAL_THRESHOLD=0.84`) — and takes the
stronger match. `ImmunityCheckResult.match_vector` tells you which fired; a
`canonical` hit is shown as "variant" on the dashboard. This is what catches a
reworded scam that rotates every URL, amount, and phone number.

## 8. Full-pipeline eval

`eval/pipeline_eval.py`

The v2 eval scored triage only. This runs triage → investigator → policy end to
end (no DB writes, no WhatsApp) and scores the **final policy action**:

- `action_accuracy`, `scam_caught`, `legit_preserved`
- `harmful_on_legit` — the count that embarrasses you on stage
- investigator efficiency: avg LLM calls, avg tools used, avg latency, mode split
- `--immunity` runs the **propagation benchmark**: member 1 (fresh, full
  investigation) vs member 2 (reworded variant, immunity short-circuit) with the
  speed-up printed.

---

## Deploy / migrate

1. **Run the schema** in the Supabase SQL editor:
   - Fresh project (or unsure): `backend/app/db/schema_full.sql` — base + v3, one
     idempotent script, safe to re-run.
   - Project that already has the v2 tables: `backend/app/db/schema_v3.sql` —
     additive only (new columns default-null, 2 tables, 1 RPC).

   `ERROR 42P01 relation "audit_events" does not exist` means you ran `schema_v3`
   on a project with no base schema — run `schema_full.sql` instead.

2. **New env vars** (all have safe defaults — see `backend/.env.example`):
   | var | default | effect |
   |---|---|---|
   | `AGENT_RUNTIME` | `strands` | `direct` skips the Strands SDK entirely |
   | `LLM_PROVIDER` | `groq` | `openai` / `azure` also supported |
   | `EMBED_PROVIDER` | `fastembed` | local 384-d embeddings, no API key |
   | `INVESTIGATOR_MODE` | `agentic` | with `AGENT_RUNTIME=strands` the ladder is strands→agentic→fixed; `fixed` forces the deterministic pipeline |
   | `INVESTIGATOR_MAX_STEPS` | `6` | ReAct loop ceiling |
   | `INVESTIGATOR_EARLY_STOP_SCORE` | `70` | score that nudges the loop to finish |
   | `REPUTATION_REPEAT_OFFENDER_THRESHOLD` | `2` | prior scams → auto-block |
   | `IMMUNITY_CANONICAL_THRESHOLD` | `0.84` | canonical-vector match bar |
   | `EDUCATION_NOTES_ENABLED` | `true` | educator agent on/off |

3. No new Python or npm dependencies. `frontend` builds unchanged (`vite build`).

## Demo script additions

| Beat | What to show |
|---|---|
| Investigation | Expand a scam card → **AGENT REASONING** trace: watch it call `check_threat_intelligence`, see the hit, and finish in one step. Badge reads `AGENTIC · 2 llm`. |
| Reputation | Send two scams from the same number, BLOCK both → third is killed by `repeat_offender_silent_kill`, header shows **REPEAT OFFENDER · 2×**. |
| Educator | Every scam card now carries a plain-Hindi "why this was a scam" line. |
| Immunity variant | `python -m eval.pipeline_eval --immunity` → member 2's reworded variant matches on the **canonical** vector in ~40ms. |
| Chakshu | Reply `REPORT` → `GET /complaint/{id}` returns the drafted 1930 complaint, "held for one-tap, never auto-filed". |

## Tests

`backend/tests/test_enhancements_offline.py` — 28 assertions, no network, stubs the
SDKs. Covers models, policy-engine reputation rules + regression, the agentic loop
control flow (early-stop, trace shape, no-URL short-circuit), and educator gating.
