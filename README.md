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

## What the bot does

- `/start` — onboarding in Mongolian, pick your level (A1–A2 / B1 / B2+)
- `/today` — today's workout: a scenario (ordering coffee, job interview, IELTS-style describe-your-hometown, …) chosen for your level, rotating daily
- 🎤 answer by **voice note** → Grok STT → coach replies with: what you said, the corrected sentence, one specific tip **in Mongolian**, a 0–100 score, and the next conversation turn — plus a TTS audio reply so you hear the model answer
- ⌨️ typing works too (voice is just better practice)
- `/streak` — 🔥 daily streak & totals (sessions complete after ~3 turns)
- `/remind 19` — free daily nudge at 19:00 (Telegram pushes cost nothing)
- Built-in **cost guardrail**: per-user daily voice cap (default 300s, `DAILY_VOICE_SECONDS_CAP`)

## Architecture

```
main.py                      entrypoint (long polling)
talking_gym/
  config.py                  env config
  db.py                      SQLite: users, streaks, sessions, voice usage
  scenarios.py               bilingual scenario bank (12 seed scenarios, 3 levels)
  prompts.py                 coach persona + strict-JSON turn contract
  coach.py                   ★ channel-agnostic coaching engine
  providers/
    llm.py                   OpenAI-compatible chat (xAI Grok default)
    stt.py                   Grok STT  (POST /v1/stt)
    tts.py                   Grok TTS  (POST /v1/tts)
  channels/
    telegram_bot.py          Telegram adapter (handlers, reminders, voice pipeline)
  dev_chat.py                console REPL (+ --mock for offline dev)
```

The **coach engine knows nothing about Telegram** — adding Facebook Messenger (83% of Mongolians) later means writing one new adapter in `channels/`, per the channel-agnostic recommendation in the market research.

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
