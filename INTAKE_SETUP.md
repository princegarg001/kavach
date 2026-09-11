# Kavach — automatic intake setup (SMS · WhatsApp · Email)

All three land on the same endpoint and run the identical pipeline. They differ
only in `source` and how the message reaches Kavach.

| Channel | How it's captured | Fully passive? | Who sets it up |
|---|---|---|---|
| **SMS** | Android SMS-forwarder app → `POST /ingest` | ✅ every SMS, no forwarding | you (install 1 app) |
| **WhatsApp** | Android Notification-Listener app → `POST /ingest` | ✅ every notification | you (install 1 app) — or keep the Twilio "forward suspicious here" path |
| **Email** | IMAP poll (backend) **or** filter-forward → `POST /ingest/email` | ✅ every email | you (one Gmail App Password) — code is done |

`<BASE>` below = your public URL. Local testing: the `cloudflared` tunnel
(`https://…trycloudflare.com`). Production: your deployed URL.
`<TOKEN>` = the value of `INGEST_SECRET` in `backend/.env`
(currently `kavach_c4a8c989ec143c8e755bad33`).

---

## 1. SMS — Android forwarder (10 min)

1. On the phone to protect, install **"SMS Forwarder"** (F-Droid, by *enach*) or
   **"SMS to URL Forwarder"** (Play Store).
2. Grant **Receive SMS / Read SMS** permission.
3. Add one rule:
   - **Trigger:** any incoming SMS (leave the sender filter blank / `*`)
   - **Action:** HTTP request
   - **Method:** `POST`
   - **URL:** `<BASE>/ingest`
   - **Headers:**
     ```
     Content-Type: application/json
     X-Kavach-Token: <TOKEN>
     ```
   - **Body** (the app substitutes its own placeholder tokens — names vary):
     ```json
     {"member_id":"whatsapp:+918295095069","member_name":"Mummy","raw_text":"{{message}}","source":"sms","message_id":"{{sentStamp}}-{{from}}"}
     ```
     Common placeholder names: `%message%` / `{{message}}` / `[message]` for the
     text; `%from%` / `{{from}}` for the sender; any timestamp for `message_id`.
4. Send the phone a test SMS from another number.

**Verify:** `curl <BASE>/intake/status` → `by_source` shows `"sms": N`; the event
appears on the dashboard with the **INTAKE** pill green — **nobody forwarded anything**.

---

## 2. WhatsApp

### Option A (recommended, compliant) — Twilio "forward suspicious here"
Already working. The family forwards anything doubtful to the Twilio sandbox
number; everything after is automatic. No app to install.

### Option B (fully passive, one phone) — Notification Listener
No API can read a whole WhatsApp inbox, but an Android app can read the
**notification text** of every incoming message.

1. Install a notification-forwarder: **"Notification Forwarder"** (F-Droid) or
   **"Tasker"** + **AutoNotification**.
2. Grant **Notification access** (Settings → Notifications → Notification access).
3. Rule: when a notification from package **`com.whatsapp`** arrives →
   `POST <BASE>/ingest` with headers as above and body:
   ```json
   {"member_id":"whatsapp:+918295095069","member_name":"Mummy","raw_text":"{{text}}","source":"whatsapp","message_id":"{{postTime}}-{{title}}"}
   ```
4. Caveat: long messages are truncated in the notification; this reads *your own*
   device's notifications for personal use.

---

## 3. Email — DONE in code, needs one credential

### Option A — IMAP poll (fully automatic, backend-only, no webhook)
1. On the Gmail account to watch: enable **2-Step Verification**, then create an
   **App Password** → https://myaccount.google.com/apppasswords (pick "Mail").
2. In `backend/.env`:
   ```
   EMAIL_INTAKE_ENABLED=true
   IMAP_USER=that-account@gmail.com
   IMAP_PASSWORD=the16charapppassword     # no spaces
   EMAIL_POLL_SECONDS=120
   ```
3. Restart the backend. It polls every 2 min for **unread** mail, runs each through
   the pipeline as `source: email`, and marks it read.

**Verify:** send that inbox an email → within 2 min it's on the dashboard;
`/intake/status` shows `"email": N`.

### Option B — filter-forward (no IMAP, instant)
1. In Gmail → Settings → Filters → create a filter (e.g. "from anyone") →
   **Forward to** `kavach@<your-domain>`.
2. Point that address at `POST <BASE>/ingest/email` via **Cloudflare Email Routing
   → Email Worker**, or **SendGrid Inbound Parse**, or **Mailgun routes**.
   The endpoint accepts JSON `{from,subject,text,message_id}` or the form-encoded
   payloads SendGrid/Mailgun send. Include header `X-Kavach-Token: <TOKEN>`.

---

## Endpoints reference

| Method | Path | Used by | Auth |
|---|---|---|---|
| POST | `/ingest` | SMS + WhatsApp forwarder apps | `X-Kavach-Token` |
| POST | `/ingest/email` | Cloudflare/SendGrid/Mailgun inbound-parse | `X-Kavach-Token` |
| POST | `/whatsapp` | Twilio sandbox webhook | Twilio signature (optional) |
| GET  | `/intake/status` | dashboard INTAKE pill | none |

Body for `/ingest`:
```json
{"member_id":"<stable id>","member_name":"<display>","raw_text":"<message>","source":"sms|whatsapp|email","message_id":"<unique>"}
```
`message_id` is deduped for `DEDUP_WINDOW_SECONDS` (600) so retries are harmless.
