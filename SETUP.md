# SquawkBox — Railway Deployment (iPad-Friendly)

Every step below can be done from an iPad using Safari — no terminal,
Docker, or ngrok required.

- **App:** FastAPI (uvicorn) — Twilio webhooks + voice note media
- **Worker:** Celery worker — AI pipeline (news enrichment, script, TTS)
- **Beat:** Celery beat — recurring tasks (poll every 15 min, trial expiry)
- **Redis:** Railway plugin — message broker and audio cache
- **Database:** Hosted Supabase (no self-managed Postgres)

---

## 1. Prerequisites

You need a browser and accounts at each of the following:

| Service | Why | Sign up |
|---|---|---|
| **Railway** | Hosts app, worker, beat, Redis | https://railway.app |
| **GitHub** | Railway deploys from your repo | https://github.com |
| **Supabase** | Hosted Postgres database + REST API | https://supabase.com/dashboard/sign-up |
| **Twilio** | WhatsApp messaging (sandbox) | https://www.twilio.com/try-twilio |
| **NewsData.io** | Financial news feed | https://newsdata.io/register |
| **Tavily** | Context enrichment for headlines | https://app.tavily.com/home |
| **Together AI** | LLM script generation | https://api.together.ai/signin |
| **Cartesia** | Text-to-speech | https://play.cartesia.ai/sign-up |
| **Stripe** | Billing (test mode — dummy keys are fine) | https://dashboard.stripe.com/register |
| **Sentry** (optional) | Error tracking — leave blank to skip | https://sentry.io/signup/ |

---

## 2. Connect the GitHub repo

1. Go to https://github.com/fraserb101/SquawkBox.
2. Fork it to your own GitHub account (tap **Fork** at the top right).

---

## 3. Create a Railway project

1. Go to https://railway.app and sign in with GitHub.
2. Tap **New Project** → **Empty Project**.
3. Name it `squawkbox`.

---

## 4. Add Redis

1. Inside your project, tap **New** → **Database** → **Redis**.
2. Railway provisions a Redis instance automatically. No config needed.

---

## 5. Create the three services

All three use the same GitHub repo with different start commands.

### 5a. App service (FastAPI)

1. Tap **New** → **GitHub Repo** → select your SquawkBox fork.
2. Railway detects the Dockerfile and starts building.
3. Click the new service, go to **Settings**:
   - **Service Name:** `app`
   - **Start Command:**
     ```
     alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1
     ```
     This runs database migrations before each deploy (Alembic is
     idempotent), then starts the web server on Railway's assigned port.
   - Under **Networking**, tap **Generate Domain**. Copy the public URL
     (e.g. `https://app-production-abc123.up.railway.app`). This replaces
     ngrok entirely.

### 5b. Worker service (Celery worker)

1. Tap **New** → **GitHub Repo** → select the same repo again.
2. Click the service, go to **Settings**:
   - **Service Name:** `worker`
   - **Start Command:**
     ```
     celery -A celery_worker worker --loglevel=info
     ```
   - Do NOT generate a domain — the worker has no HTTP traffic.

### 5c. Beat service (Celery beat)

1. Tap **New** → **GitHub Repo** → select the same repo a third time.
2. Click the service, go to **Settings**:
   - **Service Name:** `beat`
   - **Start Command:**
     ```
     celery -A celery_worker beat --loglevel=info
     ```
   - Do NOT generate a domain.

---

## 6. Set environment variables

Use Railway's **Shared Variables** so all three services inherit the
same config.

1. In your project, tap **Settings** → **Shared Variables** →
   **New Variable Group** → name it `squawkbox-config`.
2. Add every variable from the table below.
3. Go to each service (app, worker, beat), tap **Variables** →
   **Shared Variable Groups** → attach `squawkbox-config`.
4. In each service's own variables, also add the Redis reference:
   - **Name:** `REDIS_URL`
   - **Value:** `${{Redis.REDIS_URL}}`

### Variable reference

| Variable | Required | Where to get it |
|---|---|---|
| `SUPABASE_URL` | Yes | Supabase → Project Settings → API → Project URL |
| `SUPABASE_KEY` | Yes | Supabase → Project Settings → API → `service_role` secret (not `anon` — service role bypasses RLS) |
| `DATABASE_URL` | Yes | Supabase → Project Settings → Database → Connection string → URI. Used by Alembic migrations only. |
| `NEWSDATA_API_KEY` | Yes | https://newsdata.io/dashboard → API key |
| `TAVILY_API_KEY` | Yes | https://app.tavily.com/home → API key |
| `TOGETHER_API_KEY` | Yes | https://api.together.ai → Settings → API Keys → create one |
| `CARTESIA_API_KEY` | Yes | https://play.cartesia.ai → API Keys in sidebar |
| `CARTESIA_VOICE_ID` | Yes | https://play.cartesia.ai → Voices → pick a voice → copy its ID |
| `TWILIO_ACCOUNT_SID` | Yes | Twilio Console home → Account SID |
| `TWILIO_AUTH_TOKEN` | Yes | Twilio Console home → click Show next to Auth Token |
| `TWILIO_WHATSAPP_FROM` | Yes | Twilio sandbox phone number, e.g. `+14155238886` (include the `+`) |
| `PUBLIC_BASE_URL` | Yes | Your Railway app domain from step 5a, e.g. `https://app-production-abc123.up.railway.app` — no trailing slash |
| `STRIPE_SECRET_KEY` | Yes | `sk_test_dummy` for now, or a real key from https://dashboard.stripe.com/test/apikeys |
| `STRIPE_WEBHOOK_SECRET` | Yes | `whsec_dummy` for now |
| `STRIPE_PAYMENT_LINK` | Yes | `https://buy.stripe.com/test_dummy` for now |
| `ADMIN_SECRET` | Yes | Any long random string — protects `/admin/*` endpoints via `X-Admin-Secret` header |
| `SENTRY_DSN` | No | Leave blank to disable Sentry. Get from https://sentry.io if you want error tracking. |
| `YOUR_WHATSAPP_NUMBER` | No | Twilio sandbox number without the `+`, e.g. `14155238886`. Used for wa.me referral deep links. |
| `TERMS_URL` | Yes | Any URL, e.g. `https://example.com/terms` |

**Note:** `REDIS_URL` is set per-service as a reference variable
(`${{Redis.REDIS_URL}}`), not in the shared group.

---

## 7. Set up Twilio WhatsApp sandbox

1. In the Twilio Console, go to **Messaging → Try it out → Send a
   WhatsApp message**.
2. Note the **sandbox phone number** (e.g. `+1 415 523 8886`) and the
   **join word** (e.g. `join violet-purple`).
3. Tap **Sandbox settings**.
4. In **"When a message comes in"**, paste your Railway app URL with
   `/webhook`:
   ```
   https://app-production-abc123.up.railway.app/webhook
   ```
   Method: **HTTP POST**.
5. Tap **Save**.

### Join the sandbox from your phone

1. Open WhatsApp on your phone.
2. Start a new chat with the sandbox number.
3. Send the join word (e.g. `join violet-purple`).
4. Twilio confirms you've joined. You're authorized for 72 hours.

---

## 8. Database migrations

Migrations run automatically on every deploy — the app service's start
command begins with `alembic upgrade head`.

After the first successful deploy, verify in **Supabase Studio → Table
Editor** that these tables exist: `users`, `ticker_subscriptions`,
`subscriptions`, `referrals`, `squawk_logs`, `squawk_deliveries`.

If tables are missing, check the app service's deploy logs in Railway.

---

## 9. Create a starter user

The signup flow requires a referral code from an existing user. Bootstrap
yourself directly in Supabase:

1. Open **Supabase Studio → SQL Editor → New query**.
2. Run:
   ```sql
   INSERT INTO users (phone_number, subscription_status, trial_expiry,
                      referral_code, terms_accepted_at)
   VALUES (
     '+15551234567',
     'trial',
     now() + interval '7 days',
     'BOOTSTRAP',
     now()
   );
   ```
   Replace `+15551234567` with the phone number you joined the sandbox
   from (E.164 format, include the `+`).

---

## 10. Verify it's working

- [ ] **Railway services green:** All three services show Active in the
      Railway dashboard.
- [ ] **App healthy:** Open `https://<your-railway-url>/health` in Safari
      → `{"status":"ok"}`
- [ ] **Worker running:** Railway → worker → Logs → `celery@... ready.`
- [ ] **Beat running:** Railway → beat → Logs → `beat: Starting...`
- [ ] **Twilio webhook set:** Sandbox settings shows your Railway URL
      with `/webhook`.
- [ ] **Sandbox joined:** You got a confirmation WhatsApp from Twilio.
- [ ] **User exists:** Supabase SQL Editor →
      `SELECT * FROM users WHERE phone_number = '+...';` returns a row.
- [ ] **Text round-trip:** Send `HELP` via WhatsApp → get the command
      list back. Check app logs in Railway.
- [ ] **ADD and LIST:** Send `ADD AAPL`, then `LIST`.
- [ ] **Voice note:** Beat polls news every 15 minutes. Watch worker logs
      for `Processing article: ...` → `Pipeline complete`. You'll receive
      a voice note in WhatsApp when a relevant article is found.

---

## 11. Viewing logs

1. Open your project in the Railway dashboard.
2. Click any service (app, worker, beat).
3. Tap **Logs** for real-time output.

---

## 12. Redeploying

Railway auto-deploys all three services when you push to the connected
branch on GitHub. Manual redeploy: click a service → **Deployments** →
**Redeploy**.

---

## 13. Known unknowns

- **Sandbox expiry.** The Twilio sandbox only talks to numbers that sent
  the join word within the last 72 hours. Re-send it if messages stop.
- **Supabase RLS.** The code uses the `service_role` key to bypass RLS.
  Swapping to the `anon` key will silently return empty results.
- **Cartesia audio format.** `send_voice_note()` assumes WAV from Cartesia
  and transcodes to OGG/Opus via FFmpeg. If Cartesia changes defaults,
  update `input_format` in `services/analyst.py`.
- **YOUR_WHATSAPP_NUMBER vs TWILIO_WHATSAPP_FROM.** Both point at the
  sandbox number. `TWILIO_WHATSAPP_FROM` sends messages;
  `YOUR_WHATSAPP_NUMBER` builds wa.me referral links.
- **Stripe dummy keys.** Billing flows won't work until you plug in real
  Stripe test-mode keys. The app still starts fine.
- **Voice-note media endpoint.** `/media/{token}` is unauthenticated so
  Twilio can fetch audio. Tokens are 256-bit URL-safe, expire in 10 min.
- **Railway sleep.** On hobby tier, services may sleep after inactivity.
  First request after idle may time out — retry after a minute.

---

## 14. Limitations

These steps genuinely cannot be done from an iPad:

- **Running tests locally.** `pytest` requires a Python environment and
  terminal. Tests run automatically via GitHub Actions CI on every push
  to `main` or on pull requests — check the **Actions** tab on GitHub.
- **Local Docker development.** `docker-compose.yml` in the repo is for
  local laptop development only. Railway does not use it.
- **Writing new Alembic migrations.** `alembic revision` requires a local
  Python + Alembic setup. Running existing migrations works automatically
  on Railway via the app start command.
- **FFmpeg debugging.** If audio conversion fails, diagnosing FFmpeg
  requires a terminal. Check worker logs in Railway for error output.

For everything else — deploying, configuring, monitoring, viewing logs,
managing the database via Supabase Studio, configuring Twilio — Safari
on an iPad is all you need.

---

## Local development (optional, requires a laptop)

The repo includes `docker-compose.yml` for running the full stack locally
with Docker Desktop. You'll also need ngrok to expose port 8000 to Twilio.
See the comments at the top of that file for usage.
