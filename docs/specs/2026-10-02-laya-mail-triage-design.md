# Laya Mail Triage — Design Spec

- **Date:** 2026-10-02
- **Status:** Draft, pending review
- **Owner:** Mihir Vaze

## 1. Goal

Automatically classify every incoming email in a personal Gmail account into a useful category and priority, using **Laya** (a non-generative decision model) running locally, and apply the result as Gmail labels.

### In scope (v1)

- Personal Gmail account, accessed with the Gmail API and OAuth.
- Runs locally on the owner's Mac (Intel x86_64) inside Docker.
- **Label-only:** it adds labels and never archives, deletes, moves, sends or marks as read.
- An evaluation mode that classifies recent mail into a CSV for review and tuning without touching Gmail.

### Out of scope (v1)

- Auto-archive or other inbox changes (planned for v2, after accuracy is proven).
- Cloud hosting or real-time push through Pub/Sub (v3).
- Fine-tuning Laya. v1 tunes only the criteria text and thresholds.
- Workspace or work accounts, multiple accounts, and a UI.

## 2. Key constraints

| Constraint | Impact on design |
|---|---|
| The Mac is **Intel x86_64**. PyTorch's last Intel-Mac build is 2.2.2 (Python ≤3.12), and ONNX Runtime has no Intel-Mac build. | Laya runs in a **Linux amd64 Docker container**, which gets current CPU PyTorch builds. |
| The English Laya checkpoint reads **512 tokens** of input. | Input text is compact: headers plus the first ~1,500 characters of the body. |
| Laya shares a **192-token budget** across all option text in one call (256 for multilingual). | Questions are split into **two calls**, and the criteria text is kept short. |
| Laya does **not** support few-shot examples. | Tuning is done by editing the criteria wording and the confidence thresholds. |
| Laya is an encoder, not an LLM, and handles nested conditional rules poorly. | Criteria are plain descriptions. Hard logic lives in deterministic code rules. |
| Expected CPU speed is ~0.2–0.5 s per call. | Fine for personal volume. A 500-email backlog should take roughly 5–10 minutes. |
| An OAuth app in "Testing" status gets refresh tokens that expire after 7 days. | The OAuth app is published to "In production" and left unverified. |

## 3. Taxonomy

Each email gets **one Type label** and **zero or one Priority label**. Every label is nested under `Laya/`.

### 3.1 Type labels (Laya `choice`)

| Group | Label | Short criteria text given to Laya |
|---|---|---|
| Inbox-worthy | `Laya/Personal` | personally written by friends or family; casual conversation, not about jobs |
| | `Laya/Career` | jobs, recruiters, HR outreach, interviews, applications, freelance offers |
| | `Laya/Finance` | banks, credit cards, UPI, statements, bills, tax, insurance |
| | `Laya/Security` | OTP, verification code, login alert, password reset, account change |
| | `Laya/Travel & Events` | flights, trains, hotels, cabs, tickets, bookings, event passes |
| | `Laya/Orders` | shop or merchant purchases, order receipts, shipping, delivery, returns |
| Low priority | `Laya/Updates` | automated app, service or social notifications, product or policy changes |
| | `Laya/Newsletters` | subscribed content, blogs, digests, editorial articles |
| Junk | `Laya/Promotions` | sales, offers, discounts, marketing, limited-time deals |
| | `Laya/Suspicious` | phishing, scam, fake prize, urgent request for password or payment |

The criteria text lives in `config/config.yaml`, so it can be tuned without code changes.

### 3.2 Priority labels

| Label | Condition |
|---|---|
| `Laya/!Act Now` | The sender is on the VIP list, **or** (`needs_action` ≥ 0.6 **and** urgency ≥ "today") |
| `Laya/!This Week` | `needs_action` ≥ 0.6 **and** urgency is "this week" |
| `Laya/?Unsure` | The Type confidence is below the threshold (0.55 by default). The best-guess Type label is still applied. |

Priority labels are **never** applied to `Promotions`, `Newsletters` or `Suspicious`. The `!` and `?` prefixes sort them above the type labels in Gmail's sidebar.

## 4. Architecture

```
┌──────────────── Mac (host) ────────────────┐
│  auth.py  (one-time, host Python)          │
│     └─► secrets/token.json                 │
│                                            │
│  Docker Desktop                            │
│  ┌──────── container: triage ───────────┐  │
│  │  loop every 5 min:                    │  │
│  │   GmailClient.fetch_new()             │  │
│  │     → extract.features(msg)           │  │
│  │     → rules.pre(features)             │  │──► Gmail API (HTTPS)
│  │     → classifier.classify(features)   │  │    list / get / modify labels
│  │     → decide.final(features, laya,    │  │
│  │                    rule_hits)         │  │
│  │     → GmailClient.apply_labels()      │  │
│  │     → store.record()                  │  │
│  └───────────────────────────────────────┘  │
│   volumes: secrets/ (rw), config/ (ro),     │
│            data/ (rw), hf-cache (rw)        │
└────────────────────────────────────────────┘
```

All classification happens locally. The only network traffic is to the Gmail API, plus a one-time model download from Hugging Face.

### 4.1 Project layout

```
laya-mail-triage/
  Dockerfile
  docker-compose.yml
  requirements.txt          # container deps
  auth.py                   # host-only, one-time OAuth login
  triage/
    __init__.py
    main.py                 # CLI: eval | once | run
    config.py               # load config.yaml + vip.txt
    gmail_client.py         # list / get / labels / modify (no delete, no send)
    extract.py              # MIME → Features (pure)
    rules.py                # deterministic overrides (pure)
    classifier.py           # Laya wrapper: builds questions and calls the model
    decide.py               # Laya output + rules → labels (pure)
    store.py                # SQLite: processed ids, retries, prediction log
    pipeline.py             # classify_message(), run_once()
    evaluate.py             # eval CSV export / re-eval, scoring report
  scripts/
    spike_laya.py           # P0 feasibility spike
  config/
    config.yaml             # criteria, thresholds, label names, interval
    vip.example.txt         # format example; the real vip.txt is gitignored
  data/                     # gitignored: state.db, eval CSVs, logs
  secrets/                  # gitignored, chmod 700: credentials.json, token.json
  tests/
    helpers.py              # message builders, FakeModel, FakeGmail
    test_extract.py
    test_rules.py
    test_decide.py
    test_classifier.py      # with a fake Laya model
    test_gmail_client.py    # with a mocked API
    test_store.py, test_pipeline.py, test_evaluate.py, test_config.py
```

## 5. Components

### 5.1 `gmail_client.py`

- **Scope:** `https://www.googleapis.com/auth/gmail.modify`. This allows reading mail and changing labels. It does not allow permanent deletion, and the code never calls trash, delete or send.
- **Fetching:** `users.messages.list` with the query `in:inbox after:<last_run_epoch - 3600>`, which overlaps the previous run by an hour so nothing is missed. Message IDs already in `state.db` are skipped. Each message is then loaded with `users.messages.get(format="full")`.
- **Skipped messages:** anything labelled `SPAM` or `TRASH`, mail sent by the account owner, and messages that already carry any `Laya/*` label.
- **Labels:** on startup, every configured `Laya/*` label is created if missing (`users.labels.create`). Label IDs are cached.
- **Applying labels:** `users.messages.batchModify` with `addLabelIds` only, grouping messages that get the same label set. `removeLabelIds` is never sent.
- **Dry-run guard:** in `eval` mode the client is built with `read_only=True`, and any write method raises an error. This guarantees evaluation can't change the mailbox.
- **Retries:** API calls use `num_retries=5`, which gives exponential backoff on 429 and 5xx errors.

### 5.2 `extract.py` (pure functions)

Turns a Gmail message into a `Features` dataclass:

| Field | Source |
|---|---|
| `msg_id`, `thread_id`, `internal_date` | message metadata |
| `from_name`, `from_email`, `from_domain` | `From` header |
| `reply_to_domain` | `Reply-To` header |
| `subject` | `Subject` header |
| `body` | Prefer `text/plain`; otherwise convert `text/html` to text with BeautifulSoup. Quoted replies (`>` lines and everything after `On … wrote:`) and extra whitespace are removed, then the text is cut to 1,500 characters. |
| `is_bulk` | `List-Unsubscribe` or `Precedence: bulk/list` header present |
| `auth` | Parsed from `Authentication-Results`: `spf`, `dkim`, `dmarc` ∈ {pass, fail, none} |
| `label_ids` | the message's Gmail labels (used for skip checks) |

`to_state(features)` builds the text Laya reads:

```
From: HDFC Bank <alerts@hdfcbank.net>
Subject: Your credit card statement for September
Sender verified: yes
Bulk sender: yes
Body: Dear customer, your statement for card ending 1234 ...
```

`Sender verified` is `yes` when DMARC passes, or when both SPF and DKIM pass.

### 5.3 `rules.py` (pure functions, run before Laya)

| Rule | Effect |
|---|---|
| `vip` | The sender is in `vip.txt` (case-insensitive exact match, or `@domain` wildcard) → force Priority `!Act Now` |
| `auth_fail` | `dmarc=fail`, or both SPF and DKIM fail → force Type `Suspicious` |
| `reply_to_mismatch` | The Reply-To domain differs from the From domain **and** the sender isn't verified → force Type `Suspicious` |

Rules return a `RuleHits` object. A forced Type skips the Laya type call. A forced Priority still runs Laya, so the type is known.

### 5.4 `classifier.py` (Laya wrapper)

- Uses `laya.Router(device="cpu")` with lazy loading, so the English checkpoint is used by default. Devanagari text or non-English mail is routed automatically to `laya-multilingual`.
- Each email takes **two calls**, to stay within the per-call option budget:
  - **Call A, `type`** (`choice`): the 10 categories from section 3.1. Skipped when a rule forces the type.
  - **Call B:**
    - `needs_action` (`noul`): "Does this email ask the recipient to reply, pay, confirm, or do something?"
    - `urgency` (`score`): `["no deadline", "this month", "this week", "today", "immediately"]` (levels 0–4)
- v1 calls `predict(state, questions)` once per call, because the request format of `Router.predict_batch` is not documented. Batching is a later optimisation if eval runs are too slow.
- Returns a `LayaResult` with the type, `type_conf` (`answer_confidence`), the top-2 probabilities, `needs_action`, `urgency` (the expected level as a float) and which checkpoint was used.
- The `Router` sits behind a small interface (`predict`, `predict_batch`), so tests can inject a fake model.

### 5.5 `decide.py` (pure)

```
type     = rules.forced_type or laya.type
unsure   = rules.forced_type is None and laya.type_conf < T_type        # 0.55
priority = None
if type not in {Promotions, Newsletters, Suspicious}:
    if rules.vip:                                         priority = ActNow
    elif laya.needs_action >= T_action and urgency >= 2.5: priority = ActNow    # ≥ "today"
    elif laya.needs_action >= T_action and urgency >= 1.5: priority = ThisWeek  # ≥ "this week"
labels = [type] + ([priority] if priority else []) + ([Unsure] if unsure else [])
```

Every threshold (`T_type`, `T_action`, the urgency cut-offs) is set in `config.yaml`.

### 5.6 `store.py`

The SQLite database `data/state.db` has two tables:

- `processed(msg_id PK, processed_at, labels, status, attempts)`, where `status` is `ok`, `error` or `skipped`.
- `predictions(msg_id, run_id, from_email, subject, type, type_conf, top2, needs_action, urgency, rule_hits, model, labels)`, which holds the tuning data.

It also stores `meta(last_run_epoch)`.

### 5.7 `main.py` (CLI)

| Command | Behaviour |
|---|---|
| `python -m triage.main eval --from data/eval-<ts>.csv` | Re-classifies the same emails after tuning and keeps the `true_*` columns, so scores can be compared. |
| `python -m triage.main eval --limit 500` | **Read-only.** Classifies the last N inbox emails and writes `data/eval-<ts>.csv` with columns msg_id, date, from, subject, predicted type, confidence, top-2, needs_action, urgency, priority, rule hits, and an empty `true_type` column. No labels are applied. |
| `python -m triage.main score data/eval-<ts>.csv` | Reads a CSV with the `true_type` column filled in, then prints accuracy, a per-class confusion matrix and the precision of `!Act Now` (if a `true_priority` column is filled). |
| `python -m triage.main once` | One labelling pass over new mail. |
| `python -m triage.main run` | Repeats `once` every `interval_minutes` (default 5). This is what the container runs. |

### 5.8 `auth.py` (host, one-time)

Run with the host's Python: `pip install google-auth-oauthlib`. It uses `InstalledAppFlow.run_local_server()` with `secrets/credentials.json`, writes `secrets/token.json` with mode 600, and prints the authorised email address. The container only loads and refreshes this token. It never opens a browser.

## 6. Deployment

- **Image:** `python:3.12-slim` (`linux/amd64`). It installs PyTorch from the CPU-only wheel index (`https://download.pytorch.org/whl/cpu`) to keep the image small, then `laya` and the Google client libraries.
- **docker-compose:** a single `triage` service with `restart: unless-stopped`, a `mem_limit` of 4g, and the command `python -m triage.main run`.
- **Volumes:**
  - `./secrets:/app/secrets` (rw, because the token refresh rewrites `token.json`)
  - `./config:/app/config:ro`
  - `./data:/app/data`
  - the named volume `hf-cache:/root/.cache/huggingface`, so the model (~1.7 GB per checkpoint) downloads only once
- **Auto-start:** Docker Desktop is set to "Start when you sign in to your computer". The container's restart policy brings the service back after a reboot.
- **Logs:** written to stdout (`docker compose logs -f triage`), one line per message with the id, labels and confidence. Body text is never logged.

## 7. Error handling

| Failure | Behaviour |
|---|---|
| Gmail 429 or 5xx | The client library retries with backoff. If it still fails, the cycle is skipped and the next one retries. |
| Token revoked or expired without a refresh | A clear error tells you to re-run `auth.py`. The loop stops (exit code 2) instead of spinning. |
| Laya fails on one message | The error is logged and the message is marked `status=error, attempts+1`. Errored ids are re-queued in later cycles (whatever their age), up to 3 attempts, and then labelled `Laya/?Unsure`. |
| Message can't be parsed | Laya runs on the subject and sender alone. |
| Model download fails | The process exits non-zero, and the container restart policy retries. |

## 8. Security and privacy

- Email content never leaves the machine. Laya runs locally.
- `secrets/` has mode 700 and its files mode 600. Both `secrets/` and `data/` are in `.gitignore`.
- The `gmail.modify` scope is the minimum needed for labelling. The code makes no trash, delete, send or draft calls, and a unit test asserts the client exposes no such methods.
- The eval CSVs contain senders and subjects. They stay in `data/` (gitignored, local only).
- Access can be revoked at any time at https://myaccount.google.com/permissions.

## 9. Testing

- **Unit (pytest, host venv without torch):** `extract`, `rules` and `decide` are pure functions tested against synthetic Gmail message dicts:
  - multipart, HTML-only and quoted-reply emails
  - DMARC fail and Reply-To mismatch
  - VIP wildcard matching
  - the threshold edges
- **Classifier:** a fake model checks that the questions are built correctly, the two calls are split, and forced types are skipped.
- **Gmail client:** a mocked `googleapiclient` checks the label-creation logic, the batch grouping, the read-only guard, and that `removeLabelIds` is never sent.
- **Smoke test (real Laya, in the container):** `scripts/spike_laya.py` classifies 5 synthetic emails and reports accuracy, latency and the urgency scale.
- **Evaluation (real mail):** run `eval --limit 500`, then hand-label at least 100 rows in `true_type` and run `score`.

## 10. Acceptance criteria to go live with labelling

1. Type accuracy is at least **85%** on at least 100 hand-labelled emails.
2. `!Act Now` precision is at least **80%**. False alarms cost attention, so this matters more than recall.
3. No real bank, OTP or personal email is classified as `Suspicious` in the labelled set.
4. A run of `once` on the live inbox applies labels and changes nothing else, checked manually on 20 messages.

If a target is missed, the next steps are to adjust the criteria wording or thresholds, add a rule, or merge categories that keep getting confused (for example, Updates with Newsletters).

## 11. Rollout

| Phase | Deliverable |
|---|---|
| P0 Spike | Laya runs in Docker on this Mac. Measure the per-email latency and memory on 5 synthetic emails. |
| P1 Eval | Auth works, `eval` produces a CSV from the last 500 emails, and the owner labels at least 100 rows. |
| P2 Tune | Iterate on the criteria and thresholds until section 10 is met. |
| P3 Live | `run` in label mode inside the container, auto-started. |
| v2 (later) | Optionally auto-archive Promotions and Newsletters, and use label corrections as tuning data. |
| v3 (later) | Cloud Run plus Gmail Pub/Sub push for always-on, real-time triage. |

## 12. Risks and open questions

- **Option budget:** 10 types with short criteria may still overflow the 192-token budget. P0 will measure this. If it overflows, the fallback is a two-level choice: first Inbox-worthy / Low priority / Junk, then the sub-type.
- **Intel CPU speed:** expected to be fine, but P0 will confirm.
- **Hinglish or code-mixed mail:** this may route to the English checkpoint and score worse. P2 watches for it, and the fix is forcing `model="multilingual"`.
- **Calibration:** Laya's documentation recommends temperature calibration before gating on confidence. v1 uses simple thresholds tuned on the eval set, and calibration can be added if confidence turns out to be poorly calibrated.
- **Docker Desktop has to be running:** if it isn't, no triage happens, but nothing breaks either. The next run catches up thanks to the one-hour overlap.
