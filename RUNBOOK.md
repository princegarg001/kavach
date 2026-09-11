# Kavach — Step-by-step runbook

Expands `COMPLETION_PLAN.md` into an exact, do-it-in-order checklist.
**[YOU]** = you do it · **[ME]** = ask me and I'll write the code · **[BOTH]**

Repo layout: `backend/` (FastAPI + Strands agents), `frontend/` (React dashboard),
`backend/app/db/schema_full.sql` (run once in Supabase).

---

## PHASE 0 — Harden the core ✅ DONE (verify only)

Nothing to build. Confirm it still runs:

```powershell
cd C:\Users\princ\kavach\backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --port 8000
```
In another terminal:
```powershell
curl http://localhost:8000/health          # {"status":"ok",...}
curl http://localhost:8000/intake/status   # {"connected":false,...} before any message
```
Send one test message from the dashboard (`npm run dev` in `frontend/`), then
`curl http://localhost:8000/intake/status` again → `"connected":true`, `by_source:{"sms":1}`.

**Done when:** health ok, a pasted message shows on the dashboard with an AGENT
REASONING trace, and the INTAKE pill in the header turns green.

---

## PHASE 1 — Automatic intake (≈1 day)

Goal: messages reach `/ingest` **without you pasting anything**. Two channels.
Both need a public HTTPS URL — for local testing use a tunnel; in production it's
your deployed URL from Phase 2.

### Step 1.1 — Pick an INGEST_SECRET  **[YOU, 1 min]**
Generate a random string and put it in `backend/.env`:
```
INGEST_SECRET=kavach_7f3c9a1e5b2d8064
```
Restart the backend. Now `/ingest` needs header `X-Kavach-Token: kavach_7f3c9a1e5b2d8064`.
Verify it's enforced:
```powershell
curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d "{}"
# 401 in production; in dev (APP_ENV=development) it still 202s unless the secret is set
```

### Step 1.2 — Local public URL for testing  **[YOU, 5 min]**
Install a tunnel (skip if you'll deploy first and test on the live URL):
```powershell
winget install Cloudflare.cloudflared
cloudflared tunnel --url http://localhost:8000
```
Copy the `https://xxxx.trycloudflare.com` URL it prints. That's `<PUBLIC_URL>` below.
(ngrok works too: `ngrok http 8000`.)

### Step 1.3 — SMS intake (the zero-touch channel)  **[YOU, 30 min] — DEFERRED**
> Do this AFTER Phase 2, once the deployed HTTPS URL exists (not the temporary
> tunnel). Passive WhatsApp (Notification Listener) and Gmail intake are also
> deferred — see `COMPLETION_PLAN.md` "Phase 2.9 — Passive multi-channel intake".
1. On an Android phone (a spare / the parent's), install an SMS-forwarder from
   F-Droid or Play Store. Good ones: **"SMS Forwarder"** (by ~enachb) or
   **"SMS to URL Forwarder"** (by fbree04).
2. Add ONE rule: trigger = *any incoming SMS*, action = HTTP POST.
3. Configure the request:
   - URL: `<PUBLIC_URL>/ingest`
   - Method: `POST`
   - Headers:
     ```
     Content-Type: application/json
     X-Kavach-Token: kavach_7f3c9a1e5b2d8064
     ```
   - Body (use the app's placeholder tokens — names vary):
     ```json
     {"member_id":"whatsapp:+919812345678","member_name":"Mummy","raw_text":"%message%","source":"sms","message_id":"%receivedTimestamp%-%sender%"}
     ```
     - `%message%` → SMS text · `%sender%` → sender number · timestamp → any unique id
     - `member_id` = the protected person's number (any stable string is fine)
4. Send yourself an SMS from another phone.

**Done when:** `curl <PUBLIC_URL>/intake/status` shows `by_source:{"sms":N}` and the
message appears on the dashboard within ~15 s, with no action from you.

### Step 1.4 — Twilio WhatsApp (forward-suspicious + all outbound)  **[YOU, 20 min]**
1. Create a free account at twilio.com. Console home shows **Account SID** + **Auth Token**.
2. Left nav → **Messaging → Try it out → Send a WhatsApp message** → the **Sandbox**.
   - Note the sandbox number (e.g. `+1 415 523 8886`) and the **join code** (`join xxxx-yyyy`).
   - From Mother's phone AND Natu's phone, WhatsApp that join code to the sandbox number.
3. In the Sandbox **Settings** tab:
   - "When a message comes in" → `<PUBLIC_URL>/whatsapp`  (HTTP POST)
4. Put in `backend/.env`:
   ```
   TWILIO_ACCOUNT_SID=ACxxxxxxxx...
   TWILIO_AUTH_TOKEN=xxxxxxxx...
   TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
   MOTHER_PHONE=whatsapp:+919812345678
   NATU_PHONE=whatsapp:+919898765432
   TWILIO_VALIDATE_SIGNATURE=true
   ```
   Restart the backend.
5. From Mother's phone, forward a scam message to the sandbox number.

**Done when:** the message runs the pipeline; if it's a scam, **Natu's** phone gets
the escalation card; replying `BLOCK` marks it on the dashboard, writes an immunity
signature, and flags the sender as a repeat offender.

### Step 1.5 (optional) — Email intake  **[ME + YOU, 1 h]**
Only if you own a domain. Cloudflare → Email Routing → Email Worker that parses the
mail and does `POST /ingest` with `"source":"email"`. Tell me and I'll write the Worker.

**Phase 1 exit:** `/intake/status` shows real traffic from `sms` (and `whatsapp`)
with zero manual input.

---

## PHASE 2 — Deploy (AWS + AgentCore, ≈1–1.5 days)

Order: prereqs → deploy the pipeline as an AgentCore runtime → deploy a thin
ingress API on App Runner → deploy the frontend → wire webhooks. If AgentCore
fights you, jump to **§2-ALT** (pure App Runner) — still a valid submission.

### Step 2.1 — AWS prereqs  **[YOU, 30–60 min]**
1. AWS account with **billing enabled**. Submit the Devpost **$50 credits** form now (Resources tab) — it can take a day.
2. Create an IAM user with programmatic access (or use IAM Identity Center). Locally:
   ```powershell
   aws configure   # key, secret, region = us-west-2, output = json
   aws sts get-caller-identity   # confirms auth
   ```
3. **Bedrock → Model access** (in `us-west-2`) → request access to **Anthropic Claude 3.5 Haiku** (approves in minutes). We still call Groq for inference; AgentCore's control plane wants Bedrock access enabled.
4. Install the toolkit:
   ```powershell
   cd C:\Users\princ\kavach\backend
   .\.venv\Scripts\Activate.ps1
   pip install bedrock-agentcore bedrock-agentcore-starter-toolkit
   ```

### Step 2.2 — Wrap the pipeline as an AgentCore entrypoint  **[ME]**
Ask me to create `backend/agentcore_app.py`:
```python
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from app.models import InboundMessage
from app.agents.graph import run_kavach_pipeline

app = BedrockAgentCoreApp()

@app.entrypoint
async def invoke(payload: dict):
    result = await run_kavach_pipeline(InboundMessage(**payload))
    return {k: (v.model_dump() if hasattr(v, "model_dump") else v) for k, v in result.items()}

if __name__ == "__main__":
    app.run()
```
I'll also add `backend/requirements-agentcore.txt` and make `main.py` able to call
the runtime (`PIPELINE_MODE=agentcore`).

### Step 2.3 — Launch the runtime  **[YOU, 30 min]**
```powershell
cd C:\Users\princ\kavach\backend
agentcore configure --entrypoint agentcore_app.py --name kavach
# answer prompts: region us-west-2, let it create the execution role + ECR repo
```
Edit the generated `.bedrock_agentcore.yaml` to add env vars (or use Secrets
Manager): `AGENT_RUNTIME`, `LLM_PROVIDER=groq`, `GROQ_API_KEY`, `LLM_FAST_MODEL`,
`LLM_SMART_MODEL`, `EMBED_PROVIDER=fastembed`, `EMBED_DIM=384`, `SUPABASE_URL`,
`SUPABASE_SERVICE_KEY`, `SUPABASE_ANON_KEY`, `INGEST_SECRET`, `APP_ENV=production`.
```powershell
agentcore launch          # builds ARM container, pushes to ECR, deploys
agentcore invoke '{"member_id":"t","member_name":"Test","raw_text":"Your SBI KYC expired http://sbi-kyc-verify.top/login","source":"sms"}'
```
Copy the **runtime ARN** from the output.

### Step 2.4 — Thin ingress API on App Runner  **[ME + YOU]**
- **[ME]** split `main.py` so `process_message` calls the runtime when
  `PIPELINE_MODE=agentcore` (webhooks + SSE + audit stay here; the heavy pipeline
  + chromium + fastembed live in the runtime container). Add `Dockerfile.ingress`.
- **[YOU]** AWS console → **App Runner → Create service**:
  - Source: your GitHub repo, branch, `backend/` dir, `Dockerfile.ingress`
  - Env vars: `PIPELINE_MODE=agentcore`, `AGENTCORE_RUNTIME_ARN=<arn>`,
    `AGENTCORE_REGION=us-west-2`, `INGEST_SECRET`, `TWILIO_*`, `MOTHER_PHONE`,
    `NATU_PHONE`, `SUPABASE_*`, `APP_ENV=production`, `CORS_ORIGINS=https://<frontend-domain>`
  - Health check path: `/health` · 1 vCPU / 1 GB
  - After deploy, add `bedrock-agentcore:InvokeAgentRuntime` on the runtime ARN to
    the App Runner **instance role** (IAM console).
- Note the App Runner URL → this is your production `<PUBLIC_URL>`.

### Step 2.5 — Frontend to S3 + CloudFront  **[YOU, 30 min]**
```powershell
cd C:\Users\princ\kavach\frontend
$env:VITE_API_URL="https://<apprunner-url>"; npm run build
aws s3 mb s3://kavach-dashboard-<random>
aws s3 sync dist s3://kavach-dashboard-<random> --delete
```
CloudFront → Create distribution → origin = that S3 bucket (use OAC) →
Default root object `index.html` → add a custom error response: 403 & 404 →
`/index.html` (200) for SPA routing. Note the `*.cloudfront.net` URL.
Set App Runner's `CORS_ORIGINS` to that URL and redeploy.

### Step 2.6 — Supabase prod schema  **[YOU, 5 min]**
Supabase SQL editor → paste & run `backend/app/db/schema_full.sql` once.
Verify: `select count(*) from audit_events;` returns a number.

### Step 2.7 — Wire webhooks + smoke test  **[YOU, 15 min]**
- Twilio sandbox "when a message comes in" → `https://<apprunner-url>/whatsapp`
- SMS forwarder URL → `https://<apprunner-url>/ingest` (keep the `X-Kavach-Token` header)
- Real SMS from the phone → CloudFront dashboard shows the event → if scam, Natu
  gets WhatsApp → reply `BLOCK` → decision + immunity + reputation update land.

### Step 2.8 (optional, take what fits) — AgentCore extras  **[ME + YOU]**
- **AgentCore Browser** replaces Playwright for the sandboxed screenshot (removes
  chromium from the container). Ask me to swap the `screenshot_page` tool.
- **AgentCore Observability** — enable during `agentcore configure`; agent spans +
  tokens show in CloudWatch GenAI Observability. Strong tech-implementation signal.

### §2-ALT — Pure App Runner (if AgentCore is blocked)  **[YOU, 1–2 h]**
Deploy the whole `backend/` (unchanged, `PIPELINE_MODE=local`) to App Runner:
- Add to `backend/Dockerfile`: `RUN playwright install --with-deps chromium`
- App Runner from `backend/Dockerfile`, **1 vCPU / 2 GB** (fastembed + chromium need RAM)
- Same env vars minus the AgentCore ones
- First request downloads the fastembed model (~90 MB) — warm it with one `/ingest` call
- README: "AgentCore attempted; blocked by <quota/region>; deployed on App Runner." Still legitimate.

### §2-ALT-2 — Render (if AWS itself is blocked)
`render.yaml` is ready. Render → New → Blueprint → point at the repo → fill the
`sync:false` secrets → add a Static Site for `frontend/` (`npm run build`, publish
`dist`) → UptimeRobot ping `/health` every 10 min so the free service doesn't sleep.

**Phase 2 exit:** a real SMS on the phone appears on the public dashboard and (if a
scam) triggers a real WhatsApp escalation — all on deployed infra.

---

## PHASE 3 — Advanced polish (≈1 day)  **[ME] (you review)**

Ask me for these in this order:

1. **Speed pass** (target < 10 s typical): investigator on `gpt-oss-20b`, cut
   `INVESTIGATOR_MAX_STEPS` to 4, tighten the prompt so it stops in 2–3 tool calls,
   add a `TRIAGE_MODE=direct` fast path.
2. **Scheduled daily digest** — APScheduler fires `send_digest` at `DIGEST_HOUR` to
   Mother + Natu (counts, blocked scams, immunity hits, Doer actions, education notes).
3. **Dashboard panels** — reputation table (`/reputation` exists), stage-trace bar,
   render the investigation screenshot (base64) in the expanded card, immunity
   member-1-vs-2 timeline chart (`recharts` already installed).
4. **Consent + kill switch (brief §7)** — `/enroll` records consent + scope +
   timestamp; `/unenroll` also pauses the digest and shows "MONITORING PAUSED" on
   the dashboard; per-member "everything the agent saw and did" view.
5. **Observability** — export Strands OTel spans; show `llm_calls` + token counts
   per stage on the card.
6. **A real known-bad URL demo script** in `demo/` so the tools demonstrably fire,
   the screenshot lands, and the verdict is a confident `scam`.

---

## PHASE 4 — Eval + docs (≈½ day)  **[ME]**

1. `python -m eval.eval_runner` → triage precision / recall / F1 on 250 messages.
2. `python -m eval.pipeline_eval --limit 60 --immunity` → end-to-end action
   accuracy + the member-1-vs-2 propagation benchmark. (`--limit` because Groq free
   tier ≈ 30 rpm.)
3. Record the headline numbers, **especially false-positive rate on legit bank SMS**.
4. **README**: one-paragraph pitch · the escalation table (`plan.md §4`) ·
   architecture diagram (Excalidraw, ~45 min) · eval numbers · honest limitations ·
   MIT license · a "consent, audit log, kill switch" section.
5. Fold `ENHANCEMENTS.md` into the README "How it's built" section.

---

## PHASE 5 — Submit (≈½ day)  **[YOU]**

1. **Demo video ≤ 5 min** (`plan.md §6` beat sheet). Real scam messages throughout;
   label anything synthetic. Show: automatic SMS arrives → investigation trace →
   Natu escalation → one tap → **the immunity moment** (member 2, reworded, killed
   in seconds) → a Doer action → "agents investigate, code decides" → consent + audit log.
2. **Devpost** submission, AWS Builder ID attached, track = **Good Neighbor** (it
   protects a group / family).
3. **builder.aws.com** post with "Agents for Humans" in the title (bonus points).
4. Confirm the **$50 credits** landed; MIT `LICENSE` file in the repo.
5. Submit with hours to spare.

---

## Quick dependency map

| Blocked on YOU | I can start now |
|---|---|
| Twilio account + sandbox join (1.4) | `agentcore_app.py` + ingress split (2.2, 2.4) |
| Spare Android phone + forwarder app (1.3) | Speed pass, digest, dashboard panels (Phase 3) |
| AWS account + `aws configure` + Bedrock model access (2.1) | consent/kill-switch, demo script (Phase 3) |
| `$50` credits form (2.1) | eval runs + README draft (Phase 4) |
| Video, Devpost, builder.aws post (Phase 5) | AgentCore Browser / Observability wiring (2.8) |

## Recommended next 3 actions
1. **[YOU]** set `INGEST_SECRET` in `.env`, create the Twilio account, get a phone with a forwarder app.
2. **[YOU]** create the AWS account + `aws configure` + Bedrock model access + submit the credits form.
3. **[ME]** while you do that: `agentcore_app.py`, the ingress split, and the Phase 3 speed pass.
