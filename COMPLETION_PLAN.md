# Kavach — Completion Plan (automatic intake + proper deploy)

Goal: a background agent that ingests a family member's messages **automatically**,
runs the Strands pipeline, and only surfaces when there's a real decision —
deployed on a stable public URL, hardened, documented, and submitted.

Legend: **[me]** = code I can do here · **[you]** = needs an account / phone / recording · **[both]**

Estimated total: ~4–5 focused days.

---

## Phase 0 — Harden the core (½ day) **[me]** — ✅ DONE

Make what already works safe to expose to the internet.
Shipped: `X-Kavach-Token` on `/ingest`, Twilio signature validation on `/whatsapp`
(`TWILIO_VALIDATE_SIGNATURE`), `message_id` dedup (`DEDUP_WINDOW_SECONDS`),
prod log truncation, `GET /intake/status` + dashboard "INTAKE" pill,
`PIPELINE_MODE` / `AGENTCORE_*` config hooks, graph docstring fix.
Verified: dedup drops the 2nd delivery, `/intake/status` flips connected on first
message, 28/28 offline tests still pass.

- [ ] **Webhook auth**
  - `/ingest`: require header `X-Kavach-Token: <INGEST_SECRET>`; reject otherwise. Add `INGEST_SECRET` to config.
  - `/whatsapp`: validate the `X-Twilio-Signature` header with `twilio.request_validator` against `TWILIO_AUTH_TOKEN`.
- [ ] **Dedup** — `/ingest` and `/whatsapp` drop a message whose `message_id` was seen in the last 10 min (in-memory TTL cache; the audit table is the durable check).
- [ ] **Prod safety** — when `APP_ENV=production`: `/test/message` already 404s; also stop logging full `raw_text` at INFO (truncate to 40 chars), lock `CORS_ORIGINS` to the deployed frontend origin only.
- [ ] **Secrets** — nothing real in `.env.example` (done); rotate the Groq key; all secrets go in the platform's env, never committed.
- [ ] **Cosmetic** — graph.py docstring says "Strands"; drop the stale `"unknown scam pattern"` immunity row.
- [ ] **Intake health** — new `GET /intake/status` → `{last_message_at, messages_last_hour, source_breakdown}`; dashboard shows a green/red "INTAKE CONNECTED · last msg 12s ago" pill.

Exit check: `curl` without the token is 401; with it, 202. Twilio signature rejection works.

---

## Phase 1 — Automatic intake — ✅ WhatsApp verified live

WhatsApp path **working end-to-end**: a message forwarded to the Twilio sandbox →
tunnel (`cloudflared`) → `/whatsapp` → full Strands pipeline → escalation card
delivered back to the phone with BLOCK/SAFE. Twilio creds + real numbers in `.env`,
`INGEST_SECRET` set.
Still open: (a) the SMS-forwarder app on an Android phone (the true zero-touch
channel — WhatsApp forwarding works but needs a human to forward); (b) confirm a
`BLOCK` reply writes the immunity signature + reputation.

---

## Phase 1 (reference) — Automatic intake **[both]**

The pipeline doesn't change — we feed it automatically. Two channels, both landing on the same `/ingest` contract.

### 1a. SMS — the truly zero-touch path **[you: 30 min]**
- [ ] Install an off-the-shelf SMS-forwarder on the protected phone (Android). Recommended: **"SMS Forwarder"** (F-Droid) or **"SMS to URL Forwarder"** — anything that does an HTTP POST per incoming SMS.
- [ ] Configure one rule: **all inbound SMS** → `POST https://<deployed>/ingest`
  - Headers: `Content-Type: application/json`, `X-Kavach-Token: <INGEST_SECRET>`
  - Body template:
    ```json
    {"member_id":"whatsapp:+9198XXXXXXXX","member_name":"Mummy","raw_text":"%message%","source":"sms","message_id":"%sentStamp%"}
    ```
  - (Field names vary by app — map `%from%` / `%message%` / timestamp accordingly.)
- [ ] Send yourself a test SMS → it appears on the dashboard within ~15s with no action taken.

This is the demo's honest centrepiece: *"We reused an SMS forwarder instead of building an Android app, so we spent the time on the agent."*

### 1b. WhatsApp — forward-suspicious + all outbound **[you: 20 min]**
- [ ] Twilio account → **WhatsApp sandbox**. Note `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, sandbox number.
- [ ] Sandbox settings → "When a message comes in" → `https://<deployed>/whatsapp` (POST).
- [ ] Join the sandbox from Natu's phone and Mother's phone (send the join code).
- [ ] Set `MOTHER_PHONE=whatsapp:+91…`, `NATU_PHONE=whatsapp:+91…`, `TWILIO_WHATSAPP_FROM=whatsapp:+14155238886`.
- [ ] End-to-end reply test: send a scam → Natu gets the escalation card → reply `BLOCK` → dashboard shows the human decision, immunity signature written, sender marked repeat-offender.

### 1c. (Optional, "most advanced") Email intake **[me + you: 1h]**
- [ ] Cloudflare Email Routing on a domain you own → **Email Worker** that parses the raw email and does `POST /ingest` with `source:"email"`. Zero server code, no OAuth. Add `"email"` to the `source` Literal.
- Alternative: SendGrid Inbound Parse / Mailgun route to a new `/ingest/email` endpoint.

Exit check: `GET /intake/status` shows messages arriving from `sms` (and `whatsapp`/`email`) with no manual paste.

---

## Phase 2 — Deploy properly (1–1.5 days) **[both]**

**Decision: AWS + AgentCore.** App Runner is the always-works fallback; AgentCore
Runtime is the target (timeboxed — quota/region can bite like Azure did).
LLM stays **Groq** — AgentCore Runtime hosts the agent code, it does not force a
Bedrock model.

### 2a. Prereqs **[you: 30 min]**
- [ ] AWS account with billing; request **$50 credits** (Phase 5 form) early.
- [ ] Pick a region AgentCore supports **and** where Bedrock is enabled — `us-west-2` or `us-east-1` are safest.
- [ ] `aws configure` locally; install `pip install bedrock-agentcore bedrock-agentcore-starter-toolkit`.
- [ ] Bedrock console → Model access → enable at least one Anthropic model (AgentCore's default provider; we still call Groq from our code, but access must be on for the runtime to start cleanly).

### 2b. Backend as an AgentCore Runtime agent — code ✅ DONE
`backend/agentcore_app.py` (the `@app.entrypoint` wrapper), `app/runtime_client.py`
(ingress → runtime via boto3), `main.py` branches on `PIPELINE_MODE`,
`requirements-agentcore.txt`. Default stays `PIPELINE_MODE=local` — nothing changes
until you launch. **You** still run `agentcore configure` / `launch` (§2c below).
- [ ] New `backend/agentcore_app.py`:
  ```python
  from bedrock_agentcore.runtime import BedrockAgentCoreApp
  from app.models import InboundMessage
  from app.agents.graph import run_kavach_pipeline
  app = BedrockAgentCoreApp()

  @app.entrypoint
  async def invoke(payload: dict):
      msg = InboundMessage(**payload)
      result = await run_kavach_pipeline(msg)
      return {k: (v.model_dump() if hasattr(v, "model_dump") else v) for k, v in result.items()}

  if __name__ == "__main__":
      app.run()
  ```
- [ ] `agentcore configure --entrypoint backend/agentcore_app.py` → generates the Dockerfile + `.bedrock_agentcore.yaml`.
- [ ] Put all env vars (Groq, Supabase, Twilio, `INGEST_SECRET`) in the AgentCore config / Secrets Manager.
- [ ] `agentcore launch` → builds the ARM container, pushes to ECR, deploys. Get the **runtime ARN**.
- [ ] Test: `agentcore invoke '{"member_id":"...","member_name":"Test","raw_text":"...","source":"sms"}'`.

### 2c. Thin ingress API in front of the runtime **[me + you]**
AgentCore Runtime is invoked via the AWS API, not a plain webhook — Twilio and the
SMS forwarder need a normal HTTPS endpoint. So keep a **small FastAPI app** whose
only job is: auth-check → call the AgentCore runtime (`bedrock-agentcore` client
`invoke_agent_runtime`) → persist + SSE. Deploy that thin app on **App Runner**.
- [ ] Strip `backend/app/main.py` to: `/ingest`, `/whatsapp`, `/whatsapp/reply`, `/events`, `/audit`, `/stats`, `/reputation`, `/complaint/{id}`, `/enroll`, `/unenroll`, `/health`, `/intake/status`. It calls the runtime instead of `run_kavach_pipeline` directly (feature-flag `PIPELINE_MODE=agentcore|local`).
- [ ] App Runner service from `backend/Dockerfile.ingress`, 1 vCPU / 1 GB (no chromium/fastembed here — those live in the runtime container).
- [ ] IAM: the App Runner instance role gets `bedrock-agentcore:InvokeAgentRuntime` on the runtime ARN.

### 2d. AgentCore extras (each ~2–4h, take what fits) **[me + you]**
- [ ] **AgentCore Browser** replaces Playwright for the sandboxed screenshot (plan.md's preferred option; frees the runtime container from chromium). Wrap it behind the existing `screenshot_page` tool.
- [ ] **AgentCore Observability** — enable in `agentcore configure`; agent spans + token counts land in CloudWatch GenAI Observability. Big Technical Implementation signal.
- [ ] **AgentCore Memory** — considered for the immunity ledger; we keep the working dual-vector Supabase impl and note the trade-off in the README.

### 2e. Frontend — S3 + CloudFront **[both]**
- [ ] `cd frontend && VITE_API_URL=https://<apprunner-ingress-url> npm run build`
- [ ] `aws s3 sync dist s3://<bucket>` → CloudFront distribution (HTTPS, SPA fallback to `index.html`, `default_root_object=index.html`).

### 2f. Database — Supabase **[you: 5 min]**
- [ ] Run `backend/app/db/schema_full.sql` on the prod project (idempotent, `vector(384)`).

### 2g. Wire + smoke test **[you]**
- [ ] Twilio "when a message comes in" → `https://<ingress>/whatsapp`
- [ ] SMS forwarder → `https://<ingress>/ingest` with `X-Kavach-Token`
- [ ] Real SMS from the phone → CloudFront dashboard shows the event → scam → Natu WhatsApp → `BLOCK` reply → decision + immunity + reputation update.

### §2-alt — Pure App Runner (no AgentCore), if AgentCore quota/region blocks you
Deploy `backend/` (full app, `PIPELINE_MODE=local`) straight to App Runner with
`RUN playwright install --with-deps chromium` in the Dockerfile, 1 vCPU / 2 GB.
Everything already works this way locally. Note "AgentCore attempted, blocked by
X" in the README — still a legitimate submission.

### §2-alt-2 — Render (fastest, if AWS itself blocks you)
`render.yaml` is written. New → Blueprint → set `sync:false` secrets → Static Site
for the frontend → UptimeRobot ping `/health` every 10 min to beat the sleep.

### Backend — AWS App Runner **[me: config · you: console]**
- [ ] `backend/Dockerfile` already builds the FastAPI app (which contains the Strands agents). Add `RUN playwright install --with-deps chromium` to it.
- [ ] Push the repo to GitHub. App Runner → "Source: GitHub" → `backend/` → auto-build from Dockerfile.
- [ ] Service env vars (App Runner console, all as plain env or Secrets Manager refs):
  `AGENT_RUNTIME=strands`, `LLM_PROVIDER=groq`, `GROQ_API_KEY`, `LLM_FAST_MODEL=openai/gpt-oss-20b`, `LLM_SMART_MODEL=openai/gpt-oss-120b`, `EMBED_PROVIDER=fastembed`, `EMBED_DIM=384`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_ANON_KEY`, `TWILIO_*`, `MOTHER_PHONE`, `NATU_PHONE`, `INGEST_SECRET`, `APP_ENV=production`, `CORS_ORIGINS=https://<frontend-domain>`.
- [ ] Health check path `/health`. Instance: 1 vCPU / 2 GB (fastembed + chromium need the RAM).
- [ ] First boot downloads the fastembed model (~90 MB) — warm it with one `/ingest` call after deploy.

### Frontend — S3 + CloudFront (or AWS Amplify) **[both]**
- [ ] `cd frontend && VITE_API_URL=https://<apprunner-url> npm run build`
- [ ] Upload `dist/` to an S3 bucket; put CloudFront in front (HTTPS, SPA fallback to `index.html`).
- [ ] Or: Amplify Hosting → connect GitHub → build command `npm run build`, output `dist`, env `VITE_API_URL`.

### Database — Supabase (already hosted) **[you: 5 min]**
- [ ] On the production Supabase project, run `backend/app/db/schema_full.sql` once (idempotent; `vector(384)`).
- [ ] Confirm: `select count(*) from audit_events;` works.

### Wire the public URL back **[you]**
- [ ] Twilio webhook → `https://<apprunner-url>/whatsapp`
- [ ] SMS forwarder → `https://<apprunner-url>/ingest`
- [ ] Deployed smoke test: real SMS from the phone → dashboard event → (if scam) Natu WhatsApp → reply → decision recorded.

### Stretch — AgentCore Runtime **[me + you, if time]**
Host the Strands agents on **Bedrock AgentCore Runtime** and have the FastAPI app call them. The brief: *"Deploying with AgentCore … will strengthen your Technical Implementation score."* Timebox 4h; if AgentCore quota/region fights you (like Azure did), stay on App Runner and note it in the README.

### §2-alt — Render (fastest, free-ish) **[you: 20 min]**
`render.yaml` is already written. Render → "New → Blueprint" → point at the repo → set the `sync:false` secrets in the dashboard. Frontend as a Render Static Site (`npm run build`, publish `dist`). Free web service sleeps after 15 min idle — add a Render Cron or UptimeRobot ping to `/health` every 10 min, or use the $7 tier.

---

## Phase 2.9 — Passive multi-channel intake

**Full click-by-click guide: `INTAKE_SETUP.md`.**

Status:
- **Email — ✅ code done & verified.** IMAP poller (`app/email_intake.py`, backend-
  only, no OAuth — just a Gmail App Password + `EMAIL_INTAKE_ENABLED=true`) **and**
  a `POST /ingest/email` webhook for the filter-forward path (Cloudflare / SendGrid
  / Mailgun). `source: "email"` added to the model. Tested: a posted email showed
  `by_source: {"email": 1}` and ran the pipeline.
- **SMS / WhatsApp — backend ready, needs an Android app you install.** `/ingest`
  already accepts `source: sms|whatsapp` with token auth + dedup. See
  `INTAKE_SETUP.md §1` (SMS forwarder) and `§2B` (Notification Listener).

All three land on the same pipeline, differing only in `source`.

### SMS (Android, fully passive)  **[you, 15 min]**
- Install **"SMS Forwarder"** (F-Droid) / **"SMS to URL Forwarder"** (Play) on the protected phone.
- Rule: any inbound SMS → HTTP POST to `https://<deployed>/ingest`
  - headers: `Content-Type: application/json`, `X-Kavach-Token: <INGEST_SECRET>`
  - body: `{"member_id":"whatsapp:+91…","member_name":"Mummy","raw_text":"%message%","source":"sms","message_id":"%sentDate%-%from%"}`
- Nobody forwards anything — every SMS the phone receives is checked.

### WhatsApp (Android, passive via notifications)  **[you, 15 min] — optional**
- No API reads a full WhatsApp inbox. Use an **Android Notification Listener**
  forwarder ("Notification Forwarder", "AutoNotification + Tasker", etc.).
- Grant `BIND_NOTIFICATION_LISTENER_SERVICE`. Rule: notifications from
  `com.whatsapp` → POST to `/ingest` with `"source":"whatsapp"`, `raw_text` = the
  notification body.
- Caveat: long messages are truncated in the notification; grey-area re: WhatsApp ToS
  (reading your own device's notifications for personal use is generally fine).
- The Twilio "forward suspicious here" path stays as the primary, compliant channel.

### Gmail (fully official)  **[me + you, ~1 h] — optional**
Pick one:
1. **Filter-forward + inbound parse** — Gmail filter forwards all mail to
   `kavach@<yourdomain>`; Cloudflare Email Routing → Email Worker → `POST /ingest`
   `"source":"email"`. No OAuth. (I write the Worker.)
2. **IMAP poll** — a small backend loop logs in with a Gmail **app password**,
   polls unread every 2 min, feeds new mail to the pipeline. Backend-only, no OAuth.
3. **Gmail API + Pub/Sub push** — OAuth once → `users.watch()` → Google pushes
   new-mail events → webhook fetches + `/ingest`. Most "proper", most setup.

Add `"email"` to the `source` Literal in `app/models.py` when any email path lands.

**Exit:** `/intake/status` `by_source` shows `sms` (+ optionally `whatsapp`, `email`)
with zero manual forwarding.

---

## Phase 3 — "Most advanced" polish — ✅ MOSTLY DONE

Done this pass:
- **UI overhaul** — new light design system (`index.css` / `App.css`), rebuilt
  `Dashboard` + `EventCard`. The **agent trace animates**: a rail draws down, each
  thought / tool_call / observation / verdict node pops in with a stagger, tool
  calls get a one-shot "running" sweep. Soft-shadow cards, pill badges, count-up
  stats, dark-mode fallback.
- **Screenshot** rendered in the expanded card (base64 → `<img>`).
- **Stage-trace bar** — per-stage latency as a coloured bar under every card.
- **Reputation** — repeat-offender badge on cards; `/reputation` endpoint.
- **Daily digest** — `app/digest.py` + APScheduler job at `DIGEST_HOUR` IST to
  Mother + Natu; `POST /digest/send` on-demand. Verified: job scheduled at boot.
- **Consent + kill switch** — `/consent` endpoint; `enrolled_members` gains
  `consent_scope` + `paused_at`; the demo group is auto-seeded at startup.
  Dashboard has a **Consent & control** panel with a per-member Kill switch / Resume
  toggle and a "Monitoring paused" banner. The pause genuinely gates `process_message`.

Deferred (low value / do only with spare time):
- Speed micro-tuning — latency doesn't matter for a background agent.
- Strands OTel span export.
- A `demo/` script that hits a real known-bad URL end to end.

<details><summary>original task list</summary>

- [ ] **Speed** (currently 14–35 s). Target < 10 s typical:
  - Investigator on `gpt-oss-20b` (from 120b) or cut `INVESTIGATOR_MAX_STEPS` to 4.
  - Tighten the investigator system prompt so it stops in 2–3 tool calls (it currently sometimes runs 7 and repeats a tool).
  - Add an `INVESTIGATOR_PARALLEL_TOOLS` hint; batch the first 3 checks.
  - Triage: offer a `TRIAGE_MODE=direct` that skips Strands `structured_output` for the faster JSON path (keep Strands as default for the story).
- [ ] **Daily digest** — APScheduler (already a dep) fires `send_digest` at `DIGEST_HOUR` to Mother + Natu: counts, blocked scams, immunity hits, Doer actions, education notes.
- [ ] **Dashboard**:
  - Reputation panel (endpoint `/reputation` exists) — table of known senders, scam counts, repeat-offender flags.
  - Stage-trace panel — the `stage_traces` breakdown as a bar (replace/augment the old waterfall).
  - Render the investigation **screenshot** (`investigation.screenshot_path` is base64) in the expanded card.
  - Immunity timeline chart (member-1 vs member-2 ms) using the `recharts` dep already installed.
- [ ] **Consent / kill switch (brief §7 — judges will ask)**:
  - `/enroll` writes a consent record with timestamp + the exact scope ("reads forwarded SMS/WhatsApp").
  - Kill switch: `_is_enrolled` already gates the pipeline — make `/unenroll` also stop the digest and show a clear "MONITORING PAUSED" state on the dashboard.
  - "Everything the agent saw and did" = the audit log; add a per-member filtered view.
- [ ] **Observability** — Strands ships OpenTelemetry hooks; export agent spans to console (or an OTLP endpoint). Surface `llm_calls` + token counts per stage on the card.
- [ ] **Investigator realism** — add one **known-bad test URL** path (e.g. a URLhaus sample) to a `demo/` script so the tools demonstrably fire, screenshot lands, verdict = `scam` with confidence ≥ 0.9.

</details>

---

## Phase 4 — Eval + docs (½ day) **[me]**

- [ ] `python -m eval.eval_runner` — triage precision/recall/F1 on the 250-message set.
- [ ] `python -m eval.pipeline_eval --limit 60 --immunity` — end-to-end action accuracy + the propagation benchmark. Groq free tier ≈ 30 rpm, so `--limit` + the built-in sleeps.
- [ ] Record the headline numbers, **especially false-positive rate on legitimate bank SMS**.
- [ ] **README**: one-paragraph pitch · the escalation table (plan.md §4) · architecture diagram (Excalidraw, 45 min) · eval numbers · honest limitations · MIT license · "consent, audit log, kill switch" section.
- [ ] Move `ENHANCEMENTS.md` content into the README's "How it's built" section.

---

## Phase 5 — Submission (½ day) **[you]**

- [ ] **Demo video** ≤ 5 min, plan.md §6 beat sheet. Real scam messages throughout; label anything synthetic. Show: real SMS arrives automatically → investigation trace → Natu escalation → one tap → **the immunity moment** (member 2, reworded, killed in seconds) → a Doer action → "agents investigate, code decides" → consent + audit log.
- [ ] **Devpost** submission with AWS Builder ID attached; pick the track (**Good Neighbor** — it protects a group).
- [ ] **builder.aws.com** post with "Agents for Humans" in the title (bonus points).
- [ ] Request the **$50 AWS credits** (Resources tab form) — ideally before Phase 2.
- [ ] Submit with hours to spare.

---

## Dependency / ownership summary

| Needs you (accounts / hardware / recording) | I can do (code) |
|---|---|
| Twilio account + sandbox join | Phase 0 hardening |
| A spare Android phone + SMS-forwarder app | `/intake/status` + dashboard pill |
| GitHub repo pushed | Speed pass, digest, dashboard panels |
| AWS account (App Runner / S3 / CloudFront) or Render | Consent/kill-switch gating |
| Rotate the Groq key | Eval runs + README draft |
| Record the video, Devpost, builder.aws post | AgentCore adapter (stretch) |

## Suggested order for the next session

1. Phase 0 (I do it now) → 2. you create Twilio + get a phone with the forwarder → 3. Phase 2 deploy (Render first for speed, App Runner if time) → 4. wire webhooks, verify automatic intake on the deployed URL → 5. Phase 3 polish → 6. Phase 4 eval + README → 7. Phase 5 video + submit.
