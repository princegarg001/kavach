## Inspiration

Every family has one person who quietly becomes everyone else's tech support — the one who gets the panicked forward at 11pm: *"beta, is this message real?"* A scam SMS lands on a parent's phone, they can't tell a spoofed bank alert from a real one, and the fix is always the same exhausting loop: screenshot, forward, wait, explain.

The insight that started Kavach: **scammers rotate phone numbers and URLs constantly, but they reuse the same pitch.** Blocklists miss that completely — a new number, a new shortlink, same fake-KYC script. If one family member gets targeted and the household "figures it out," that knowledge dies in one WhatsApp thread. It never reaches the parent three towns over who's about to get the exact same message with a different link.

We wanted an agent that does what the tech-support family member does — investigate, decide, act — but that also **remembers**, so the second person it protects benefits from what happened to the first, automatically, in milliseconds.

## What it does

Kavach is an autonomous agent that watches over a family's WhatsApp, SMS, and email, and only interrupts a human when there's a real decision to make.

- **Automatic intake, three channels.** An Android SMS-forwarder app and Twilio WhatsApp both feed every message straight into the pipeline — no one has to remember to forward anything. A background IMAP poller does the same for email.
- **A real agentic Investigator.** When a message contains a link, a Strands agent decides for itself which of 7 forensic tools to run — domain age, TLS cert, redirect chain, 4 threat-intel feeds, brand-impersonation/homograph detection, landing-page credential-harvesting analysis — and stops the moment it has enough evidence. A confirmed-malicious hit ends the investigation in one tool call instead of exhaustively running all of them.
- **The immunity ledger — the actual point of the project.** The moment a scam is confirmed for one family member, its *semantic pitch* (not just the URL) is embedded and stored. Every subsequent message from anyone else in the family is checked against that ledger *before* a full investigation runs. Member 1's investigation takes ~15–25 seconds. Member 2's reworded variant — different number, different shortlink, same scam — is silently killed in **under a second**.
- **A deterministic policy engine decides, not a model.** Every signal (triage, immunity match, investigation verdict, sender reputation) feeds a YAML rule engine — plain code, no LLM — that picks one of: block silently, act automatically, ask the protected person a simple yes/no, or escalate urgently to a trusted decider. Two different humans can be in the loop: the person being protected, and the person who actually makes the call.
- **A Doer agent that does real work**, not just flags things: extracts bill due-dates and sets reminders, rewrites cryptic bank SMS into plain language, explains OTPs, and — when fraud is confirmed — drafts a Chakshu/1930 cyber-fraud complaint that's held for one human tap and *never auto-filed*.
- **An Educator agent** writes a two-sentence, plain-language explanation of *why* a message was a scam, in the recipient's own language (English/Hindi/Hinglish), so the person it protects gets a little better at spotting the next one themselves.
- **Real consent and a real kill switch.** Every enrolled member has a recorded consent scope and a visible audit trail of everything the agent saw and did; a family member (or the whole system) can be paused instantly from the dashboard.

## How we built it

- **Strands Agents SDK** runs all four reasoning agents: Triage (`structured_output_async`), Investigator (a genuine ReAct tool-calling agent with 7 `@tool`s), Doer, and Educator.
- **Groq** (`openai/gpt-oss-120b` for the investigator, `gpt-oss-20b` for everything else) is the model provider, reached through Strands' OpenAI-compatible model interface — chosen so the SDK integration is the star, not a specific cloud's model catalogue.
- **Supabase + pgvector** stores the audit log and the immunity ledger, with a **dual-vector search**: one embedding of the raw message, one of the message with every identifier (phone, URL, amount, date) stripped out — the "pitch only" vector — so a reworded scam still matches on meaning.
- **fastembed**, running locally with no API key, generates those embeddings.
- **FastAPI** backend with SSE for live dashboard updates, APScheduler for a daily digest, and a hardened ingress (shared-secret auth, Twilio signature validation, message dedup).
- **React 19** dashboard with a from-scratch design system: an animated, step-by-step reveal of the agent's actual reasoning trace (thought → tool call → observation → verdict), not just a spinner.
- **Twilio** for WhatsApp intake and outbound escalations; an Android SMS-forwarder app and a stdlib IMAP poller for the other two channels.
- Deployed on **Render** (Docker backend + static frontend), with an AWS Bedrock AgentCore entrypoint built and ready (`agentcore_app.py`) as a documented next step.

## Challenges we ran into

- **Cloud access, twice.** We started on Azure OpenAI and hit a hard regional policy block; when we then tried deploying the whole app to Azure, the university's Microsoft tenant had Security Defaults enabled and denied the account access to Azure Resource Manager entirely — an admin-level lock, not something retriable. We pivoted the model provider to Groq and the deployment to Render rather than keep fighting infrastructure that wasn't ours to unblock.
- **A classic asyncio trap, in production.** WhatsApp escalations worked locally but silently vanished once deployed. The cause: several background jobs (send the escalation, write the immunity signature, update sender reputation) were started with bare `asyncio.create_task()` and never referenced again — the event loop only holds a *weak* reference to an unreferenced task, so under real memory pressure it can be garbage-collected mid-flight, with no error and nothing in the logs. We built a small `spawn()` helper that keeps a strong reference to every background task until it finishes and logs any failure instead of swallowing it.
- **Webhook signature validation behind a proxy.** Render's edge (Cloudflare-fronted) meant the ASGI-reconstructed request URL didn't always match what Twilio signed against. We made the validator try several candidate URLs (raw, forced-https, `X-Forwarded-*`, an explicit `PUBLIC_BASE_URL`) rather than trust one reconstruction — and the actual root cause, once we could rule out the URL, turned out to be a simple mismatched credential in the deploy environment.
- **512MB of RAM.** The investigator's screenshot tool needs headless Chromium, which doesn't comfortably coexist with the local embedding model on a free-tier instance. We made it a feature flag (`ENABLE_SCREENSHOT_TOOL`) so the other six forensic tools keep running unaffected wherever it's off.

## Accomplishments that we're proud of

- Four genuinely agentic Strands agents in production, not just API wrappers around a single prompt.
- A measured, demonstrable "herd immunity" moment: the same scam pitch, reworded, killed in milliseconds for the second family member instead of re-investigated from scratch.
- Three real automatic intake channels feeding one pipeline.
- A policy engine that keeps every irreversible decision out of the model's hands.
- 28 passing offline tests and a live, publicly reachable deployment — not just a local demo.

## What we learned

- How to design a tool-calling agent that *stops early* — the interesting engineering problem in agentic systems is knowing when enough evidence exists, not how to call more tools.
- That the most dangerous bugs in an agentic backend aren't in the agent logic at all — they're in the unglamorous plumbing (background tasks, proxy headers, credentials) around it.
- Why a deterministic policy layer matters: it's the difference between "an LLM decided to silently drop a message" and "here is the exact, auditable rule that fired."

## What's next

- Deploy the agent layer on **Bedrock AgentCore Runtime** (the entrypoint is already written) for a stronger production story.
- Passive WhatsApp capture via an Android Notification Listener, so forwarding becomes fully optional.
- Expand the Doer agent's task set and add more Indian languages to the Educator.
- Real eval numbers (precision/recall, false-positive rate on legitimate bank SMS) from the full pipeline, not just triage.
