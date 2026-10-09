# Laya Mail Triage

Automatically sorts your Gmail inbox into categories using **[Laya](https://huggingface.co/convaiinnovations/laya)**, an open-source, non-generative decision model that runs **locally** in Docker. Each email gets a Gmail label such as `Laya/Finance` or `Laya/Travel & Events`.

- **Runs on your machine.** The model runs in a local container. Email content is never sent to any AI service; the only network calls go to the Gmail API (to read mail and add labels) and to Hugging Face (one-time model download).
- **Label-only.** It only *adds* labels. It never archives, deletes, moves, sends or marks mail as read.
- **Honest when unsure.** If the model isn't confident, the email gets `Laya/?Unsure` rather than a guessed category.

## Categories

| Label | What goes there |
|---|---|
| `Laya/Career` | Your job applications: employer messages, interviews, assessments, onboarding |
| `Laya/Job Alerts` | Job openings and recruiter mass mail you haven't applied to |
| `Laya/Finance` | Bank alerts, statements, bills, payments, tax, subscription billing |
| `Laya/Orders` | Online shopping: orders, receipts, shipping, deliveries |
| `Laya/Security` | OTPs, verification codes, sign-in and security alerts |
| `Laya/Updates` | Service notices: welcome emails, policy changes, delivery failures |
| `Laya/Travel & Events` | Flights, trains, buses, rides, bookings, tickets |
| `Laya/Newsletters` | Articles, tips, blogs, digests |
| `Laya/Promotions` | Offers, sales, paid courses, webinars, referral programs |
| `Laya/Personal` | Emails written by friends or family |
| `Laya/Suspicious` | Phishing or scams (mostly from failed sender authentication) |
| `Laya/?Unsure` | The model wasn't confident enough to pick a category |
| `Laya/!Act Now` | Senders on your optional VIP list |

## How it classifies

There is no keyword matching for categories. For each email:

1. **Clean the text:** strip HTML, CSS, links and quoted replies; keep the subject, sender and first 600 characters.
2. **Security rules (deterministic):** if the sender fails Gmail's SPF/DKIM/DMARC checks, or an unverified sender uses a different Reply-To domain, the email is `Suspicious`.
3. **Laya decides the category:** the model reads the email together with a plain-English description of each category (in `config/config.yaml`) and returns a probability for each. It first picks an area (jobs, money, account, travel, reading, personal), then the category within it.
4. **Confidence check:** at 0.4 or above the category label is applied; below that, only `?Unsure`. A Laya-chosen `Suspicious` needs 0.7.

You change its behaviour by editing the category descriptions in `config/config.yaml`, not code.

**Accuracy** (measured on one real inbox): about 67% of emails get a category, and about 73% of those are correct on emails never used for tuning. Mail written to look like something else (for example course marketing phrased as "Confirm your application…") is the most common mistake.

## Requirements

- **Docker Desktop** with at least **4 GB** of memory (Settings → Resources)
- **Python 3.10+** on your machine, only for the one-time Google login and the tests
- A **Google account** (personal Gmail)
- About **3 GB** of disk space (the model is about 1.7 GB)

## Setup

### 1. Create your own Google credentials (about 10 minutes)

Each person uses their own Google Cloud project. Nothing is shared.

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and **create a project**.
2. **APIs & Services → Library → Gmail API → Enable.**
3. **Google Auth Platform → Branding:** enter an app name, a support email and a developer contact email, then save.
4. **Audience:** choose **External**. Under **Test users**, add your own Gmail address.
5. **Data access → Add or remove scopes:** add `https://www.googleapis.com/auth/gmail.modify`.
6. **Clients → Create client:** application type **Desktop app**, any name, then **Create**.
7. **Download JSON** and save it as `secrets/credentials.json` in this repository.

> Keep `secrets/` private. It is gitignored. The credentials file plus the login token give access to your mailbox.

### 2. Get the code and log in once

```bash
git clone https://github.com/PewDieRes/laya-mail-triage.git
cd laya-mail-triage

python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

.venv/bin/python auth.py
```

A browser opens. Because the app is your own and unverified, Google shows a warning: click **Advanced → Go to (your app name)**, then **Allow**. This creates `secrets/token.json`.

> **The login expires every 7 days** while your app is in Testing mode, so re-run `auth.py` when you see an auth error. To stop that, click **Publish app** on the Audience page. The app stays unverified for your own use.

### 3. Set your timezone and build

```bash
cp .env.example .env              # then set TZ, e.g. TZ=Asia/Kolkata
docker compose build              # first build takes a few minutes
```

Optional: put senders who should always get `!Act Now` in `config/vip.txt`. See `config/vip.example.txt` for the format.

> **Apple Silicon Macs:** the image is pinned to `linux/amd64` (it was built on an Intel Mac) and will run under emulation, which is slow. Remove the `--platform=linux/amd64` in `Dockerfile` and the `platform:` line in `docker-compose.yml` to run natively.

## Usage

All commands run from the repository folder. The first run downloads the model (about 1.7 GB, once). Speed is roughly **8 seconds per email** on a laptop CPU.

**See what's labelled and what's still to do** (read-only):
```bash
docker compose run --rm triage python -m triage.main coverage --month 2026-10
```

**Label the newest N inbox emails:**
```bash
docker compose run --rm triage python -m triage.main once --latest 100
```

**Label a date range** (`--before` is exclusive; already-labelled mail is skipped):
```bash
docker compose run --rm triage python -m triage.main once --after 2026-09-01 --before 2026-10-01
```

**Label your whole inbox, newest month first, back to the oldest email** (resumable; waits out network drops):
```bash
./scripts/resume_backfill.sh 2026-10        # start month (YYYY-MM)
cat data/backfill-progress.txt              # progress, one line per month
```
A large inbox takes many hours on a CPU. Keep the computer awake and plugged in. If it stops, run the same command again; finished emails are skipped.

**Always-on mode** (labels new mail every 5 minutes):
```bash
docker compose up -d
docker compose logs -f triage     # Ctrl-C stops watching, not the service
docker compose stop               # stop the service
```

Labels for a run are written to Gmail together at the end of each pass.

### Measure accuracy on your own mail (optional)

```bash
docker compose run --rm triage python -m triage.main eval --limit 200   # read-only, writes data/eval-*.csv
# fill in the true_type / true_priority columns, then:
.venv/bin/python -m triage.main score data/eval-YYYYMMDD-HHMMSS.csv
```

## Configuration (`config/config.yaml`)

| Setting | What it does |
|---|---|
| `types` | Category keys, Gmail label names and the description Laya reads for each |
| `type_groups` | The two-level grouping (area, then category) |
| `thresholds.type_conf` | Minimum confidence to apply a category (default 0.4) |
| `type_conf_by_type` | Stricter minimum per category (`suspicious: 0.7`) |
| `priority_enabled` | Laya-based priority labels; off by default because they weren't reliable |
| `interval_minutes` | How often always-on mode checks for new mail |
| `body_limit`, `head_max_len` | How much email text and option text Laya reads |

## Undoing it

Delete the `Laya` label and its sub-labels in Gmail (Settings → Labels). That removes the labels and leaves every email untouched. To revoke access, go to [myaccount.google.com/permissions](https://myaccount.google.com/permissions).

## Development

```bash
.venv/bin/pytest -q            # unit tests (no Laya or Docker needed)
```

| Path | Purpose |
|---|---|
| `triage/extract.py` | Gmail message → cleaned features |
| `triage/rules.py` | Sender-authentication and VIP rules |
| `triage/classifier.py` | Laya questions and calls |
| `triage/decide.py` | Confidence thresholds → labels |
| `triage/pipeline.py` | A labelling pass: fetch, classify, add labels, retries, circuit breaker |
| `triage/gmail_client.py` | Gmail API wrapper (read and add labels only) |
| `triage/store.py` | SQLite state in `data/state.db` |
| `triage/main.py` | CLI: `eval`, `score`, `once`, `run`, `coverage` |
| `scripts/backfill_months.py` | Month-by-month whole-inbox backfill |
| `docs/` | Design spec and implementation plan |

## Limitations

- **Accuracy:** zero-shot, about 73% of applied labels correct, and many emails end up `?Unsure`. Fine-tuning Laya on a labelled email dataset would be the next step.
- **Speed:** about 8 s per email on CPU; a GPU would be about 100× faster.
- **Google limits:** an unverified app allows 100 users over its lifetime. For yourself, using your own Google project, that's not a problem.
