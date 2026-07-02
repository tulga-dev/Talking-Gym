# Talking Gym 🏋️🗣

**Your personal English speaking coach in Telegram — built for Mongolian learners.**

Daily 5-minute speaking workouts: the bot gives you a real-life scenario, you answer with a **voice message**, and coach *Tamir* replies with corrections, tips in Mongolian, a score, and the next line of the conversation — plus an audio reply so you *hear* the correct sentence. Streaks keep you coming back.

> Market context: speaking is measurably Mongolia's weakest English skill (EF EPI), Messenger/Telegram are where learners already are, and voice-AI cost — not demand — is the main constraint. This MVP attacks all three. See the research report in the parent project for full citations.

## Why Grok (the cost answer)

Voice is the expensive part of an AI tutor. xAI's standalone audio APIs (launched Apr 2026) change the math:

| Component | Grok (xAI) | Typical alternative | Notes |
|---|---|---|---|
| Speech-to-text | **$0.10/hr** batch (`grok-stt`), $0.20/hr streaming | Azure $1.00/hr · Whisper API ~$0.36/hr | **10× cheaper than Azure** |
| Text-to-speech | $15 / 1M chars (`/v1/tts`, 5 voices) | Azure $15/1M · ElevenLabs ≫ | Par with the cheapest |
| LLM | `grok-4.3` $1.25/M in · $2.50/M out | Comparable-tier models similar | Fine for coaching |
| Realtime voice agent | $3.00/hr ($0.05/min) | OpenAI Realtime ≫ | Future "live call" premium feature, not MVP |

**Per heavy learner (10 min voice/day, 20 days/mo):** STT ≈ $0.33 + TTS ≈ $0.45 + LLM ≈ $0.20 → **≈ $1.00/month**, vs ≈ $5 on the Azure stack estimated in the original research. At a ₮14,900/mo (~$4.30) subscription this leaves healthy margin — the unit-economics problem is essentially solved by Grok pricing.

*Trade-off:* Grok STT gives a transcript, not phoneme-level pronunciation scores. The MVP coaches from the transcript (grammar, vocabulary, task success — plus what the STT "mishears" is itself a pronunciation signal). Rubric-graded pronunciation (Azure Pronunciation Assessment / Speechace, IELTS-mapped) is the planned **IELTS premium tier**, where the higher price absorbs the cost.

## Quick start

1. **Create a bot:** talk to [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. **Get an xAI key:** https://console.x.ai → API keys.
3. **Run:**

```bash
git clone https://github.com/tulga-dev/Talking-Gym.git
cd Talking-Gym
python -m venv .venv && .venv\Scripts\activate    # Windows (use source .venv/bin/activate on Linux/Mac)
pip install -r requirements.txt
copy .env.example .env                             # then edit .env: bot token + xAI key
python main.py
```

Open your bot in Telegram → `/start`.

### Try the engine without Telegram / without keys

```bash
python -m talking_gym.dev_chat --mock   # offline console chat with a canned LLM
python -m talking_gym.dev_chat          # console chat through the real Grok LLM
```

## Go live (runtime + Supabase) — let other people test it

Local SQLite is dev-only. For a live bot that real testers can use, run it as a hosted worker with Supabase Postgres so data (users, streaks, feedback) survives redeploys.

**1. Supabase (free tier is enough):**
- Create a project at [supabase.com](https://supabase.com) → wait for it to provision
- Dashboard → **Connect** → copy the **Transaction pooler** URI (port `6543`, IPv4-friendly — works on all hosts)
- That URI (with your DB password filled in) is your `SUPABASE_DB_URL`. **Tables are created automatically on first boot** — no SQL to run.

**2. Runtime — Fly.io via GitHub Actions (configured):**
- Every push to `main` auto-deploys app **`talking-gym-mn`** (region `hkg`) through [.github/workflows/deploy.yml](.github/workflows/deploy.yml)
- Required GitHub repo secrets: `TELEGRAM_BOT_TOKEN`, `XAI_API_KEY`, `SUPABASE_DB_URL`, `ADMIN_CHAT_ID`, `FLY_API_TOKEN` (a Fly deploy token: `flyctl tokens create deploy -a talking-gym-mn`). App secrets are re-staged from GitHub secrets on every deploy — GitHub is the single source of truth.
- Deploys run `--ha=false`: the bot long-polls, so **exactly 1 machine** (two pollers on one token conflict). No domain, no port, nothing to expose.
- Ops: `flyctl status -a talking-gym-mn` · `flyctl logs -a talking-gym-mn` · `flyctl machine restart -a talking-gym-mn`
- Alternatives: Railway (deploy from GitHub repo, add the same env vars) or any Docker host: `docker build -t talking-gym . && docker run --env-file .env talking-gym`

**3. Invite testers:**
- Share your bot's `t.me/...` link
- Testers send suggestions with `/feedback ...` — stored in Supabase and forwarded to your Telegram (`ADMIN_CHAT_ID` = your numeric id, ask [@userinfobot](https://t.me/userinfobot))
- You watch adoption with `/stats` (admin-only): users, sessions, trained-today, voice seconds, feedback count

## What the bot does

**Duolingo-grade UX, zero typing needed:** persistent big-button keyboard (🏋️ Өнөөдрийн дасгал / 🔥 Миний ахиц / ❓ Тусламж), populated command menu, and an inline "🔁 Дахин дасгал" button after every finished workout. Picking a level drops you straight into your first lesson.

- `/start` — onboarding in Mongolian, pick your level (A1–A2 / B1 / B2+) → first workout starts immediately
- `/today` (or the 🏋️ button) — today's workout: a scenario (ordering coffee, job interview, IELTS-style describe-your-hometown, …) chosen for your level, rotating daily
- **Gamification:** per-turn XP (score-based + completion bonus), 6 ranks (🌱→💎) with progress bars, turn dots (●●○), tiered visual score bars (🟩🟨🟥), streak-milestone celebrations (3/7/14/30/60/100 days)
- 🎤 answer by **voice note** → Grok STT → coach replies with: what you said, the corrected sentence, one specific tip **in Mongolian**, a 0–100 score, and the next conversation turn — plus a TTS audio reply so you hear the model answer
- ⌨️ typing works too (voice is just better practice)
- `/progress` (or 🔥 button) — rank, XP progress bar, streak & totals (sessions complete after ~3 turns)
- `/remind 19` — free daily nudge at 19:00 (Telegram pushes cost nothing)
- `/feedback ...` — testers' suggestions go to Supabase + your Telegram; `/stats` (admin) shows adoption
- Built-in **cost guardrail**: per-user daily voice cap (default 300s, `DAILY_VOICE_SECONDS_CAP`)

## Facebook Messenger channel (the 83%-of-Mongolia channel)

The bot also serves Messenger via a webhook (same process, same brain, plain-text rendering). To activate:

1. Create a **Facebook Page** for Talking Gym (the bot speaks as the Page).
2. [developers.facebook.com](https://developers.facebook.com) → Create App (Business) → add the **Messenger** product → connect your Page → generate a **Page access token**.
3. App settings → Basic → copy the **App secret** (used to verify webhook signatures).
4. Add GitHub repo secrets: `MESSENGER_PAGE_TOKEN`, `MESSENGER_APP_SECRET`, `MESSENGER_VERIFY_TOKEN` (any string you invent), then re-run the deploy workflow.
5. Messenger product → Webhooks → Callback URL `https://talking-gym-mn.fly.dev/webhooks/messenger`, Verify token = the same string → subscribe the Page to `messages` and `messaging_postbacks`.
6. Test from your own account (works immediately for app admins/testers). Public access needs Meta's `pages_messaging` review (a few days).

Users get the same coach: persistent menu (🏋️/🔥/❓), quick-reply onboarding, voice both ways, streaks/XP. Feedback: users type `санал: ...`.

## Architecture

```
main.py                      entrypoint (long polling)
talking_gym/
  config.py                  env config
  db.py                      dual backend: Supabase Postgres (prod) / SQLite (dev)
                             users, streaks, sessions, voice usage, feedback
  scenarios.py               bilingual scenario bank (12 seed scenarios, 3 levels)
  prompts.py                 coach persona + strict-JSON turn contract
  coach.py                   ★ channel-agnostic coaching engine
  providers/
    llm.py                   OpenAI-compatible chat (xAI Grok default)
    stt.py                   Grok STT  (POST /v1/stt)
    tts.py                   Grok TTS  (POST /v1/tts)
  channels/
    telegram_bot.py          Telegram adapter (handlers, reminders, voice pipeline)
    messenger.py             Facebook Messenger adapter (webhook server, Send API)
  dev_chat.py                console REPL (+ --mock for offline dev)
```

The **coach engine knows nothing about the channels** — Telegram polls, Messenger receives webhooks, and both call the same `coach.py`. main.py runs the poller and the aiohttp web server in one asyncio loop.

## Roadmap (from the research)

- [ ] Facebook Messenger adapter (the mass-reach channel in Mongolia)
- [ ] IELTS premium tier: Azure Pronunciation Assessment / Speechace rubric scores
- [ ] QPay subscription checkout + tiers (Free / Gym ₮14,900 / IELTS ₮39,900)
- [ ] Cached AI-image library for visual vocabulary prompts
- [ ] Grok Realtime voice agent ($3/hr) as a premium "live call" mode
- [ ] Leaderboards & community challenges

## Config reference

See [.env.example](.env.example) — every knob (models, voice, caps, reminder hour, timezone) is an env var.

## Sources for pricing claims

- xAI STT/TTS launch & pricing: [x.ai/news/grok-stt-and-tts-apis](https://x.ai/news/grok-stt-and-tts-apis), [docs.x.ai models](https://docs.x.ai/docs/models) (STT $0.10/hr REST, $0.20/hr streaming; TTS $15/1M chars; realtime $0.05/min; grok-4.3 $1.25/$2.50 per M tokens — checked 2026-07-02)
- Endpoint shapes: [docs.x.ai voice overview](https://docs.x.ai/developers/model-capabilities/audio/voice) (`POST /v1/stt` multipart, `POST /v1/tts` JSON, voices eve/ara/rex/sal/leo)
- Azure comparison: [Azure Speech pricing](https://azure.microsoft.com/en-us/pricing/details/speech/)
