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

*Revised 2026-10-02 after tuning on a real 500-email inbox (see section 13).* The tool is **general**:
category text is generic and nothing is learned from any user's mail.

Each email gets **one Type label**, or **only `Laya/?Unsure`** when Laya is not confident. Every label is nested under `Laya/`.

### 3.1 Type labels, asked as a two-level hierarchy

Laya first picks a **group**, and in the same call a **member** within each multi-member group. The type is the
member with the highest P(group) × P(member). Fewer options per question proved more accurate than one 11-way choice.

| Group | Label | Criteria text given to Laya (abridged; full text in `config/config.yaml`) |
|---|---|---|
| jobs | `Laya/Career` | about a job application you submitted: employer messages, interviews, assessments, onboarding |
| | `Laya/Job Alerts` | new job openings you have not applied to: hiring posts, recommendations, recruiter mass mail |
| money | `Laya/Finance` | bank alerts, statements, bills, due or overdue payments, tax, subscription billing |
| | `Laya/Orders` | online shopping: order, receipt, shipping, delivered, return |
| account | `Laya/Security` | OTP, verification code, verify device, login or security alert, password |
| | `Laya/Updates` | service notices: welcome, terms or privacy change, failed delivery, support ticket, ratings |
| travel | `Laya/Travel & Events` | flight, train, bus, ride share, hotel, booking, boarding pass, tickets |
| reading | `Laya/Newsletters` | articles, advice, tips, blogs, digests, reviews |
| | `Laya/Promotions` | offers, sales, rewards, paid courses, webinars, referral programs |
| personal | `Laya/Personal` | a personal email written by a friend or family member |
| scam | `Laya/Suspicious` | phishing or scam (offered to Laya **only for unverified senders**) |

### 3.2 Confidence and `?Unsure`

- A type label is shown only when its probability is **≥ 0.4** (`thresholds.type_conf`).
- Laya-chosen `Suspicious` needs **≥ 0.7** (`type_conf_by_type`), because a wrong Suspicious label hides real mail. Suspicious forced by the auth rules is always shown.
- Below the threshold the email gets **only `Laya/?Unsure`**, never a type guess.

### 3.3 Priority (disabled in v1)

`priority_enabled: false`. No Laya priority question is asked. Four methods were measured (urgency/needs-action
scores, a priority choice, a set of concrete yes/no signals, Laya's own email preset); none exceeded ~60% `!Act Now`
precision, so v1 shows no Laya-driven priority. Senders on the owner's VIP list (verified senders only) still get
`Laya/!Act Now`. The code for the other priority methods remains, behind config, for future work.

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

Only the topmost `Authentication-Results` header with authserv-id `mx.google.com` is trusted. `Sender verified` is `yes` when DMARC passes, or when an SPF or DKIM pass is for a domain aligned with the From domain (the same domain, or a subdomain either way). The decision is recorded on 2026-10-02. Subject and sender name have newlines and invisible characters collapsed, so they can't inject fake lines.

### 5.3 `rules.py` (pure functions, run before Laya)

| Rule | Effect |
|---|---|
| `vip` | The sender is in `vip.txt` (case-insensitive exact match, or `@domain` wildcard) and the sender is verified (DMARC pass, or SPF and DKIM both pass) → force Priority `!Act Now`. Unverified VIP mail gets no boost and is recorded as `vip_unverified` |
| `auth_fail` | `dmarc=fail`, or both SPF and DKIM fail → force Type `Suspicious` |
| `reply_to_mismatch` | The Reply-To domain differs from the From domain **and** the sender isn't verified → force Type `Suspicious` |

Rules return a `RuleHits` object. A forced Type skips the Laya type call. A forced Priority still runs Laya, so the type is known.

### 5.4 `classifier.py` (Laya wrapper)

- Uses `laya.Router(device="cpu")` with lazy loading.
- **Type:** one `predict` call with the group question plus one member question per multi-member group (`type_groups` in config). A flat single choice is still supported when `type_groups` is absent. `head_max_len: 512` raises Laya's option-text budget.
- Verified senders are never offered `suspicious`: bank and app mail quotes anti-scam warnings, which Laya mistook for phishing. Spoofing is caught by the auth rules instead.
- **Action call:** skipped entirely when `priority_enabled` is false (v1), so v1 makes **one Laya call per email** (~8.5 s on the owner's Intel CPU).
- The state is the text form (`From`, `Subject`, `Sender verified`, `Bulk sender`, `Body`, body cut to `body_limit: 600`). Laya's dict state format was measured and was not better.
- Returns a `LayaResult` with the type, `type_conf` (P(group) × P(member)), the top-2 types and the checkpoint used.

### 5.5 `decide.py` (pure)

```
type     = rules.forced_type or laya.type
needed   = type_conf_by_type.get(type, T_type)            # suspicious 0.7, else 0.4
unsure   = rules.forced_type is None and laya.type_conf < needed
priority = ActNow if rules.vip and type not in no_priority_types else None   # v1: priority disabled
labels   = [Unsure] + ([priority] if priority) if unsure else [type] + ([priority] if priority)
```

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
| Laya or fetch fails on one message | The error is logged and the message is marked `status=error, attempts+1`. Errored ids are re-queued in later cycles (whatever their age). After 3 attempts the status becomes the terminal `failed`, and the message stays **unlabelled**. The decision is recorded on 2026-10-02. |
| Gmail 404 on fetch (message deleted) | Marked `skipped`; it is not an error. |
| Most messages fail in one pass (outage) | A circuit breaker: if 3 or more new messages fail **and** more than 50% of the pass fails, the pass is aborted. No attempts are bumped for new messages, and `last_run` is not advanced. If every label-write group fails, it is treated the same way. |
| One label-write group fails | The other groups are still applied. The failed group's messages are marked `error`. |
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

The original targets (85% type accuracy on all mail, 80% `!Act Now` precision) were **not reachable with Laya
zero-shot**; see section 13. The owner chose (2026-10-02) to go live with:

1. Labels shown only above the confidence cutoff (0.4), measured at **~73% correct on held-out mail, covering ~67% of mail**; the rest is `?Unsure`.
2. Priority disabled.
3. No real mail labelled `Suspicious` in the 500-email set. Met with the 0.7 Suspicious cutoff.
4. A run of `once` on the live inbox applies labels and changes nothing else, checked manually on 20 messages.

## 11. Rollout

| Phase | Deliverable |
|---|---|
| P0 Spike | Laya runs in Docker on this Mac. Measure the per-email latency and memory on 5 synthetic emails. |
| P1 Eval | Auth works, `eval` produces a CSV from the last 500 emails, and the owner labels at least 100 rows. |
| P2 Tune | Iterate on the criteria and thresholds until section 10 is met. |
| P3 Live | `run` in label mode inside the container, auto-started. |
| v2 (later) | Fine-tune Laya on a general, public labelled email dataset (needs a GPU) to raise accuracy and make priority usable. Optionally auto-archive Promotions and Newsletters. |
| v3 (later) | Cloud Run plus Gmail Pub/Sub push for always-on, real-time triage. |

## 12. Risks and open questions

- **Accuracy ceiling:** zero-shot Laya mislabels mail written to look like something else (course marketing phrased as "Confirm your application…", career-advice newsletters about hiring). Only fine-tuning on general labelled data is expected to fix this.
- **Evaluation bias:** all measurements come from one personal inbox. Category wording was kept generic, but results should be re-checked on 1–2 differently shaped inboxes.
- **Intel CPU speed:** ~8.5 s per email. Fine for personal volume; slow for bulk backfills.
- **Hinglish or code-mixed mail:** this may route to the English checkpoint and score worse. P2 watches for it, and the fix is forcing `model="multilingual"`.
- **Calibration:** Laya's documentation recommends temperature calibration before gating on confidence. v1 uses simple thresholds tuned on the eval set, and calibration can be added if confidence turns out to be poorly calibrated.
- **Docker Desktop has to be running:** if it isn't, no triage happens, but nothing breaks either. The next run catches up thanks to the one-hour overlap.

## 13. Measured results (2026-10-02)

Evaluation set: 500 recent inbox emails, hand-labelled by type and priority. 183 of them (one per sender/subject
group) were used to compare variants; the other **317 were never used for any tuning decision (held-out)**.

| Variant (type, dev set of 183) | Accuracy, all mail |
|---|---|
| Original flat 10-way choice | 57.4% |
| Sharper criteria, 512-token option budget | 62.8% |
| Yes/no question per type | 42.1% |
| **Two-level group hierarchy (chosen)** | **66.7%** |
| Hierarchy with Laya dict state | 65.0% |

Final config, all 500 emails:

| Cutoff | Held-out: share labelled | Held-out: labels correct | All 500: labelled / correct |
|---|---|---|---|
| 0.5 | 36% | 85% | 43% / 85% |
| **0.4 (chosen)** | **67%** | **73%** | 69% / 75% |

Per-type label precision at 0.4 (all 500): Travel 23/23, Finance 29/32, Security 37/40, Career 150/200,
Job Alerts 9/31 (mostly job-themed newsletters and marketing), Personal 0/5 (ride-platform chat), Suspicious: none wrong.

Priority (oracle type, dev set): best macro F1 ≈ 0.44 for every method tried; best `!Act Now` precision ≈ 60% at ≈ 30% recall.
