# SquawkBox — Local Setup

This is the end-to-end walkthrough for running SquawkBox on your laptop so
that a WhatsApp message to the Twilio sandbox returns an AI-generated
financial-news voice note.

- **App:** FastAPI (uvicorn) on port 8000 — `main.py`
- **Background jobs:** Celery worker + beat — `celery_worker.py`
- **Queue/broker:** Redis (Docker service)
- **Inbound messaging:** Twilio WhatsApp sandbox webhook → `POST /webhook`
- **Outbound voice notes:** served back to Twilio via `GET /media/{token}`
- **Database:** hosted Supabase (no local Postgres)

---

## 1. Prerequisites

Install these on your machine before going further:

| Tool | Why | Install |
|---|---|---|
| **Docker Desktop** | Runs the whole stack | https://www.docker.com/products/docker-desktop/ |
| **ngrok** | Exposes port 8000 to Twilio | https://ngrok.com/download (or `brew install ngrok`) |
| **Python 3.11** (optional) | Only needed if you want to run tests outside Docker. The app itself runs in Docker from `python:3.11-slim`. | https://www.python.org/downloads/release/python-31115/ |
| **A WhatsApp-enabled phone** | To message the Twilio sandbox | — |

Sign up for free accounts at each of the following — you'll grab keys from
each in the next section:

- Twilio: https://www.twilio.com/try-twilio
- Supabase: https://supabase.com/dashboard/sign-up
- NewsData.io: https://newsdata.io/register
- Tavily: https://app.tavily.com/home
- Together AI: https://api.together.ai/signin
- Cartesia: https://play.cartesia.ai/sign-up
- Stripe (test mode only): https://dashboard.stripe.com/register
- Sentry (optional, leave blank to skip): https://sentry.io/signup/

---

## 2. Clone and create your `.env`

```bash
git clone https://github.com/fraserb101/SquawkBox.git
cd SquawkBox
cp .env.example .env
```

Every variable below needs a value in `.env`. The rest of this section
explains each one and where to find it.

### Supabase

1. Create a new project at https://supabase.com/dashboard/projects.
2. Once the project is provisioned, open **Project Settings → API**.
   - Copy **Project URL** → `SUPABASE_URL`
   - Copy the **`service_role`** secret → `SUPABASE_KEY`
     (not the `anon` key — the service role is required to bypass RLS
     from the backend.)
3. Open **Project Settings → Database → Connection string**, pick
   **URI**, and copy it → `DATABASE_URL`. This is only used by Alembic
   when running migrations.

```env
SUPABASE_URL=https://<ref>.supabase.co
SUPABASE_KEY=<service_role_key>
DATABASE_URL=postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres
```

### Redis

`REDIS_URL` is pre-filled for the docker-compose network and should
normally be left as-is:

```env
REDIS_URL=redis://redis:6379/0
```

### NewsData.io

1. Sign in at https://newsdata.io/dashboard.
2. Copy your API key from the dashboard → `NEWSDATA_API_KEY`.

### Tavily (context enrichment for headline-only articles)

1. Go to https://app.tavily.com/home.
2. Copy the API key from the dashboard → `TAVILY_API_KEY`.

### Together AI (LLM script generation)

1. Sign in at https://api.together.ai.
2. Open **Settings → API Keys**, create a key → `TOGETHER_API_KEY`.

### Cartesia (text-to-speech)

1. Sign in at https://play.cartesia.ai.
2. Open **API Keys** in the sidebar, create a key → `CARTESIA_API_KEY`.
3. Open **Voices**, pick any voice, copy its ID → `CARTESIA_VOICE_ID`.

### Twilio WhatsApp sandbox

This is the one that actually delivers messages. Stay in **test** mode — you
don't need a WhatsApp Business account for the sandbox.

1. Sign up at https://www.twilio.com/try-twilio.
2. From the Twilio Console home page, copy **Account SID** →
   `TWILIO_ACCOUNT_SID`.
3. From the same page, click **Show** next to **Auth Token** and copy it
   → `TWILIO_AUTH_TOKEN`.
4. In the left sidebar, go to **Messaging → Try it out → Send a WhatsApp
   message**. This opens the sandbox page.
5. The sandbox gives you a **Twilio phone number** at the top of the page,
   e.g. `+1 415 523 8886`. Put it in `.env` **with the `+`**:
   ```env
   TWILIO_WHATSAPP_FROM=+14155238886
   ```
6. Also set the same number (without `+`) for referral deep links:
   ```env
   YOUR_WHATSAPP_NUMBER=14155238886
   ```
7. Note down the **join word** shown on the same page — something like
   `join violet-purple`. You'll send that as your first message from your
   phone later.

`PUBLIC_BASE_URL` is left blank for now — you'll fill it in once ngrok is
running (section 4).

### Stripe

Stripe isn't exercised by the basic "send a message, get a voice note"
loop, but `utils/config.py` requires the three vars to be set. For local
dev, use dummy test-mode values so imports succeed:

```env
STRIPE_SECRET_KEY=sk_test_dummy
STRIPE_WEBHOOK_SECRET=whsec_dummy
STRIPE_PAYMENT_LINK=https://buy.stripe.com/test_dummy
```

If you want real Stripe webhooks too, grab a test-mode secret key from
https://dashboard.stripe.com/test/apikeys and a webhook signing secret
from https://dashboard.stripe.com/test/webhooks.

### Admin, Sentry, Terms

```env
ADMIN_SECRET=<any-long-random-string>   # used for /admin/* X-Admin-Secret header
SENTRY_DSN=                              # leave blank to disable Sentry
TERMS_URL=https://example.com/terms     # any URL is fine for local
```

---

## 3. Start the stack

```bash
docker compose up --build
```

You should see four services come up:
- `squawkbox-redis-1` — Redis
- `squawkbox-app-1` — `Uvicorn running on http://0.0.0.0:8000`
- `squawkbox-worker-1` — `celery@... ready.`
- `squawkbox-beat-1` — `beat: Starting...`

The app will fail to start if any required env var is missing — the
error message will name the variable. Fix it in `.env` and re-run.

Sanity check from another terminal:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

---

## 4. Start ngrok and wire up the webhook

In a second terminal:

```bash
ngrok http 8000
```

ngrok prints a forwarding URL like
`https://abc123.ngrok-free.app -> http://localhost:8000`. Copy the
HTTPS URL.

### 4a. Tell the app about its public URL

Twilio signs every webhook using the public URL it's calling. Our signature
check needs that exact URL, so:

1. Set `PUBLIC_BASE_URL` in `.env` to the ngrok URL (no trailing slash):
   ```env
   PUBLIC_BASE_URL=https://abc123.ngrok-free.app
   ```
2. Restart the app container so it picks up the new value:
   ```bash
   docker compose restart app worker beat
   ```

### 4b. Paste the webhook URL into Twilio

1. Back in the Twilio Console, go to **Messaging → Try it out → Send a
   WhatsApp message → Sandbox settings**.
2. In **"When a message comes in"**, paste:
   ```
   https://abc123.ngrok-free.app/webhook
   ```
   and make sure the method is `HTTP POST`.
3. Click **Save**.

### 4c. Join the sandbox from your phone

1. Open WhatsApp on your phone.
2. Start a new chat with the Twilio sandbox number
   (`+1 415 523 8886` or whatever step 2.5 gave you).
3. Send the join word from step 2.7, e.g.:
   ```
   join violet-purple
   ```
4. Twilio replies confirming you've joined. Your phone number is now
   authorised to exchange messages with the sandbox for the next 72 hours.

---

## 5. Run database migrations

The Supabase project is empty — run Alembic once to create the schema:

```bash
docker compose run --rm app alembic upgrade head
```

You should see `Running upgrade  -> 001_initial_schema`. Check in
Supabase Studio (**Table Editor**) that the `users`,
`ticker_subscriptions`, `subscriptions`, `referrals`, `squawk_logs`, and
`squawk_deliveries` tables now exist.

---

## 6. Create a starter user

The normal signup flow is `START_<referral_code>`, which requires an
existing user to refer you — a chicken-and-egg problem on first run. The
quickest way to bootstrap yourself is to insert a row directly in
Supabase.

1. Open **Supabase Studio → SQL Editor → New query**.
2. Run:
   ```sql
   insert into users (phone_number, subscription_status, trial_expiry, referral_code, terms_accepted_at)
   values (
     '+15551234567',                          -- your WhatsApp number (E.164, incl +)
     'trial',
     now() + interval '7 days',
     'BOOTSTRAP',
     now()
   );
   ```
   Replace `+15551234567` with the same number you joined the sandbox
   from.

Now the app will recognise you as a trial user and accept commands like
`ADD AAPL`, `LIST`, `HELP`, `STOP`.

---

## 7. Verify it's working

Go through this checklist top-to-bottom. Each step should pass before
moving on.

- [ ] **App healthy:** `curl http://localhost:8000/health` → `{"status":"ok"}`
- [ ] **Redis connected:** `docker compose exec redis redis-cli ping` → `PONG`
- [ ] **Worker running:** `docker compose logs worker | grep "ready"` shows
      `celery@... ready.`
- [ ] **Beat running:** `docker compose logs beat | grep "Scheduler"` shows
      `Scheduler: Sending due task poll-news-every-15-minutes`
- [ ] **ngrok reachable:** open the `PUBLIC_BASE_URL` in a browser — you
      should see ngrok's warning page, click through, then you'll hit the
      app's root and see `{"detail":"Not Found"}` (that's fine — it means
      the tunnel and FastAPI are both up).
- [ ] **Twilio webhook configured:** Twilio sandbox page shows the
      `https://.../webhook` URL in the "When a message comes in" field.
- [ ] **Joined the sandbox:** you got a confirmation WhatsApp from Twilio
      after sending `join <word>-<word>`.
- [ ] **User exists:** `select * from users where phone_number = '+...';`
      in Supabase SQL Editor returns a row with `subscription_status='trial'`.
- [ ] **Text round-trip:** send `HELP` to the sandbox number — you should
      get back the command list within a few seconds. Check
      `docker compose logs app` for the webhook request.
- [ ] **ADD and LIST:** send `ADD AAPL`, then `LIST`. You should see
      `Added *AAPL* to your watchlist.` then `Your tickers: AAPL`.
- [ ] **First voice note:** voice notes arrive when the news poller finds
      an unseen article for one of your tracked tickers. The poll runs
      every 15 minutes via Celery beat. To trigger it immediately, run:
      ```bash
      docker compose exec worker python -c "from celery_worker import poll_news; poll_news()"
      ```
      Watch the worker logs — you should see
      `Processing article: ...` → `Pipeline complete for AAPL: ... delivered to 1 users`
      and receive a voice note in WhatsApp.

If any step fails, check `docker compose logs <service>` for the error.
Twilio signature failures usually mean `PUBLIC_BASE_URL` doesn't exactly
match the ngrok URL Twilio is calling — including scheme, host, and
*no* trailing slash.

---

## 8. Known unknowns

Things that are intentionally left unexplained or that you may hit and
need to figure out yourself:

- **Sandbox expiry.** The Twilio WhatsApp sandbox only talks to numbers
  that sent `join <word>-<word>` within the last 72 hours. If messages
  stop being delivered, re-send the join command.
- **ngrok URL rotation.** Free-tier ngrok gives you a new URL each
  time you restart it. Every new URL means updating both
  `PUBLIC_BASE_URL` in `.env` *and* the webhook URL in the Twilio
  sandbox settings, *and* restarting the app. Consider a paid ngrok
  reserved subdomain if you're iterating a lot.
- **Supabase Row Level Security.** The code uses the `service_role` key
  to bypass RLS. If you swap to the `anon` key, reads and writes will
  silently return empty — that's an RLS misconfiguration, not a bug.
- **Cartesia audio format.** `send_voice_note()` assumes Cartesia returns
  WAV bytes (from `services/analyst.py:248-288`) and asks FFmpeg to
  transcode to OGG/Opus. If Cartesia's default output changes, update
  the `input_format` argument.
- **YOUR_WHATSAPP_NUMBER vs TWILIO_WHATSAPP_FROM.** These look redundant
  and in the sandbox they are — both point at the sandbox number.
  `TWILIO_WHATSAPP_FROM` is used to actually send outbound messages;
  `YOUR_WHATSAPP_NUMBER` is baked into referral deep links
  (`services/referrals.py:130-137`).
- **Stripe with dummy keys.** Checkout and webhook flows won't work with
  dummy `sk_test_dummy`/`whsec_dummy` values. The app will still start
  — you just can't exercise billing end-to-end until you plug in real
  Stripe test-mode keys.
- **Voice-note media endpoint exposure.** The `/media/{token}` route is
  unauthenticated so that Twilio can fetch audio without credentials.
  Tokens are 256-bit URL-safe and expire after 10 minutes, but in a
  public deployment you may want to additionally allow-list Twilio's
  egress IPs at the reverse-proxy layer.

---

## 9. Daily dev loop

- `docker compose up` — start everything (omit `--build` after the first run)
- `docker compose logs -f app worker beat` — tail the app logs
- `docker compose run --rm app pytest tests/ -v` — run tests in the image
- `docker compose down` — stop everything
