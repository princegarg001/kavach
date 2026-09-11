# Kavach — 10-Day Build Plan

**Agents for Humans hackathon · Good Neighbor track · Strands Agents SDK**

> An autonomous agent that protects a group of non-technical people from scams and handles their routine message admin. When one person is targeted by a new scam, everyone else in the group becomes immune within seconds.

**Tagline for the video:** herd immunity, for a family.

---

## 1. The scope contract

10 days is enough to win, but only if you cut. Read this section twice and do not violate it after Day 7.

### Shipping

| Component | Why it's in |
|---|---|
| WhatsApp intake + notify | One channel, both directions, zero app-store friction |
| SMS intake via off-the-shelf forwarder | You do not build an Android app. See §3 |
| Triage agent | Fast classification of everything that arrives |
| Investigator agent | Link forensics + sandboxed page detonation |
| **Immunity ledger** | The differentiator. Non-negotiable |
| Policy engine (deterministic) | Decides who gets asked. The brief's core question |
| Doer agent, exactly 2 task types | Proves it's an agent, not a filter |
| Audit dashboard | Doubles as your live demo link (scores higher) |
| Eval set with real numbers | Almost nobody will have this |

### Cut

- **Gmail intake.** Adds OAuth complexity, adds nothing to the demo.
- **Chrome extension / Facebook click interception.** Impossible or a project of its own.
- **Auto-submitting Chakshu and 1930 reports.** Pre-fill and hold for approval only. Auto-filing government complaints at volume is a real harm and judges will flag it. "Prepared, not submitted" is the stronger design anyway.
- **More than 2 routine task types.** Bill due-date extraction and bank-alert plain-language rewrite. Nothing else.
- **Auth, multi-tenancy, settings UI.** Hardcode the group. It's a demo.

---

## 2. What you already have

Reuse aggressively from MCP Guard. This is where your 10 days comes from.

| MCP Guard | Kavach |
|---|---|
| FastAPI backend | Same skeleton, same patterns |
| YAML rule engine | **Becomes the policy engine directly** |
| React + Tailwind frontend | Audit dashboard |
| Supabase | Fallback vector store if AgentCore Memory fights you |

Your rule engine is the single biggest head start. The escalation policy is exactly a YAML ruleset, and you've already built and debugged that pattern.

---

## 3. Architecture

```
   WhatsApp (Twilio sandbox)  ─┐
   SMS (forwarder app → POST) ─┼──▶  FastAPI ingest  ──▶  Strands graph
                                │                              │
                                │      ┌───────────────────────┤
                                │      ▼                       ▼
                                │  ① Triage  ──────────▶  Immunity check
                                │  (Haiku, fast)          (vector similarity)
                                │      │                       │
                                │      │  unknown              │ match → silent kill
                                │      ▼                       │
                                │  ② Investigator          ④ Immunity writer
                                │  (Sonnet + tools)        (signature → memory)
                                │      │                       ▲
                                │      ▼                       │
                                │  ③ Doer ──────────────────────┘
                                │  (routine tasks)
                                │      │
                                │      ▼
                                │  Policy engine (YAML, deterministic)
                                │      │
                                └──────┴──▶ silent │ act │ prepare │ escalate
                                                              │
                                                    WhatsApp → mother or Natu
```

### Intake — do not build an Android app

Use an existing open-source SMS forwarder (several on F-Droid POST incoming SMS to a webhook). Configure it, point it at your ingest endpoint, done in 30 minutes instead of 2 days. **State this plainly in your README.** Judges respect a builder who reuses infrastructure to spend time on the actual problem. They do not respect someone who burned three days on boilerplate.

WhatsApp via Twilio sandbox handles both the "forward me anything suspicious" path and every outbound notification.

### ① Triage agent

Claude Haiku via Bedrock. Runs on every inbound message. Outputs structured JSON:

```json
{
  "category": "scam | routine_admin | personal | unknown",
  "entities": {
    "amount": 4999,
    "sender_id": "VM-HDFCBK",
    "urls": ["http://hdfc-kyc-verify.xyz/login"],
    "deadline": "2026-09-04",
    "action_demanded": "click_link"
  },
  "confidence": 0.82
}
```

Must be fast and cheap. This runs on everything.

### ② Investigator agent

Claude Sonnet. Only runs on `unknown` or low-confidence `scam`. Tools:

| Tool | What it proves |
|---|---|
| RDAP domain age | Registered 4 days ago is the single strongest phishing signal |
| TLS cert age + issuer | Free cert issued yesterday |
| Redirect chain resolution | Where the shortlink actually lands |
| URLhaus / PhishTank lookup | Known-bad corroboration |
| Typosquat distance (`rapidfuzz`) | Edit distance against a bank/brand list |
| **AgentCore Browser detonation** | Screenshot the landing page in a sandbox |

The detonation is your video's best 10 seconds. A fake HDFC login page rendering inside your own dashboard beats any architecture slide.

**Fallback if AgentCore Browser costs you more than half a day:** headless Playwright in a container, screenshot only, never execute downloads. Ship the fallback rather than lose the feature.

### ③ Doer agent

Two task types only:

1. **Bill due dates.** Extract amount, biller, due date from legitimate messages. Schedule a reminder. Surface as a card.
2. **Bank alert rewrite.** Turn `Rs.2,499.00 debited A/c XX4417 UPI/P2M/5512...` into "₹2,499 paid to Swiggy at 8:14pm." Plain language for someone who finds bank SMS unreadable.

This is what stops judges saying "it's a classifier with push notifications." Do not skip it, and make sure it appears in the video.

### ④ Immunity ledger — the crown jewel

**This is the reason to build Kavach and not something else. If you run out of time, cut anything before you cut this.**

When a scam is confirmed for one enrolled person:

1. Extract a signature: sender pattern, domain fingerprint, and the **semantic shape** of the pitch ("urgency + KYC expiry + credential link")
2. Embed the message body
3. Write it to **AgentCore Memory, scoped to the group, not the individual**

Every subsequent inbound message across every member is vector-checked against that store *before* the Investigator runs.

**The point is catching variants, not duplicates.** Scammers rotate numbers and URLs constantly but reuse the pitch. Blocklists miss that. Similarity search doesn't. Say exactly this sentence in your video.

Instrument it so you can prove it on screen: log `investigation_time_ms` for member 1 versus member 2. First hit takes 8 seconds. Second takes 40 milliseconds.

### Policy engine — deterministic, not an LLM

Your YAML rule engine, ported. Say this out loud in the pitch:

> **The model investigates. Code decides.**

Judges notice when a builder refuses to let a model make irreversible calls. Most submissions will let the LLM decide everything.

---

## 4. Escalation policy

Put this table in your README. It is the direct answer to the brief's central line, *"only surfaces when there's a real decision to make."*

| Situation | Decided by | Action |
|---|---|---|
| Immunity match, high similarity | Nobody | Silent kill, logged |
| Known-bad domain, corroborated | Nobody | Silent kill, logged |
| Routine admin, reversible | Nobody | Agent acts, appears in digest |
| Unknown sender, no money or credentials at stake | **Mother** | Simple yes/no on WhatsApp |
| Money, credentials, or account access involved | **Natu** | Two-button escalation |
| Suspected active compromise | **Natu** | Escalate + revoke-sessions link prepared |
| Confirmed fraud, victim already engaged | **Natu** | Chakshu + 1930 complaint pre-filled, held for one-tap |

Two humans in the loop is your most unusual design decision. The person being protected and the person who decides are different people. Almost no other submission will have this. Lead with it.

---

## 5. Day by day

### Day 1 — The pipe
- FastAPI skeleton from MCP Guard, Bedrock access confirmed, `$50` credits requested
- Twilio WhatsApp sandbox live
- SMS forwarder app installed and POSTing
- One message goes in, one dumb classification comes out, one WhatsApp notification arrives
- **Gate: end-to-end path works. No intelligence yet. Do not proceed until this is true.**

### Day 2 — Triage + eval set
- Triage agent in Strands, structured output
- **Build the eval set today, not later.** 150 real scam messages (your family's phones, public datasets, Twitter) + 150 legitimate bank/OTP/delivery messages
- Baseline numbers written down

### Day 3 — Investigator
- All link-analysis tools: RDAP, cert, redirect chain, URLhaus, typosquat
- Strands graph wiring Triage → Investigator
- Re-run eval, record the delta

### Day 4 — Detonation + policy
- AgentCore Browser sandbox screenshot (timebox: 4 hours, then fall back to Playwright)
- Policy engine ported from your YAML rule engine
- All four ladder rungs firing correctly

### Day 5 — Immunity ledger
- Signature extraction + embedding
- AgentCore Memory, group-scoped
- Pre-investigation similarity check
- **Instrumented timing, both members, logged and visible**
- **Gate: two-person propagation demonstrably works. This is the day that decides your submission.**

### Day 6 — Doer + dashboard
- Bill extraction, bank-alert rewrite
- React audit dashboard: message stream, verdicts, screenshots, timing, "what the agent saw and did"
- Consent/enrollment screen (see §7)

### Day 7 — Deploy and freeze
- AgentCore Runtime, public live demo URL
- **FEATURE FREEZE, 6pm.** Anything broken after this gets cut, not fixed.

### Day 8 — Numbers and docs
- Final eval run, real precision/recall, **especially false-positive rate on legitimate bank SMS**
- README with the escalation table, the eval numbers, and honest limitations
- Architecture diagram (Excalidraw, 45 minutes, don't gold-plate)
- MIT license visible in the About section

### Day 9 — Video
- Script below. Record, re-record, cut to under 5:00
- Most submissions lose here. Budget the whole day.

### Day 10 — Submit + bonus
- Devpost submission, AWS Builder ID attached
- builder.aws.com post with "Agents for Humans" in the title (free bonus points, 90 minutes)
- Submit with hours to spare, not minutes

---

## 6. Demo video — 5 minute beat sheet

| Time | Beat |
|---|---|
| 0:00–0:30 | A **real** scam SMS on a real phone. No hypotheticals. "My mother got this last month." |
| 0:30–1:00 | Who it's for: the person in every family who is everyone else's tech support |
| 1:00–2:30 | Live: scam arrives → investigation runs → sandbox screenshot of the fake login page → escalates to *you*, not her → one tap |
| 2:30–3:15 | **The immunity moment.** Second member, reworded variant, different number. Killed in 40ms. Show both timestamps on screen. |
| 3:15–4:00 | Routine work: bill scheduled, bank alert rewritten. Proves it's not a filter. |
| 4:00–4:30 | Architecture, 20 seconds. Then: "the model investigates, code decides." |
| 4:30–5:00 | Consent and audit log. Why it matters. |

Use real scam messages throughout. Label anything synthetic. A demo that is 100% simulated reads as a simulation, and judges can tell.

---

## 7. Consent — do this on Day 6, not never

You are reading a parent's messages. Build:

- An explicit enrollment step where the protected person opts in
- A visible log of everything the agent saw and every action it took
- A kill switch

Judges **will** ask. Having a real answer scores points. Not having one costs more than the feature took to build.

---

## 8. Risks and fallbacks

| Risk | Fallback |
|---|---|
| AgentCore Browser eats a day | Headless Playwright screenshot |
| AgentCore Memory fights you | Supabase + pgvector, you already know it |
| Bedrock quota or region issues | Sort this on Day 1, not Day 7 |
| False positives on real bank SMS | This is the one that embarrasses you on stage. Measure it Day 2, tune Day 8 |
| Twilio sandbox rate limits | Pre-record the WhatsApp segments as backup footage |
| Scope creep | §1. Re-read it on Day 5 |

---

## 9. The one weakness, and its fix

Kavach can be read as a classifier with push notifications. That is the criticism that kills it.

The fix is already in the plan, in three places:

1. The **Doer** agent does end-to-end work, not just flagging
2. The **immunity ledger** makes it a system that gets stronger with each attack, not a filter
3. The **two-human escalation policy** makes "when to surface" a real design problem you solved, not an afterthought

Make sure all three are visible in the first three minutes of the video. If a judge only watches half of it, they still see them.