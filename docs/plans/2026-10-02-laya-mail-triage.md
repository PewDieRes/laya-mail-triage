# Laya Mail Triage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Label every new email in a personal Gmail inbox with a Laya-decided Type label (10 categories) and an optional Priority label. It runs locally in Docker and only ever adds labels.

**Architecture:**
- A Python package `triage/` made of small units:
  - pure functions: extract, rules, decide
  - wrappers: Laya (`classifier`), Gmail (`gmail_client`)
  - SQLite state: `store`
  - orchestration: `pipeline`, `evaluate`, `main`
- Runs in a `linux/amd64` Docker container, because PyTorch has no current Intel-Mac build.
- OAuth login happens once on the host with `auth.py`, which writes `secrets/token.json`. The container mounts that file.

**Tech Stack:**
- Python 3.12 (container) and host Python 3.14 (unit tests and auth)
- `laya` 0.3.23 (PyTorch CPU)
- `google-api-python-client`, `google-auth`, `google-auth-oauthlib` (host only)
- `beautifulsoup4`, `PyYAML`, `pytest`
- SQLite, Docker Compose

**Spec:** `docs/specs/2026-10-02-laya-mail-triage-design.md`

## Global Constraints

- **Gmail scope:** exactly `https://www.googleapis.com/auth/gmail.modify`.
- **Writes:** the only Gmail write calls allowed are `users.labels.create` and `users.messages.batchModify` with `addLabelIds`. No trash, delete, send, drafts or `removeLabelIds`, ever.
- **Label names:** every label sits under `Laya/`. The names come from `config/config.yaml` and are never hard-coded outside it.
- **Laya:** use `laya.Router(device="cpu")` and `predict(state, questions)` only. Each email gets 2 calls: type (`choice`), then `needs_action` (`noul`) plus `urgency` (`score`).
- **Laya import:** `laya` is imported lazily, inside `load_router()` only, because unit tests run on the host without torch.
- **Body text:** at most 1,500 characters is passed to Laya. Body text is never logged.
- **Thresholds** (defaults in `config.yaml`):
  - `type_conf: 0.55`
  - `needs_action: 0.6`
  - `urgency_act_now: 2.5`
  - `urgency_this_week: 1.5`
- **Never versioned:** `secrets/` (mode 700, files 600), `data/` and `config/vip.txt` are gitignored.
- **Commit messages:** plain conventional messages. **No `Co-Authored-By` or other AI-attribution lines.**
- **Running commands:**
  - Run every command from the repo root, `~/Desktop/laya-mail-triage`.
  - Host tests: `.venv/bin/pytest`.
  - Container commands: `docker compose run --rm triage …`.

---

## File map

| File | Responsibility |
|---|---|
| `.gitignore`, `.dockerignore`, `pyproject.toml` | repo hygiene, pytest config |
| `requirements-base.txt` / `requirements.txt` / `requirements-dev.txt` | shared deps / container deps (+laya) / host deps (+pytest, oauthlib) |
| `Dockerfile`, `docker-compose.yml` | container image and service |
| `config/config.yaml` | categories, criteria text, label names, thresholds |
| `config/vip.example.txt` | VIP file format example (the real `vip.txt` is gitignored) |
| `scripts/spike_laya.py` | P0 feasibility spike (not part of the app) |
| `triage/config.py` | load `config.yaml` and `vip.txt` into a `Config` |
| `triage/extract.py` | Gmail message dict → `Features`; `to_state()` |
| `triage/rules.py` | deterministic overrides → `RuleHits` |
| `triage/classifier.py` | Laya questions and calls → `LayaResult` |
| `triage/decide.py` | `LayaResult` + `RuleHits` → `Decision` and label names |
| `triage/store.py` | SQLite: processed ids, attempts, predictions, last run |
| `triage/gmail_client.py` | Gmail API wrapper with a read-only guard; credential loading |
| `triage/pipeline.py` | `classify_message()`, `run_once()` |
| `triage/evaluate.py` | eval CSV export / re-eval, scoring report |
| `triage/main.py` | CLI: `eval`, `score`, `once`, `run` |
| `auth.py` | host-only one-time OAuth login |
| `tests/helpers.py`, `tests/conftest.py`, `tests/test_*.py` | fixtures, fakes, tests |

---

### Task 1: Scaffold, Docker image and Laya feasibility spike (P0)

**Files:**
- Create: `.gitignore`
- Create: `.dockerignore`
- Create: `pyproject.toml`
- Create: `requirements-base.txt`, `requirements.txt`, `requirements-dev.txt`
- Create: `Dockerfile`, `docker-compose.yml`
- Create: `config/config.yaml`, `config/vip.example.txt`
- Create: `triage/__init__.py`
- Create: `scripts/spike_laya.py`

**Interfaces:**
- Produces: the `config/config.yaml` schema that Task 2 loads:
  - top-level keys `interval_minutes`, `type_question`, `types` (map of key → `{label, criteria}`), `no_priority_types`, `needs_action_question`, `urgency_question`, `urgency_levels`, `priority_labels` (`act_now`, `this_week`, `unsure`) and `thresholds`
  - type keys `personal`, `career`, `finance`, `security`, `travel`, `orders`, `updates`, `newsletters`, `promotions` and `suspicious`
- Produces: a Docker service named `triage` with the working directory `/app`.

- [ ] **Step 1: Initialise git and write the hygiene files**

```bash
cd ~/Desktop/laya-mail-triage && git init
```

`.gitignore`:
```gitignore
secrets/
data/
config/vip.txt
.venv/
__pycache__/
.pytest_cache/
*.pyc
```

`.dockerignore`:
```
.git
.venv
secrets
data
tests
docs
**/__pycache__
```

`pyproject.toml`:
```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

`triage/__init__.py`: an empty file.

- [ ] **Step 2: Write the requirements files**

`requirements-base.txt`:
```
google-api-python-client>=2.100
google-auth>=2.23
beautifulsoup4>=4.12
PyYAML>=6.0
```

`requirements.txt` (container):
```
-r requirements-base.txt
laya==0.3.23
```

`requirements-dev.txt` (host):
```
-r requirements-base.txt
google-auth-oauthlib>=1.1
pytest>=8.0
```

- [ ] **Step 3: Write `config/config.yaml` and `config/vip.example.txt`**

`config/config.yaml`:
```yaml
interval_minutes: 5

type_question: "What kind of email is this?"
types:
  personal:
    label: "Laya/Personal"
    criteria: "personally written by friends or family; casual conversation, not about jobs"
  career:
    label: "Laya/Career"
    criteria: "jobs, recruiters, HR outreach, interviews, applications, freelance offers"
  finance:
    label: "Laya/Finance"
    criteria: "banks, credit cards, UPI, statements, bills, tax, insurance"
  security:
    label: "Laya/Security"
    criteria: "OTP, verification code, login alert, password reset, account change"
  travel:
    label: "Laya/Travel & Events"
    criteria: "flights, trains, hotels, cabs, tickets, bookings, event passes"
  orders:
    label: "Laya/Orders"
    criteria: "shop or merchant purchases, order receipts, shipping, delivery, returns"
  updates:
    label: "Laya/Updates"
    criteria: "automated app, service or social notifications, product or policy changes"
  newsletters:
    label: "Laya/Newsletters"
    criteria: "subscribed content, blogs, digests, editorial articles"
  promotions:
    label: "Laya/Promotions"
    criteria: "sales, offers, discounts, marketing, limited-time deals"
  suspicious:
    label: "Laya/Suspicious"
    criteria: "phishing, scam, fake prize, urgent request for password or payment"

no_priority_types: [promotions, newsletters, suspicious]

needs_action_question: "Does this email ask the recipient to reply, pay, confirm, or do something?"
urgency_question: "How time-sensitive is this email?"
urgency_levels: ["no deadline", "this month", "this week", "today", "immediately"]

priority_labels:
  act_now: "Laya/!Act Now"
  this_week: "Laya/!This Week"
  unsure: "Laya/?Unsure"

thresholds:
  type_conf: 0.55
  needs_action: 0.6
  urgency_act_now: 2.5
  urgency_this_week: 1.5
```

`config/vip.example.txt`:
```
# Copy to config/vip.txt (gitignored). One entry per line.
# Exact address, or @domain for everyone at that domain.
mom@example.com
@family-domain.org
```

- [ ] **Step 4: Write `Dockerfile` and `docker-compose.yml`**

`Dockerfile`:
```dockerfile
FROM --platform=linux/amd64 python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/root/.cache/huggingface

# CPU-only torch first so laya does not pull the CUDA build.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements-base.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY triage ./triage
COPY scripts ./scripts

CMD ["python", "-m", "triage.main", "run"]
```

`docker-compose.yml`:
```yaml
services:
  triage:
    build: .
    platform: linux/amd64
    restart: unless-stopped
    mem_limit: 4g
    volumes:
      - ./secrets:/app/secrets
      - ./config:/app/config:ro
      - ./data:/app/data
      - hf-cache:/root/.cache/huggingface

volumes:
  hf-cache:
```

- [ ] **Step 5: Write `scripts/spike_laya.py`**

```python
"""P0 spike: does Laya run in this container, how fast, and is the 10-way type
question usable? Not part of the app. Run:
    docker compose run --rm triage python scripts/spike_laya.py
"""
import time
from pathlib import Path

import yaml
from laya import Router

cfg = yaml.safe_load(Path("config/config.yaml").read_text())
TYPE_Q = {
    "type": {
        "type": "choice",
        "instructions": cfg["type_question"],
        "criteria": {k: v["criteria"] for k, v in cfg["types"].items()},
    }
}
ACTION_Q = {
    "needs_action": {"type": "noul", "instructions": cfg["needs_action_question"]},
    "urgency": {"type": "score", "instructions": cfg["urgency_question"], "criteria": cfg["urgency_levels"]},
}

SAMPLES = [
    ("finance", "From: HDFC Bank <alerts@hdfcbank.net>\nSubject: Credit card statement for September\n"
                "Sender verified: yes\nBulk sender: yes\n"
                "Body: Your statement for card ending 1234 is ready. Total due Rs 12,430 by 15 Oct."),
    ("security", "From: Google <no-reply@accounts.google.com>\nSubject: Your verification code\n"
                 "Sender verified: yes\nBulk sender: no\nBody: Your code is 482913. It expires in 10 minutes."),
    ("orders", "From: Amazon.in <shipment-tracking@amazon.in>\nSubject: Your package has shipped\n"
               "Sender verified: yes\nBulk sender: yes\n"
               "Body: Your order of boAt headphones has shipped and arrives Friday."),
    ("personal", "From: Rahul <rahul.k@gmail.com>\nSubject: Saturday?\nSender verified: yes\nBulk sender: no\n"
                 "Body: Hey, are you free this Saturday for dinner? Let me know by tonight."),
    ("promotions", "From: Myntra <offers@myntra.com>\nSubject: 70% OFF ends tonight!\n"
                   "Sender verified: yes\nBulk sender: yes\n"
                   "Body: Big Fashion Sale. Extra 10% off with code STYLE10. Shop now."),
]

t0 = time.perf_counter()
router = Router(device="cpu")
router.predict("warm up", ACTION_Q)
print(f"model load + warm-up: {time.perf_counter() - t0:.1f}s")

correct, total_time = 0, 0.0
for expected, state in SAMPLES:
    t = time.perf_counter()
    type_ans = router.predict(state, TYPE_Q)["answers"]["type"]
    action = router.predict(state, ACTION_Q)
    elapsed = time.perf_counter() - t
    total_time += elapsed
    got = type_ans["choice"]
    correct += got == expected
    top = sorted(type_ans["probabilities"].items(), key=lambda kv: -kv[1])[:3]
    print(
        f"{'OK ' if got == expected else 'BAD'} expected={expected:<10} got={got:<11} "
        f"conf={type_ans['answer_confidence']:.2f} top3={top} "
        f"needs_action={action['answers']['needs_action']['noul']:.2f} "
        f"urgency={action['answers']['urgency']['score']:.2f} "
        f"model={action['routing']['model']} {elapsed:.2f}s"
    )

print(f"\ntype correct: {correct}/{len(SAMPLES)}   avg seconds/email (2 calls): {total_time / len(SAMPLES):.2f}")
```

- [ ] **Step 6: Build the image**

First check that Docker Desktop is running (`docker info` succeeds). Then:

Run: `docker compose build`
Expected: the build finishes with `naming to docker.io/library/laya-mail-triage-triage`. It takes several minutes the first time, because torch is about 200 MB.

- [ ] **Step 7: Run the spike**

Run: `docker compose run --rm triage python scripts/spike_laya.py`

Expected: the first run downloads the model (~1.7 GB) into the `hf-cache` volume, then prints 5 result lines and a summary.

**Gate. Record the results in the commit message:**
- `type correct` is **at least 4/5**.
- `avg seconds/email` is **2.0 or less**.
- Check the **urgency scale:**
  - If the Rahul email ("let me know by tonight") scores roughly 2.5–4 and Myntra or Amazon score lower, levels are 0-indexed (0–4) and the config thresholds are correct.
  - If every value falls within 0–1, the scale is normalised. Change `urgency_act_now` to `0.6` and `urgency_this_week` to `0.4` in `config/config.yaml`, then re-run.
- If type accuracy is at or below 3/5, **stop and report**. The spec §12 fallback (a two-level choice) needs a design decision before continuing.

- [ ] **Step 8: Commit**

```bash
git add .gitignore .dockerignore pyproject.toml requirements-base.txt requirements.txt requirements-dev.txt \
  Dockerfile docker-compose.yml config/config.yaml config/vip.example.txt triage/__init__.py scripts/spike_laya.py
git commit -m "chore: scaffold project, docker image, laya spike

Spike: <N>/5 types correct, <X>s per email, urgency scale <0-4|0-1>."
```

---

### Task 2: Config loader

**Files:**
- Create: `triage/config.py`
- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `config/config.yaml` (Task 1).
- Produces:
  - `Thresholds(type_conf: float, needs_action: float, urgency_act_now: float, urgency_this_week: float)`
  - `Config` with these fields:
    - `interval_minutes: int`
    - `type_question: str`
    - `type_criteria: dict[str, str]`
    - `type_labels: dict[str, str]`
    - `no_priority_types: frozenset[str]`
    - `needs_action_question: str`
    - `urgency_question: str`
    - `urgency_levels: tuple[str, ...]`
    - `priority_labels: dict[str, str]` (keys `act_now`, `this_week`, `unsure`)
    - `thresholds: Thresholds`
    - `vip: frozenset[str]`
  - The method `Config.all_label_names() -> list[str]`.
  - `load_config(config_dir: Path) -> Config` and `load_vip(path: Path) -> frozenset[str]`.
  - A pytest fixture `cfg`: the repo config with `vip={"boss@example.com", "@family.org"}`.

- [ ] **Step 1: Create the host venv**

Run: `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`
Expected: ends with `Successfully installed …`

- [ ] **Step 2: Write the failing tests**

`tests/__init__.py`: an empty file.

`tests/conftest.py`:
```python
import dataclasses
from pathlib import Path

import pytest

from triage.config import load_config

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    return dataclasses.replace(
        load_config(REPO / "config"), vip=frozenset({"boss@example.com", "@family.org"})
    )
```

`tests/test_config.py`:
```python
from pathlib import Path

import pytest
import yaml

from triage.config import load_config, load_vip

REPO = Path(__file__).resolve().parent.parent


def write_config(tmp_path, mutate):
    raw = yaml.safe_load((REPO / "config" / "config.yaml").read_text())
    mutate(raw)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw))
    return tmp_path


def test_loads_repo_config():
    cfg = load_config(REPO / "config")
    assert len(cfg.type_criteria) == 10
    assert set(cfg.type_criteria) == set(cfg.type_labels)
    assert all(name.startswith("Laya/") for name in cfg.all_label_names())
    assert len(cfg.all_label_names()) == 13
    assert cfg.no_priority_types == {"promotions", "newsletters", "suspicious"}
    assert cfg.urgency_levels[0] == "no deadline"
    assert cfg.thresholds.type_conf == 0.55


def test_vip_parsing(tmp_path):
    path = tmp_path / "vip.txt"
    path.write_text("# family\nMom@Example.com\n\n  @family.org  \n")
    assert load_vip(path) == {"mom@example.com", "@family.org"}


def test_missing_vip_file_means_empty(tmp_path):
    assert load_vip(tmp_path / "vip.txt") == frozenset()


def test_unknown_no_priority_type_rejected(tmp_path):
    config_dir = write_config(tmp_path, lambda raw: raw.update(no_priority_types=["promotions", "spam"]))
    with pytest.raises(ValueError, match="spam"):
        load_config(config_dir)


def test_suspicious_type_required(tmp_path):
    def drop_suspicious(raw):
        del raw["types"]["suspicious"]
        raw["no_priority_types"] = ["promotions"]

    with pytest.raises(ValueError, match="suspicious"):
        load_config(write_config(tmp_path, drop_suspicious))
```

- [ ] **Step 3: Run the tests to check they fail**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'triage.config'`

- [ ] **Step 4: Implement `triage/config.py`**

```python
"""Load triage settings from config/config.yaml and config/vip.txt."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Thresholds:
    type_conf: float
    needs_action: float
    urgency_act_now: float
    urgency_this_week: float


@dataclass(frozen=True)
class Config:
    interval_minutes: int
    type_question: str
    type_criteria: dict[str, str]
    type_labels: dict[str, str]
    no_priority_types: frozenset[str]
    needs_action_question: str
    urgency_question: str
    urgency_levels: tuple[str, ...]
    priority_labels: dict[str, str]
    thresholds: Thresholds
    vip: frozenset[str]

    def all_label_names(self) -> list[str]:
        return list(self.type_labels.values()) + list(self.priority_labels.values())


def load_vip(path: Path) -> frozenset[str]:
    if not path.exists():
        return frozenset()
    entries = set()
    for line in path.read_text().splitlines():
        line = line.strip().lower()
        if line and not line.startswith("#"):
            entries.add(line)
    return frozenset(entries)


def load_config(config_dir: Path) -> Config:
    raw = yaml.safe_load((config_dir / "config.yaml").read_text())
    types = raw["types"]
    if "suspicious" not in types:
        raise ValueError("types must include 'suspicious' (used by the auth rules)")
    unknown = set(raw["no_priority_types"]) - set(types)
    if unknown:
        raise ValueError(f"no_priority_types has unknown types: {sorted(unknown)}")
    return Config(
        interval_minutes=int(raw["interval_minutes"]),
        type_question=raw["type_question"],
        type_criteria={key: spec["criteria"] for key, spec in types.items()},
        type_labels={key: spec["label"] for key, spec in types.items()},
        no_priority_types=frozenset(raw["no_priority_types"]),
        needs_action_question=raw["needs_action_question"],
        urgency_question=raw["urgency_question"],
        urgency_levels=tuple(raw["urgency_levels"]),
        priority_labels=dict(raw["priority_labels"]),
        thresholds=Thresholds(**raw["thresholds"]),
        vip=load_vip(config_dir / "vip.txt"),
    )
```

- [ ] **Step 5: Run the tests to check they pass**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add triage/config.py tests/__init__.py tests/conftest.py tests/test_config.py
git commit -m "feat: config loader for categories, labels, thresholds, VIPs"
```

---

### Task 3: Message feature extraction

**Files:**
- Create: `triage/extract.py`
- Create: `tests/helpers.py`
- Test: `tests/test_extract.py`

**Interfaces:**
- Consumes: none.
- Produces:
  - `Auth(spf: str = "none", dkim: str = "none", dmarc: str = "none")`. Values are `"pass"`, `"fail"` or `"none"`. It has the property `verified -> bool`.
  - `Features` with these fields:
    - `msg_id: str`, `thread_id: str`
    - `internal_date: int` (ms)
    - `from_name: str`, `from_email: str` (lowercased), `from_domain: str`
    - `reply_to_domain: str | None`
    - `subject: str`
    - `body: str` (≤1500)
    - `is_bulk: bool`
    - `auth: Auth`
    - `label_ids: tuple[str, ...]`
  - `parse_message(msg: dict) -> Features`, `to_state(f: Features) -> str`, `clean_body(text: str) -> str`, `parse_auth(values: list[str]) -> Auth`, and `BODY_LIMIT = 1500`.
  - Test helpers: `b64(text) -> str`, `make_msg(msg_id="m1", headers=None, plain=None, html=None, label_ids=("INBOX",)) -> dict` and `make_features(**overrides) -> Features`.

- [ ] **Step 1: Write the test helpers**

`tests/helpers.py`:
```python
"""Builders and fakes shared by tests."""
import base64
from dataclasses import replace

from triage.extract import Auth, Features


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def make_msg(msg_id="m1", headers=None, plain=None, html=None, label_ids=("INBOX",)):
    """A Gmail API users.messages.get(format="full") response."""
    parts = []
    if plain is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": b64(plain)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": b64(html)}})
    return {
        "id": msg_id,
        "threadId": f"t-{msg_id}",
        "internalDate": "1790000000000",
        "labelIds": list(label_ids),
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [{"name": k, "value": v} for k, v in (headers or {}).items()],
            "body": {},
            "parts": parts,
        },
    }


def make_features(**overrides) -> Features:
    base = Features(
        msg_id="m1",
        thread_id="t1",
        internal_date=1790000000000,
        from_name="Alice",
        from_email="alice@example.com",
        from_domain="example.com",
        reply_to_domain=None,
        subject="Hello",
        body="Hi there",
        is_bulk=False,
        auth=Auth(spf="pass", dkim="pass", dmarc="pass"),
        label_ids=("INBOX",),
    )
    return replace(base, **overrides)
```

- [ ] **Step 2: Write the failing tests**

`tests/test_extract.py`:
```python
import base64

from tests.helpers import b64, make_features, make_msg
from triage.extract import BODY_LIMIT, Auth, clean_body, parse_auth, parse_message, to_state


def test_parses_sender_subject_and_ids():
    f = parse_message(make_msg(
        "m9",
        headers={"From": "HDFC Bank <Alerts@HDFCbank.net>", "Subject": "Statement ready",
                 "Reply-To": "help@hdfcbank.net"},
        plain="Hello",
    ))
    assert (f.msg_id, f.thread_id, f.internal_date) == ("m9", "t-m9", 1790000000000)
    assert f.from_name == "HDFC Bank"
    assert f.from_email == "alerts@hdfcbank.net"
    assert f.from_domain == "hdfcbank.net"
    assert f.reply_to_domain == "hdfcbank.net"
    assert f.subject == "Statement ready"
    assert f.label_ids == ("INBOX",)


def test_missing_reply_to_is_none():
    assert parse_message(make_msg(headers={"From": "a@b.com"}, plain="x")).reply_to_domain is None


def test_prefers_plain_over_html():
    f = parse_message(make_msg(headers={"From": "a@b.com"}, plain="plain text", html="<p>html text</p>"))
    assert f.body == "plain text"


def test_html_only_is_converted_to_text():
    html = "<html><style>p{color:red}</style><body><p>Hello</p><p>World</p><script>x()</script></body></html>"
    assert parse_message(make_msg(headers={"From": "a@b.com"}, html=html)).body == "Hello World"


def test_nested_multipart_is_searched():
    msg = make_msg(headers={"From": "a@b.com"})
    msg["payload"]["parts"] = [
        {"mimeType": "multipart/alternative",
         "parts": [{"mimeType": "text/plain", "body": {"data": b64("deep")}}]}
    ]
    assert parse_message(msg).body == "deep"


def test_quoted_reply_removed():
    text = "Sounds good.\n\nOn Mon, 1 Oct 2026, Bob <b@x.com> wrote:\n> earlier text"
    assert clean_body(text) == "Sounds good."


def test_quote_lines_removed():
    assert clean_body("Yes\n> old\nThanks") == "Yes Thanks"


def test_body_truncated():
    assert len(clean_body("a " * 2000)) == BODY_LIMIT


def test_bulk_from_list_unsubscribe():
    msg = make_msg(headers={"From": "a@b.com", "List-Unsubscribe": "<mailto:u@b.com>"}, plain="x")
    assert parse_message(msg).is_bulk


def test_bulk_from_precedence():
    assert parse_message(make_msg(headers={"From": "a@b.com", "Precedence": "Bulk"}, plain="x")).is_bulk


def test_not_bulk_by_default():
    assert not parse_message(make_msg(headers={"From": "a@b.com"}, plain="x")).is_bulk


def test_parse_auth_normalises_results():
    header = ("mx.google.com; dkim=pass header.i=@x.com; spf=softfail smtp.mailfrom=x.com; "
              "dmarc=fail (p=NONE) header.from=x.com")
    assert parse_auth([header]) == Auth(spf="fail", dkim="pass", dmarc="fail")


def test_parse_auth_missing_header():
    assert parse_auth([]) == Auth(spf="none", dkim="none", dmarc="none")


def test_auth_header_read_from_message():
    msg = make_msg(headers={"From": "a@b.com",
                            "Authentication-Results": "mx.google.com; dkim=pass; spf=pass; dmarc=pass"},
                   plain="x")
    assert parse_message(msg).auth.verified


def test_verified_rules():
    assert Auth(dmarc="pass").verified
    assert Auth(spf="pass", dkim="pass").verified
    assert not Auth(spf="pass").verified


def test_encoded_subject_decoded():
    encoded = "=?UTF-8?B?" + base64.b64encode("Café ☕".encode()).decode() + "?="
    assert parse_message(make_msg(headers={"From": "a@b.com", "Subject": encoded}, plain="x")).subject == "Café ☕"


def test_to_state_format():
    f = make_features(from_name="Alice", from_email="alice@example.com", subject="Hi", body="See you")
    assert to_state(f) == (
        "From: Alice <alice@example.com>\nSubject: Hi\nSender verified: yes\nBulk sender: no\nBody: See you"
    )


def test_to_state_without_name():
    assert to_state(make_features(from_name="")).startswith("From: alice@example.com\n")
```

- [ ] **Step 3: Run the tests to check they fail**

Run: `.venv/bin/pytest tests/test_extract.py -v`
Expected: collection error `No module named 'triage.extract'`

- [ ] **Step 4: Implement `triage/extract.py`**

```python
"""Turn a Gmail API message (format="full") into Features for rules and Laya."""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.utils import parseaddr

from bs4 import BeautifulSoup

BODY_LIMIT = 1500
_AUTH_RE = re.compile(r"\b(spf|dkim|dmarc)=([a-z]+)", re.IGNORECASE)
_WROTE_RE = re.compile(r"^\s*On .+wrote:\s*$", re.MULTILINE)
_FAIL_RESULTS = {"fail", "softfail"}
_BULK_PRECEDENCE = {"bulk", "list", "junk"}


@dataclass(frozen=True)
class Auth:
    spf: str = "none"
    dkim: str = "none"
    dmarc: str = "none"

    @property
    def verified(self) -> bool:
        return self.dmarc == "pass" or (self.spf == "pass" and self.dkim == "pass")


@dataclass(frozen=True)
class Features:
    msg_id: str
    thread_id: str
    internal_date: int
    from_name: str
    from_email: str
    from_domain: str
    reply_to_domain: str | None
    subject: str
    body: str
    is_bulk: bool
    auth: Auth
    label_ids: tuple[str, ...]


def _headers(payload: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for header in payload.get("headers", []):
        out.setdefault(header["name"].lower(), []).append(header["value"])
    return out


def _decode_header(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _decode_data(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _find_part(payload: dict, mime: str) -> str | None:
    if payload.get("mimeType") == mime and payload.get("body", {}).get("data"):
        return _decode_data(payload["body"]["data"])
    for part in payload.get("parts") or []:
        found = _find_part(part, mime)
        if found is not None:
            return found
    return None


def clean_body(text: str) -> str:
    match = _WROTE_RE.search(text)
    if match:
        text = text[: match.start()]
    lines = [line for line in text.splitlines() if not line.lstrip().startswith(">")]
    return re.sub(r"\s+", " ", " ".join(lines)).strip()[:BODY_LIMIT]


def _body(payload: dict) -> str:
    plain = _find_part(payload, "text/plain")
    if plain is not None:
        return clean_body(plain)
    html = _find_part(payload, "text/html")
    if html is None:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return clean_body(soup.get_text("\n"))


def parse_auth(values: list[str]) -> Auth:
    found: dict[str, str] = {}
    for value in values:
        for mech, result in _AUTH_RE.findall(value):
            mech, result = mech.lower(), result.lower()
            if mech in found:
                continue
            if result == "pass":
                found[mech] = "pass"
            elif result in _FAIL_RESULTS:
                found[mech] = "fail"
            else:
                found[mech] = "none"
    return Auth(**found)


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def parse_message(msg: dict) -> Features:
    payload = msg.get("payload", {})
    headers = _headers(payload)

    def first(name: str) -> str:
        return headers.get(name, [""])[0]

    from_name, from_email = parseaddr(first("from"))
    _, reply_to = parseaddr(first("reply-to"))
    precedence = first("precedence").strip().lower()
    return Features(
        msg_id=msg["id"],
        thread_id=msg.get("threadId", ""),
        internal_date=int(msg.get("internalDate", 0)),
        from_name=_decode_header(from_name),
        from_email=from_email.lower(),
        from_domain=_domain(from_email),
        reply_to_domain=_domain(reply_to) or None,
        subject=_decode_header(first("subject")),
        body=_body(payload),
        is_bulk="list-unsubscribe" in headers or precedence in _BULK_PRECEDENCE,
        auth=parse_auth(headers.get("authentication-results", [])),
        label_ids=tuple(msg.get("labelIds", [])),
    )


def _yes_no(flag: bool) -> str:
    return "yes" if flag else "no"


def to_state(f: Features) -> str:
    sender = f"{f.from_name} <{f.from_email}>" if f.from_name else f.from_email
    return (
        f"From: {sender}\n"
        f"Subject: {f.subject}\n"
        f"Sender verified: {_yes_no(f.auth.verified)}\n"
        f"Bulk sender: {_yes_no(f.is_bulk)}\n"
        f"Body: {f.body}"
    )
```

- [ ] **Step 5: Run the tests to check they pass**

Run: `.venv/bin/pytest tests/test_extract.py -v`
Expected: 18 passed

- [ ] **Step 6: Commit**

```bash
git add triage/extract.py tests/helpers.py tests/test_extract.py
git commit -m "feat: extract sender, body, bulk and auth features from Gmail messages"
```

---

### Task 4: Deterministic rules

**Files:**
- Create: `triage/rules.py`
- Test: `tests/test_rules.py`

**Interfaces:**
- Consumes: `Features` and `Auth` from `triage.extract`, and `make_features` from `tests.helpers`.
- Produces:
  - `RuleHits(forced_type: str | None = None, vip: bool = False, names: tuple[str, ...] = ())`
  - `is_vip(email: str, vip: frozenset[str]) -> bool`
  - `apply_rules(f: Features, vip: frozenset[str]) -> RuleHits`
  - The rule names are `"auth_fail"`, `"reply_to_mismatch"` and `"vip"`, in that order. The forced type is always `"suspicious"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_rules.py`:
```python
from tests.helpers import make_features
from triage.extract import Auth
from triage.rules import RuleHits, apply_rules, is_vip

VIP = frozenset({"boss@example.com", "@family.org"})
UNVERIFIED = Auth(spf="none", dkim="none", dmarc="none")


def test_clean_mail_hits_nothing():
    assert apply_rules(make_features(), VIP) == RuleHits()


def test_vip_exact_match_ignores_case():
    assert is_vip("Boss@Example.com", VIP)


def test_vip_domain_wildcard():
    assert is_vip("mom@family.org", VIP)
    assert not is_vip("x@notfamily.org", VIP)


def test_vip_rule_sets_flag_only():
    hits = apply_rules(make_features(from_email="boss@example.com"), VIP)
    assert hits == RuleHits(forced_type=None, vip=True, names=("vip",))


def test_dmarc_fail_forces_suspicious():
    hits = apply_rules(make_features(auth=Auth(spf="pass", dkim="pass", dmarc="fail")), VIP)
    assert hits == RuleHits(forced_type="suspicious", vip=False, names=("auth_fail",))


def test_spf_and_dkim_fail_forces_suspicious():
    hits = apply_rules(make_features(auth=Auth(spf="fail", dkim="fail", dmarc="none")), VIP)
    assert hits.forced_type == "suspicious"


def test_single_failure_is_not_enough():
    assert apply_rules(make_features(auth=Auth(spf="fail", dkim="pass", dmarc="none")), VIP).forced_type is None


def test_reply_to_mismatch_on_unverified_sender():
    hits = apply_rules(make_features(reply_to_domain="evil.example", auth=UNVERIFIED), VIP)
    assert hits == RuleHits(forced_type="suspicious", vip=False, names=("reply_to_mismatch",))


def test_reply_to_mismatch_on_verified_sender_is_fine():
    assert apply_rules(make_features(reply_to_domain="mailer.example"), VIP).forced_type is None


def test_spoofed_vip_is_still_suspicious():
    f = make_features(from_email="boss@example.com", auth=Auth(spf="fail", dkim="fail", dmarc="fail"))
    hits = apply_rules(f, VIP)
    assert hits == RuleHits(forced_type="suspicious", vip=True, names=("auth_fail", "vip"))
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `.venv/bin/pytest tests/test_rules.py -v`
Expected: collection error `No module named 'triage.rules'`

- [ ] **Step 3: Implement `triage/rules.py`**

```python
"""Deterministic rules that run before Laya. They decide facts Laya cannot see
from text alone: sender authentication and the owner's VIP list."""
from __future__ import annotations

from dataclasses import dataclass

from triage.extract import Features

SUSPICIOUS = "suspicious"


@dataclass(frozen=True)
class RuleHits:
    forced_type: str | None = None
    vip: bool = False
    names: tuple[str, ...] = ()


def is_vip(email: str, vip: frozenset[str]) -> bool:
    email = email.lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    return email in vip or (bool(domain) and f"@{domain}" in vip)


def apply_rules(f: Features, vip: frozenset[str]) -> RuleHits:
    names: list[str] = []
    forced_type = None
    if f.auth.dmarc == "fail" or (f.auth.spf == "fail" and f.auth.dkim == "fail"):
        names.append("auth_fail")
        forced_type = SUSPICIOUS
    if f.reply_to_domain and f.reply_to_domain != f.from_domain and not f.auth.verified:
        names.append("reply_to_mismatch")
        forced_type = SUSPICIOUS
    vip_hit = is_vip(f.from_email, vip)
    if vip_hit:
        names.append("vip")
    return RuleHits(forced_type=forced_type, vip=vip_hit, names=tuple(names))
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `.venv/bin/pytest tests/test_rules.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add triage/rules.py tests/test_rules.py
git commit -m "feat: auth-failure, reply-to mismatch and VIP rules"
```

---

### Task 5: Laya classifier wrapper

**Files:**
- Create: `triage/classifier.py`
- Modify: `tests/helpers.py` (append `FakeModel`)
- Test: `tests/test_classifier.py`

**Interfaces:**
- Consumes: `Config` (Task 2) and the `cfg` fixture.
- Produces:
  - `Model` protocol: `predict(state: str, questions: dict) -> dict`.
  - `LayaResult` with these fields:
    - `type: str | None`
    - `type_conf: float`
    - `top2: tuple[tuple[str, float], ...]`
    - `needs_action: float`
    - `urgency: float`
    - `model: str`
  - `load_router() -> Model` (lazy `laya` import).
  - `Classifier(cfg: Config, model: Model)` with:
    - `.type_questions() -> dict`
    - `.action_questions() -> dict`
    - `.classify(state: str, skip_type: bool = False) -> LayaResult`
  - Test helper: `FakeModel(type_="finance", conf=0.9, needs_action=0.1, urgency=0.5, probs=None)`, which records `.calls: list[tuple[str, dict]]`.

- [ ] **Step 1: Append `FakeModel` to `tests/helpers.py`**

```python
class FakeModel:
    """Stands in for laya.Router: fixed answers, records every call."""

    def __init__(self, type_="finance", conf=0.9, needs_action=0.1, urgency=0.5, probs=None):
        self.type_ = type_
        self.conf = conf
        self.needs_action = needs_action
        self.urgency = urgency
        self.probs = probs
        self.calls = []

    def predict(self, state, questions):
        self.calls.append((state, questions))
        answers = {}
        if "type" in questions:
            answers["type"] = {
                "choice": self.type_,
                "answer_confidence": self.conf,
                "probabilities": self.probs or {self.type_: self.conf},
            }
        if "needs_action" in questions:
            answers["needs_action"] = {"noul": self.needs_action}
        if "urgency" in questions:
            answers["urgency"] = {"score": self.urgency}
        return {"answers": answers, "routing": {"model": "english"}}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_classifier.py`:
```python
from tests.helpers import FakeModel
from triage.classifier import Classifier


def test_two_calls_with_split_questions(cfg):
    model = FakeModel(type_="finance", conf=0.8, needs_action=0.7, urgency=3.1,
                      probs={"finance": 0.8, "orders": 0.15, "updates": 0.05})
    result = Classifier(cfg, model).classify("state text")
    assert [set(questions) for _, questions in model.calls] == [{"type"}, {"needs_action", "urgency"}]
    assert all(state == "state text" for state, _ in model.calls)
    assert result.type == "finance"
    assert result.type_conf == 0.8
    assert result.top2 == (("finance", 0.8), ("orders", 0.15))
    assert (result.needs_action, result.urgency, result.model) == (0.7, 3.1, "english")


def test_type_question_uses_config(cfg):
    question = Classifier(cfg, FakeModel()).type_questions()["type"]
    assert question["type"] == "choice"
    assert question["instructions"] == cfg.type_question
    assert question["criteria"] == cfg.type_criteria


def test_action_questions_shape(cfg):
    questions = Classifier(cfg, FakeModel()).action_questions()
    assert questions["needs_action"] == {"type": "noul", "instructions": cfg.needs_action_question}
    assert questions["urgency"]["type"] == "score"
    assert questions["urgency"]["criteria"] == list(cfg.urgency_levels)


def test_skip_type_makes_one_call(cfg):
    model = FakeModel()
    result = Classifier(cfg, model).classify("s", skip_type=True)
    assert len(model.calls) == 1
    assert (result.type, result.type_conf, result.top2) == (None, 1.0, ())
```

- [ ] **Step 3: Run the tests to check they fail**

Run: `.venv/bin/pytest tests/test_classifier.py -v`
Expected: collection error `No module named 'triage.classifier'`

- [ ] **Step 4: Implement `triage/classifier.py`**

```python
"""Laya wrapper. Two calls per email keep each call inside Laya's option-text
budget: the 10-way type choice, then needs_action + urgency."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from triage.config import Config


class Model(Protocol):
    def predict(self, state: str, questions: dict) -> dict: ...


@dataclass(frozen=True)
class LayaResult:
    type: str | None
    type_conf: float
    top2: tuple[tuple[str, float], ...]
    needs_action: float
    urgency: float
    model: str


def load_router() -> Model:
    # Imported here so host-side tests run without laya/torch installed.
    from laya import Router

    return Router(device="cpu")


class Classifier:
    def __init__(self, cfg: Config, model: Model):
        self.cfg = cfg
        self.model = model

    def type_questions(self) -> dict:
        return {
            "type": {
                "type": "choice",
                "instructions": self.cfg.type_question,
                "criteria": dict(self.cfg.type_criteria),
            }
        }

    def action_questions(self) -> dict:
        return {
            "needs_action": {"type": "noul", "instructions": self.cfg.needs_action_question},
            "urgency": {
                "type": "score",
                "instructions": self.cfg.urgency_question,
                "criteria": list(self.cfg.urgency_levels),
            },
        }

    def classify(self, state: str, skip_type: bool = False) -> LayaResult:
        type_, type_conf, top2 = None, 1.0, ()
        if not skip_type:
            answer = self.model.predict(state, self.type_questions())["answers"]["type"]
            type_ = answer["choice"]
            type_conf = float(answer["answer_confidence"])
            ranked = sorted(answer["probabilities"].items(), key=lambda kv: -kv[1])
            top2 = tuple((label, float(p)) for label, p in ranked[:2])
        result = self.model.predict(state, self.action_questions())
        answers = result["answers"]
        return LayaResult(
            type=type_,
            type_conf=type_conf,
            top2=top2,
            needs_action=float(answers["needs_action"]["noul"]),
            urgency=float(answers["urgency"]["score"]),
            model=result.get("routing", {}).get("model", "unknown"),
        )
```

- [ ] **Step 5: Run the tests to check they pass**

Run: `.venv/bin/pytest tests/test_classifier.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add triage/classifier.py tests/helpers.py tests/test_classifier.py
git commit -m "feat: Laya classifier wrapper with split type and action calls"
```

---

### Task 6: Decision logic

**Files:**
- Create: `triage/decide.py`
- Modify: `tests/helpers.py` (append `make_laya`)
- Test: `tests/test_decide.py`

**Interfaces:**
- Consumes: `LayaResult` (Task 5), `RuleHits` (Task 4) and `Config` (Task 2).
- Produces:
  - `Decision(type: str, priority: str | None, unsure: bool)`, where `priority` is `"act_now"`, `"this_week"` or `None`.
  - `Decision.label_names(cfg: Config) -> list[str]`, which returns labels in the order type, priority, unsure.
  - `decide(laya: LayaResult, hits: RuleHits, cfg: Config) -> Decision`.
  - Test helper: `make_laya(**overrides) -> LayaResult`, with defaults: finance, conf 0.9, needs_action 0.1, urgency 0.5.

- [ ] **Step 1: Append `make_laya` to `tests/helpers.py`**

```python
from triage.classifier import LayaResult


def make_laya(**overrides) -> LayaResult:
    base = LayaResult(type="finance", type_conf=0.9, top2=(("finance", 0.9),),
                      needs_action=0.1, urgency=0.5, model="english")
    return replace(base, **overrides)
```

Move the `from triage.classifier import LayaResult` line up into the import block at the top of `tests/helpers.py`.

- [ ] **Step 2: Write the failing tests**

`tests/test_decide.py`:
```python
import pytest

from tests.helpers import make_laya
from triage.decide import Decision, decide
from triage.rules import RuleHits


def test_informational_mail_gets_type_only(cfg):
    decision = decide(make_laya(), RuleHits(), cfg)
    assert decision == Decision("finance", None, False)
    assert decision.label_names(cfg) == ["Laya/Finance"]


def test_action_today_is_act_now(cfg):
    decision = decide(make_laya(needs_action=0.8, urgency=3.0), RuleHits(), cfg)
    assert decision.priority == "act_now"
    assert decision.label_names(cfg) == ["Laya/Finance", "Laya/!Act Now"]


def test_action_this_week(cfg):
    assert decide(make_laya(needs_action=0.8, urgency=2.0), RuleHits(), cfg).priority == "this_week"


def test_action_without_deadline_has_no_priority(cfg):
    assert decide(make_laya(needs_action=0.8, urgency=1.0), RuleHits(), cfg).priority is None


def test_urgent_but_no_action_has_no_priority(cfg):
    assert decide(make_laya(needs_action=0.5, urgency=4.0), RuleHits(), cfg).priority is None


def test_threshold_edges_are_inclusive(cfg):
    assert decide(make_laya(needs_action=0.6, urgency=2.5), RuleHits(), cfg).priority == "act_now"
    assert decide(make_laya(needs_action=0.6, urgency=1.5), RuleHits(), cfg).priority == "this_week"


def test_vip_forces_act_now(cfg):
    assert decide(make_laya(), RuleHits(vip=True, names=("vip",)), cfg).priority == "act_now"


def test_no_priority_for_promotions_even_from_vip(cfg):
    decision = decide(make_laya(type="promotions", needs_action=0.9, urgency=4.0),
                      RuleHits(vip=True, names=("vip",)), cfg)
    assert decision.priority is None


def test_forced_type_overrides_laya(cfg):
    hits = RuleHits(forced_type="suspicious", vip=True, names=("auth_fail", "vip"))
    decision = decide(make_laya(type=None, type_conf=1.0, top2=()), hits, cfg)
    assert decision == Decision("suspicious", None, False)
    assert decision.label_names(cfg) == ["Laya/Suspicious"]


def test_low_confidence_marks_unsure(cfg):
    decision = decide(make_laya(type_conf=0.4), RuleHits(), cfg)
    assert decision.unsure
    assert decision.label_names(cfg) == ["Laya/Finance", "Laya/?Unsure"]


def test_missing_type_raises(cfg):
    with pytest.raises(ValueError):
        decide(make_laya(type=None), RuleHits(), cfg)
```

- [ ] **Step 3: Run the tests to check they fail**

Run: `.venv/bin/pytest tests/test_decide.py -v`
Expected: collection error `No module named 'triage.decide'`

- [ ] **Step 4: Implement `triage/decide.py`**

```python
"""Combine Laya output and rule hits into the labels to apply."""
from __future__ import annotations

from dataclasses import dataclass

from triage.classifier import LayaResult
from triage.config import Config
from triage.rules import RuleHits


@dataclass(frozen=True)
class Decision:
    type: str
    priority: str | None
    unsure: bool

    def label_names(self, cfg: Config) -> list[str]:
        names = [cfg.type_labels[self.type]]
        if self.priority:
            names.append(cfg.priority_labels[self.priority])
        if self.unsure:
            names.append(cfg.priority_labels["unsure"])
        return names


def decide(laya: LayaResult, hits: RuleHits, cfg: Config) -> Decision:
    t = cfg.thresholds
    type_ = hits.forced_type or laya.type
    if type_ is None:
        raise ValueError("no type from rules or Laya")
    unsure = hits.forced_type is None and laya.type_conf < t.type_conf
    priority = None
    if type_ not in cfg.no_priority_types:
        needs_action = laya.needs_action >= t.needs_action
        if hits.vip or (needs_action and laya.urgency >= t.urgency_act_now):
            priority = "act_now"
        elif needs_action and laya.urgency >= t.urgency_this_week:
            priority = "this_week"
    return Decision(type=type_, priority=priority, unsure=unsure)
```

- [ ] **Step 5: Run the tests to check they pass**

Run: `.venv/bin/pytest tests/test_decide.py -v`
Expected: 11 passed

- [ ] **Step 6: Commit**

```bash
git add triage/decide.py tests/helpers.py tests/test_decide.py
git commit -m "feat: decision logic for type, priority and unsure labels"
```

---

### Task 7: SQLite state store

**Files:**
- Create: `triage/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Features`, `LayaResult`, `RuleHits`, and the helpers `make_features` and `make_laya`.
- Produces: `Store(path: Path)` with these methods:
  - `.is_done(msg_id) -> bool` (status `ok` or `skipped`)
  - `.attempts(msg_id) -> int`
  - `.retry_ids() -> list[str]` (status `error`)
  - `.mark(msg_id: str, status: str, labels: list[str]) -> None` (an `error` adds 1 to attempts)
  - `.log_prediction(run_id: str, f: Features, laya: LayaResult, hits: RuleHits, labels: list[str]) -> None`
  - `.get_last_run() -> int | None` and `.set_last_run(epoch: int) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/test_store.py`:
```python
import json

from tests.helpers import make_features, make_laya
from triage.rules import RuleHits
from triage.store import Store


def test_new_message_is_not_done(tmp_path):
    store = Store(tmp_path / "s.db")
    assert not store.is_done("m1")
    assert store.attempts("m1") == 0


def test_ok_and_skipped_are_done(tmp_path):
    store = Store(tmp_path / "s.db")
    store.mark("m1", "ok", ["Laya/Finance"])
    store.mark("m2", "skipped", [])
    assert store.is_done("m1") and store.is_done("m2")


def test_errors_count_attempts_and_queue_retry(tmp_path):
    store = Store(tmp_path / "s.db")
    store.mark("m1", "error", [])
    store.mark("m1", "error", [])
    assert store.attempts("m1") == 2
    assert not store.is_done("m1")
    assert store.retry_ids() == ["m1"]


def test_ok_after_error_clears_retry(tmp_path):
    store = Store(tmp_path / "s.db")
    store.mark("m1", "error", [])
    store.mark("m1", "ok", ["Laya/?Unsure"])
    assert store.retry_ids() == []
    assert store.attempts("m1") == 1


def test_last_run_roundtrip(tmp_path):
    store = Store(tmp_path / "s.db")
    assert store.get_last_run() is None
    store.set_last_run(123)
    store.set_last_run(456)
    assert store.get_last_run() == 456


def test_log_prediction_writes_row(tmp_path):
    store = Store(tmp_path / "s.db")
    store.log_prediction("run1", make_features(), make_laya(), RuleHits(vip=True, names=("vip",)),
                         ["Laya/Finance", "Laya/!Act Now"])
    row = store.db.execute("SELECT msg_id, run_id, type, rule_hits, labels FROM predictions").fetchone()
    assert row[:3] == ("m1", "run1", "finance")
    assert json.loads(row[3]) == ["vip"]
    assert json.loads(row[4]) == ["Laya/Finance", "Laya/!Act Now"]


def test_state_persists_across_instances(tmp_path):
    Store(tmp_path / "s.db").mark("m1", "ok", [])
    assert Store(tmp_path / "s.db").is_done("m1")
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `.venv/bin/pytest tests/test_store.py -v`
Expected: collection error `No module named 'triage.store'`

- [ ] **Step 3: Implement `triage/store.py`**

```python
"""SQLite state: which messages are handled, retry counts, prediction log, last run."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from triage.classifier import LayaResult
from triage.extract import Features
from triage.rules import RuleHits

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processed (
    msg_id TEXT PRIMARY KEY,
    processed_at INTEGER NOT NULL,
    labels TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS predictions (
    msg_id TEXT, run_id TEXT, from_email TEXT, subject TEXT, type TEXT, type_conf REAL,
    top2 TEXT, needs_action REAL, urgency REAL, rule_hits TEXT, model TEXT, labels TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(_SCHEMA)

    def is_done(self, msg_id: str) -> bool:
        row = self.db.execute("SELECT status FROM processed WHERE msg_id = ?", (msg_id,)).fetchone()
        return row is not None and row[0] in ("ok", "skipped")

    def attempts(self, msg_id: str) -> int:
        row = self.db.execute("SELECT attempts FROM processed WHERE msg_id = ?", (msg_id,)).fetchone()
        return row[0] if row else 0

    def retry_ids(self) -> list[str]:
        rows = self.db.execute("SELECT msg_id FROM processed WHERE status = 'error' ORDER BY msg_id")
        return [row[0] for row in rows]

    def mark(self, msg_id: str, status: str, labels: list[str]) -> None:
        bump = 1 if status == "error" else 0
        self.db.execute(
            "INSERT INTO processed (msg_id, processed_at, labels, status, attempts) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(msg_id) DO UPDATE SET processed_at = excluded.processed_at, "
            "labels = excluded.labels, status = excluded.status, "
            "attempts = processed.attempts + excluded.attempts",
            (msg_id, int(time.time()), json.dumps(labels), status, bump),
        )
        self.db.commit()

    def log_prediction(self, run_id: str, f: Features, laya: LayaResult, hits: RuleHits,
                       labels: list[str]) -> None:
        self.db.execute(
            "INSERT INTO predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f.msg_id, run_id, f.from_email, f.subject, laya.type, laya.type_conf, json.dumps(laya.top2),
             laya.needs_action, laya.urgency, json.dumps(hits.names), laya.model, json.dumps(labels)),
        )
        self.db.commit()

    def get_last_run(self) -> int | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = 'last_run'").fetchone()
        return int(row[0]) if row else None

    def set_last_run(self, epoch: int) -> None:
        self.db.execute(
            "INSERT INTO meta (key, value) VALUES ('last_run', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(epoch),),
        )
        self.db.commit()
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `.venv/bin/pytest tests/test_store.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add triage/store.py tests/test_store.py
git commit -m "feat: SQLite store for processed ids, retries and predictions"
```

---

### Task 8: Gmail client and host auth script

**Files:**
- Create: `triage/gmail_client.py`
- Create: `auth.py`
- Test: `tests/test_gmail_client.py`

**Interfaces:**
- Consumes: none.
- Produces:
  - `SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]`
  - the exception classes `AuthError` and `ReadOnlyError`
  - `load_credentials(token_path: Path)`
  - `GmailClient(service, read_only: bool)` with:
    - `.from_token(token_path: Path, read_only: bool)` (classmethod)
    - `.list_ids(query: str, limit: int | None = None) -> list[str]`
    - `.get(msg_id: str) -> dict`
    - `.ensure_labels(names: list[str]) -> dict[str, str]` (name → id; creates missing labels and their parents)
    - `.add_labels(msg_ids: list[str], label_ids: list[str]) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/test_gmail_client.py`:
```python
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import triage.gmail_client as gmail_module
from triage.gmail_client import GmailClient, ReadOnlyError


def users(service):
    return service.users.return_value


def test_list_ids_follows_pages():
    service = MagicMock()
    users(service).messages.return_value.list.return_value.execute.side_effect = [
        {"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "p2"},
        {"messages": [{"id": "c"}]},
    ]
    assert GmailClient(service, read_only=True).list_ids("in:inbox") == ["a", "b", "c"]


def test_list_ids_stops_at_limit():
    service = MagicMock()
    list_call = users(service).messages.return_value.list
    list_call.return_value.execute.side_effect = [
        {"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "p2"},
    ]
    assert GmailClient(service, read_only=True).list_ids("in:inbox", limit=2) == ["a", "b"]
    assert list_call.call_count == 1
    assert list_call.call_args.kwargs["maxResults"] == 2


def test_get_requests_full_format():
    service = MagicMock()
    get_call = users(service).messages.return_value.get
    get_call.return_value.execute.return_value = {"id": "m1"}
    assert GmailClient(service, read_only=True).get("m1") == {"id": "m1"}
    assert get_call.call_args.kwargs == {"userId": "me", "id": "m1", "format": "full"}


def test_ensure_labels_reuses_existing_and_creates_missing():
    service = MagicMock()
    labels = users(service).labels.return_value
    labels.list.return_value.execute.return_value = {
        "labels": [{"name": "Laya", "id": "L0"}, {"name": "Laya/Finance", "id": "L1"}]
    }
    labels.create.return_value.execute.side_effect = [{"id": "L2"}]
    result = GmailClient(service, read_only=False).ensure_labels(["Laya/Finance", "Laya/Orders"])
    assert result == {"Laya/Finance": "L1", "Laya/Orders": "L2"}
    assert [c.kwargs["body"]["name"] for c in labels.create.call_args_list] == ["Laya/Orders"]


def test_ensure_labels_creates_parent_first():
    service = MagicMock()
    labels = users(service).labels.return_value
    labels.list.return_value.execute.return_value = {"labels": []}
    labels.create.return_value.execute.side_effect = [{"id": "P"}, {"id": "C"}]
    result = GmailClient(service, read_only=False).ensure_labels(["Laya/Personal"])
    assert result == {"Laya/Personal": "C"}
    assert [c.kwargs["body"]["name"] for c in labels.create.call_args_list] == ["Laya", "Laya/Personal"]


def test_add_labels_only_adds():
    service = MagicMock()
    GmailClient(service, read_only=False).add_labels(["m1", "m2"], ["L1"])
    users(service).messages.return_value.batchModify.assert_called_once_with(
        userId="me", body={"ids": ["m1", "m2"], "addLabelIds": ["L1"]}
    )


def test_read_only_blocks_writes():
    service = MagicMock()
    users(service).labels.return_value.list.return_value.execute.return_value = {"labels": []}
    client = GmailClient(service, read_only=True)
    with pytest.raises(ReadOnlyError):
        client.add_labels(["m1"], ["L1"])
    with pytest.raises(ReadOnlyError):
        client.ensure_labels(["Laya/Finance"])
    users(service).messages.return_value.batchModify.assert_not_called()


def test_source_has_no_destructive_calls():
    source = Path(gmail_module.__file__).read_text()
    for banned in ("trash(", "delete(", "send(", "drafts(", "removeLabelIds"):
        assert banned not in source
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `.venv/bin/pytest tests/test_gmail_client.py -v`
Expected: collection error `No module named 'triage.gmail_client'`

- [ ] **Step 3: Implement `triage/gmail_client.py`**

```python
"""Thin Gmail API wrapper. It only reads mail and adds labels."""
from __future__ import annotations

import os
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
RETRIES = 5
BATCH_LIMIT = 1000
PAGE_SIZE = 500


class AuthError(RuntimeError):
    """The saved token is missing or no longer works; re-run auth.py on the host."""


class ReadOnlyError(RuntimeError):
    """A write was attempted on a client opened in read-only (eval) mode."""


def load_credentials(token_path: Path):
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not token_path.exists():
        raise AuthError(f"{token_path} not found - run `python auth.py` on the Mac first")
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds.valid:
        if not creds.refresh_token:
            raise AuthError("token has no refresh token - re-run `python auth.py`")
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise AuthError(f"token refresh failed ({exc}) - re-run `python auth.py`") from exc
        token_path.write_text(creds.to_json())
        os.chmod(token_path, 0o600)
    return creds


class GmailClient:
    def __init__(self, service, read_only: bool):
        self.service = service
        self.read_only = read_only

    @classmethod
    def from_token(cls, token_path: Path, read_only: bool) -> "GmailClient":
        from googleapiclient.discovery import build

        creds = load_credentials(token_path)
        return cls(build("gmail", "v1", credentials=creds, cache_discovery=False), read_only)

    def _users(self):
        return self.service.users()

    def _guard(self) -> None:
        if self.read_only:
            raise ReadOnlyError("write attempted in read-only mode")

    def list_ids(self, query: str, limit: int | None = None) -> list[str]:
        ids: list[str] = []
        page_token = None
        while True:
            page_size = min(PAGE_SIZE, limit - len(ids)) if limit else PAGE_SIZE
            response = self._users().messages().list(
                userId="me", q=query, pageToken=page_token, maxResults=page_size
            ).execute(num_retries=RETRIES)
            ids.extend(m["id"] for m in response.get("messages", []))
            page_token = response.get("nextPageToken")
            if not page_token or (limit and len(ids) >= limit):
                return ids[:limit] if limit else ids

    def get(self, msg_id: str) -> dict:
        return self._users().messages().get(userId="me", id=msg_id, format="full").execute(
            num_retries=RETRIES
        )

    def ensure_labels(self, names: list[str]) -> dict[str, str]:
        response = self._users().labels().list(userId="me").execute(num_retries=RETRIES)
        existing = {label["name"]: label["id"] for label in response.get("labels", [])}
        for name in names:
            parts = name.split("/")
            for depth in range(1, len(parts) + 1):
                path = "/".join(parts[:depth])
                if path not in existing:
                    existing[path] = self._create_label(path)
        return {name: existing[name] for name in names}

    def _create_label(self, name: str) -> str:
        self._guard()
        body = {"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
        return self._users().labels().create(userId="me", body=body).execute(num_retries=RETRIES)["id"]

    def add_labels(self, msg_ids: list[str], label_ids: list[str]) -> None:
        self._guard()
        for start in range(0, len(msg_ids), BATCH_LIMIT):
            body = {"ids": msg_ids[start:start + BATCH_LIMIT], "addLabelIds": label_ids}
            self._users().messages().batchModify(userId="me", body=body).execute(num_retries=RETRIES)
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `.venv/bin/pytest tests/test_gmail_client.py -v`
Expected: 8 passed

- [ ] **Step 5: Write `auth.py` (host only, no unit test; it's verified by hand in Task 11)**

```python
"""One-time Gmail login. Run on the Mac, not in Docker:
    .venv/bin/python auth.py
Opens a browser, saves secrets/token.json for the container to use."""
import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from triage.gmail_client import SCOPES

SECRETS = Path(__file__).resolve().parent / "secrets"


def main() -> None:
    flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS / "credentials.json"), SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    token_path = SECRETS / "token.json"
    token_path.write_text(creds.to_json())
    os.chmod(token_path, 0o600)
    profile = build("gmail", "v1", credentials=creds, cache_discovery=False).users().getProfile(
        userId="me"
    ).execute()
    print(f"Authorised {profile['emailAddress']} -> {token_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Commit**

```bash
git add triage/gmail_client.py auth.py tests/test_gmail_client.py
git commit -m "feat: label-only Gmail client with read-only guard and host auth script"
```

---

### Task 9: Labelling pipeline (`run_once`)

**Files:**
- Create: `triage/pipeline.py`
- Modify: `tests/helpers.py` (append `FakeGmail` and `bank_msg`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes:
  - `parse_message` and `to_state` (Task 3), `apply_rules` (Task 4)
  - `Classifier` (Task 5), `decide` (Task 6), `Store` (Task 7)
  - `GmailClient` and `AuthError` (Task 8)
- Produces:
  - `Outcome(features, hits, laya, decision)`
  - `classify_message(raw: dict, cfg: Config, classifier: Classifier) -> Outcome`
  - `should_skip(raw: dict, laya_label_ids: set[str]) -> bool`
  - `build_query(last_run: int | None, now: int) -> str`
  - `run_once(gmail, store, cfg, classifier, now: int | None = None) -> dict[str, int]`, which returns the counts `labelled`, `skipped` and `errors`
  - the constant `MAX_ATTEMPTS = 3`
  - test helpers `FakeGmail(messages: dict, list_once: bool = False)` and `bank_msg(msg_id) -> dict`

- [ ] **Step 1: Append `FakeGmail` and `bank_msg` to `tests/helpers.py`**

```python
class FakeGmail:
    """In-memory GmailClient. A message value that is an Exception is raised by get()."""

    def __init__(self, messages, list_once=False):
        self.messages = messages
        self.list_once = list_once
        self.queries = []
        self.added = []
        self.label_ids = {}

    def list_ids(self, query, limit=None):
        self.queries.append(query)
        if self.list_once and len(self.queries) > 1:
            return []
        ids = list(self.messages)
        return ids[:limit] if limit else ids

    def get(self, msg_id):
        msg = self.messages[msg_id]
        if isinstance(msg, Exception):
            raise msg
        return msg

    def ensure_labels(self, names):
        for name in names:
            self.label_ids.setdefault(name, f"id:{name}")
        return {name: self.label_ids[name] for name in names}

    def add_labels(self, msg_ids, label_ids):
        self.added.append((sorted(msg_ids), sorted(label_ids)))


def bank_msg(msg_id):
    return make_msg(
        msg_id,
        headers={"From": "HDFC Bank <alerts@hdfcbank.net>", "Subject": "Statement",
                 "Authentication-Results": "mx.google.com; dkim=pass; spf=pass; dmarc=pass"},
        plain="Your statement is ready",
    )
```

- [ ] **Step 2: Write the failing tests**

`tests/test_pipeline.py`:
```python
import pytest

from tests.helpers import FakeGmail, FakeModel, bank_msg, make_msg
from triage.classifier import Classifier
from triage.gmail_client import AuthError
from triage.pipeline import build_query, classify_message, run_once, should_skip
from triage.store import Store

NOW = 1_790_000_000


def test_build_query_first_run_looks_back_a_day_plus_overlap():
    assert build_query(None, NOW) == f"in:inbox after:{NOW - 86400 - 3600}"


def test_build_query_overlaps_last_run_by_an_hour():
    assert build_query(NOW - 300, NOW) == f"in:inbox after:{NOW - 300 - 3600}"


def test_should_skip():
    assert should_skip(make_msg(label_ids=("SPAM",)), set())
    assert should_skip(make_msg(label_ids=("SENT",)), set())
    assert should_skip(make_msg(label_ids=("INBOX", "L9")), {"L9"})
    assert not should_skip(make_msg(label_ids=("INBOX",)), {"L9"})


def test_classify_message_end_to_end(cfg):
    out = classify_message(bank_msg("m1"), cfg, Classifier(cfg, FakeModel(type_="finance")))
    assert out.features.from_email == "alerts@hdfcbank.net"
    assert out.decision.label_names(cfg) == ["Laya/Finance"]


def test_labels_new_mail_and_records_state(tmp_path, cfg):
    gmail = FakeGmail({"m1": bank_msg("m1"), "m2": bank_msg("m2")})
    store = Store(tmp_path / "s.db")
    counts = run_once(gmail, store, cfg, Classifier(cfg, FakeModel(type_="finance")), now=NOW)
    assert counts == {"labelled": 2, "skipped": 0, "errors": 0}
    assert gmail.added == [(["m1", "m2"], ["id:Laya/Finance"])]
    assert store.is_done("m1") and store.is_done("m2")
    assert store.get_last_run() == NOW


def test_second_run_does_not_relabel(tmp_path, cfg):
    gmail = FakeGmail({"m1": bank_msg("m1")})
    store = Store(tmp_path / "s.db")
    classifier = Classifier(cfg, FakeModel())
    run_once(gmail, store, cfg, classifier, now=NOW)
    run_once(gmail, store, cfg, classifier, now=NOW + 300)
    assert len(gmail.added) == 1


def test_skips_spam_and_already_labelled(tmp_path, cfg):
    gmail = FakeGmail({
        "spam": make_msg("spam", label_ids=("SPAM",)),
        "done": make_msg("done", label_ids=("INBOX", "id:Laya/Orders")),
    })
    counts = run_once(gmail, Store(tmp_path / "s.db"), cfg, Classifier(cfg, FakeModel()), now=NOW)
    assert counts == {"labelled": 0, "skipped": 2, "errors": 0}
    assert gmail.added == []


def test_errors_retry_then_fall_back_to_unsure(tmp_path, cfg):
    gmail = FakeGmail({"bad": RuntimeError("boom")}, list_once=True)
    store = Store(tmp_path / "s.db")
    classifier = Classifier(cfg, FakeModel())
    run_once(gmail, store, cfg, classifier, now=NOW)
    run_once(gmail, store, cfg, classifier, now=NOW + 300)
    assert gmail.added == []
    run_once(gmail, store, cfg, classifier, now=NOW + 600)
    assert gmail.added == [(["bad"], ["id:Laya/?Unsure"])]
    assert store.is_done("bad")


def test_auth_error_propagates(tmp_path, cfg):
    gmail = FakeGmail({"m1": AuthError("revoked")})
    with pytest.raises(AuthError):
        run_once(gmail, Store(tmp_path / "s.db"), cfg, Classifier(cfg, FakeModel()), now=NOW)
```

- [ ] **Step 3: Run the tests to check they fail**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: collection error `No module named 'triage.pipeline'`

- [ ] **Step 4: Implement `triage/pipeline.py`**

```python
"""One labelling pass: fetch new inbox mail, classify it, add labels."""
from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass

from google.auth.exceptions import RefreshError

from triage.classifier import Classifier, LayaResult
from triage.config import Config
from triage.decide import Decision, decide
from triage.extract import Features, parse_message, to_state
from triage.gmail_client import AuthError
from triage.rules import RuleHits, apply_rules
from triage.store import Store

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
OVERLAP_SECONDS = 3600
FIRST_RUN_LOOKBACK_SECONDS = 86400
SKIP_SYSTEM_LABELS = {"SPAM", "TRASH", "SENT"}


@dataclass(frozen=True)
class Outcome:
    features: Features
    hits: RuleHits
    laya: LayaResult
    decision: Decision


def classify_message(raw: dict, cfg: Config, classifier: Classifier) -> Outcome:
    features = parse_message(raw)
    hits = apply_rules(features, cfg.vip)
    laya = classifier.classify(to_state(features), skip_type=hits.forced_type is not None)
    return Outcome(features, hits, laya, decide(laya, hits, cfg))


def should_skip(raw: dict, laya_label_ids: set[str]) -> bool:
    label_ids = set(raw.get("labelIds", []))
    return bool(label_ids & SKIP_SYSTEM_LABELS or label_ids & laya_label_ids)


def build_query(last_run: int | None, now: int) -> str:
    start = last_run if last_run is not None else now - FIRST_RUN_LOOKBACK_SECONDS
    return f"in:inbox after:{start - OVERLAP_SECONDS}"


def run_once(gmail, store: Store, cfg: Config, classifier: Classifier, now: int | None = None) -> dict[str, int]:
    now = int(time.time()) if now is None else now
    run_id = uuid.uuid4().hex[:8]
    label_map = gmail.ensure_labels(cfg.all_label_names())
    laya_label_ids = set(label_map.values())
    unsure = cfg.priority_labels["unsure"]
    groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
    counts = {"labelled": 0, "skipped": 0, "errors": 0}

    candidates = dict.fromkeys(gmail.list_ids(build_query(store.get_last_run(), now)) + store.retry_ids())
    for msg_id in candidates:
        if store.is_done(msg_id):
            continue
        try:
            raw = gmail.get(msg_id)
            if should_skip(raw, laya_label_ids):
                store.mark(msg_id, "skipped", [])
                counts["skipped"] += 1
                continue
            outcome = classify_message(raw, cfg, classifier)
            names = outcome.decision.label_names(cfg)
            store.log_prediction(run_id, outcome.features, outcome.laya, outcome.hits, names)
            groups[tuple(names)].append(msg_id)
            log.info("%s -> %s (type conf %.2f)", msg_id, ", ".join(names), outcome.laya.type_conf)
        except (AuthError, RefreshError):
            raise
        except Exception:
            log.exception("failed on %s", msg_id)
            store.mark(msg_id, "error", [])
            counts["errors"] += 1
            if store.attempts(msg_id) >= MAX_ATTEMPTS:
                groups[(unsure,)].append(msg_id)

    for names, msg_ids in groups.items():
        gmail.add_labels(msg_ids, [label_map[name] for name in names])
        for msg_id in msg_ids:
            store.mark(msg_id, "ok", list(names))
        counts["labelled"] += len(msg_ids)

    store.set_last_run(now)
    return counts
```

- [ ] **Step 5: Run the tests to check they pass**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: 9 passed

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 72 passed

- [ ] **Step 7: Commit**

```bash
git add triage/pipeline.py tests/helpers.py tests/test_pipeline.py
git commit -m "feat: labelling pass with skip rules, retries and grouped label writes"
```

---

### Task 10: Evaluation, scoring and CLI

**Files:**
- Create: `triage/evaluate.py`
- Create: `triage/main.py`
- Test: `tests/test_evaluate.py`

**Interfaces:**
- Consumes:
  - `classify_message`, `Outcome` and `run_once` (Task 9)
  - `GmailClient` and `AuthError` (Task 8), `Store` (Task 7)
  - `Classifier` and `load_router` (Task 5), `load_config` (Task 2)
- Produces:
  - `CSV_FIELDS`
  - `outcome_row(out: Outcome) -> dict[str, str]`
  - `run_eval(gmail, cfg, classifier, out_path: Path, limit: int = 500, from_csv: Path | None = None) -> int`
  - `score_rows(rows: list[dict]) -> str`
  - `score_csv(path: Path) -> str`
  - `main(argv: list[str] | None = None) -> int`, with the subcommands `eval [--limit N] [--from CSV]`, `score CSV`, `once` and `run`. Exit code 2 means an auth failure.

- [ ] **Step 1: Write the failing tests**

`tests/test_evaluate.py`:
```python
import csv

from tests.helpers import FakeGmail, FakeModel, bank_msg
from triage.classifier import Classifier
from triage.evaluate import CSV_FIELDS, run_eval, score_rows
from triage.main import main


def read_rows(path):
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def test_run_eval_writes_csv_without_touching_gmail(tmp_path, cfg):
    gmail = FakeGmail({"m1": bank_msg("m1"), "m2": bank_msg("m2")})
    out = tmp_path / "eval.csv"
    n = run_eval(gmail, cfg, Classifier(cfg, FakeModel(type_="finance", conf=0.9)), out, limit=10)
    rows = read_rows(out)
    assert n == 2
    assert list(rows[0]) == CSV_FIELDS
    assert rows[0]["pred_type"] == "finance"
    assert rows[0]["from"] == "alerts@hdfcbank.net"
    assert rows[0]["type_conf"] == "0.90"
    assert rows[0]["true_type"] == ""
    assert gmail.added == []
    assert gmail.queries == ["in:inbox"]


def test_reeval_keeps_true_labels(tmp_path, cfg):
    gmail = FakeGmail({"m1": bank_msg("m1")})
    first = tmp_path / "first.csv"
    run_eval(gmail, cfg, Classifier(cfg, FakeModel(type_="orders")), first)
    rows = read_rows(first)
    rows[0]["true_type"] = "finance"
    rows[0]["true_priority"] = "none"
    with first.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    second = tmp_path / "second.csv"
    run_eval(gmail, cfg, Classifier(cfg, FakeModel(type_="finance")), second, from_csv=first)
    row = read_rows(second)[0]
    assert (row["pred_type"], row["true_type"], row["true_priority"]) == ("finance", "finance", "none")


def row(pred, true, pred_priority="", true_priority=""):
    return {"pred_type": pred, "true_type": true, "pred_priority": pred_priority,
            "true_priority": true_priority, "subject": "subj"}


def test_score_accuracy_and_confusions():
    report = score_rows([row("finance", "finance"), row("orders", "finance"), row("updates", "")])
    assert "Type accuracy: 1/2 = 50.0%" in report
    assert "finance -> orders: 1" in report


def test_score_counts_false_suspicious():
    assert "False Suspicious: 1" in score_rows([row("suspicious", "finance")])


def test_score_act_now_precision():
    rows = [row("finance", "finance", "act_now", "act_now"), row("finance", "finance", "act_now", "none")]
    assert "Act Now precision: 1/2 = 50.0%" in score_rows(rows)


def test_score_without_labels():
    assert score_rows([row("finance", "")]) == "No rows have true_type filled in."


def test_main_score_command(tmp_path, capsys):
    path = tmp_path / "labelled.csv"
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerow({**dict.fromkeys(CSV_FIELDS, ""), **row("finance", "finance")})
    assert main(["score", str(path)]) == 0
    assert "Type accuracy: 1/1 = 100.0%" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `.venv/bin/pytest tests/test_evaluate.py -v`
Expected: collection error `No module named 'triage.evaluate'`

- [ ] **Step 3: Implement `triage/evaluate.py`**

```python
"""Read-only evaluation: classify inbox mail into a CSV, then score hand labels."""
from __future__ import annotations

import csv
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from google.auth.exceptions import RefreshError

from triage.classifier import Classifier
from triage.config import Config
from triage.gmail_client import AuthError
from triage.pipeline import Outcome, classify_message

log = logging.getLogger(__name__)

CSV_FIELDS = [
    "msg_id", "date", "from", "subject", "pred_type", "type_conf", "top2", "needs_action",
    "urgency", "pred_priority", "rules", "true_type", "true_priority",
]


def outcome_row(out: Outcome) -> dict[str, str]:
    f, laya = out.features, out.laya
    return {
        "msg_id": f.msg_id,
        "date": datetime.fromtimestamp(f.internal_date / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "from": f.from_email,
        "subject": f.subject,
        "pred_type": out.decision.type,
        "type_conf": f"{laya.type_conf:.2f}",
        "top2": "; ".join(f"{label}={p:.2f}" for label, p in laya.top2),
        "needs_action": f"{laya.needs_action:.2f}",
        "urgency": f"{laya.urgency:.2f}",
        "pred_priority": out.decision.priority or "",
        "rules": ",".join(out.hits.names),
        "true_type": "",
        "true_priority": "",
    }


def _read_rows(path: Path) -> list[dict]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def run_eval(gmail, cfg: Config, classifier: Classifier, out_path: Path, limit: int = 500,
             from_csv: Path | None = None) -> int:
    prior = {r["msg_id"]: r for r in _read_rows(from_csv)} if from_csv else {}
    msg_ids = list(prior) if from_csv else gmail.list_ids("in:inbox", limit=limit)
    rows = []
    for i, msg_id in enumerate(msg_ids, 1):
        try:
            row = outcome_row(classify_message(gmail.get(msg_id), cfg, classifier))
        except (AuthError, RefreshError):
            raise
        except Exception:
            log.exception("eval failed on %s", msg_id)
            continue
        if msg_id in prior:
            row["true_type"] = prior[msg_id].get("true_type", "")
            row["true_priority"] = prior[msg_id].get("true_priority", "")
        rows.append(row)
        if i % 25 == 0:
            log.info("eval %d/%d", i, len(msg_ids))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _pct(part: int, whole: int) -> str:
    return f"{part}/{whole} = {part / whole:.1%}"


def score_rows(rows: list[dict]) -> str:
    labelled = [r for r in rows if r.get("true_type", "").strip()]
    if not labelled:
        return "No rows have true_type filled in."
    wrong = [r for r in labelled if r["true_type"].strip() != r["pred_type"]]
    lines = [f"Type accuracy: {_pct(len(labelled) - len(wrong), len(labelled))}"]

    confusions = Counter((r["true_type"].strip(), r["pred_type"]) for r in wrong)
    if confusions:
        lines.append("Confusions (true -> predicted):")
        lines += [f"  {true} -> {pred}: {n}" for (true, pred), n in confusions.most_common()]

    false_suspicious = [r for r in wrong if r["pred_type"] == "suspicious"]
    lines.append(f"False Suspicious: {len(false_suspicious)}")
    lines += [f"  {r['subject']}" for r in false_suspicious]

    act_now = [r for r in rows if r.get("pred_priority") == "act_now" and r.get("true_priority", "").strip()]
    if act_now:
        hits = sum(r["true_priority"].strip() == "act_now" for r in act_now)
        lines.append(f"Act Now precision: {_pct(hits, len(act_now))}")
    else:
        lines.append("Act Now precision: no labelled act_now rows")
    return "\n".join(lines)


def score_csv(path: Path) -> str:
    return score_rows(_read_rows(path))
```

- [ ] **Step 4: Implement `triage/main.py`**

```python
"""CLI: eval | score | once | run."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from google.auth.exceptions import RefreshError

from triage.classifier import Classifier, load_router
from triage.config import load_config
from triage.evaluate import run_eval, score_csv
from triage.gmail_client import AuthError, GmailClient
from triage.pipeline import run_once
from triage.store import Store

APP_DIR = Path(__file__).resolve().parent.parent
log = logging.getLogger("triage")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="triage")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ev = sub.add_parser("eval", help="read-only: classify inbox mail into data/eval-*.csv")
    ev.add_argument("--limit", type=int, default=500)
    ev.add_argument("--from", dest="from_csv", type=Path,
                    help="re-classify the emails in a labelled CSV, keeping true_* columns")
    sc = sub.add_parser("score", help="accuracy report for a labelled eval CSV")
    sc.add_argument("csv", type=Path)
    sub.add_parser("once", help="label new mail once")
    sub.add_parser("run", help="label new mail every interval_minutes")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.cmd == "score":
        print(score_csv(args.csv))
        return 0

    cfg = load_config(APP_DIR / "config")
    try:
        gmail = GmailClient.from_token(APP_DIR / "secrets" / "token.json", read_only=args.cmd == "eval")
        classifier = Classifier(cfg, load_router())
        if args.cmd == "eval":
            out = APP_DIR / "data" / f"eval-{time.strftime('%Y%m%d-%H%M%S')}.csv"
            n = run_eval(gmail, cfg, classifier, out, limit=args.limit, from_csv=args.from_csv)
            print(f"Wrote {n} rows to {out.relative_to(APP_DIR)}")
            return 0
        store = Store(APP_DIR / "data" / "state.db")
        if args.cmd == "once":
            log.info("done: %s", run_once(gmail, store, cfg, classifier))
            return 0
        while True:
            try:
                log.info("done: %s", run_once(gmail, store, cfg, classifier))
            except (AuthError, RefreshError):
                raise
            except Exception:
                log.exception("cycle failed; retrying next interval")
            time.sleep(cfg.interval_minutes * 60)
    except (AuthError, RefreshError) as exc:
        log.error("Gmail auth problem: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests to check they pass**

Run: `.venv/bin/pytest tests/test_evaluate.py -v`
Expected: 7 passed

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 79 passed

- [ ] **Step 7: Commit**

```bash
git add triage/evaluate.py triage/main.py tests/test_evaluate.py
git commit -m "feat: eval CSV export, re-eval, scoring report and CLI"
```

---

### Task 11: Real-mail evaluation and tuning (P1/P2; needs the owner)

**Files:**
- Modify: `config/config.yaml` (criteria wording and thresholds, only as the results require)

**Interfaces:**
- Consumes: everything above, plus `secrets/credentials.json`, which the owner provides.
- Produces: a tuned `config/config.yaml` that meets the spec §10 acceptance criteria.

- [ ] **Step 1: Owner logs in once (host)**

The owner runs: `.venv/bin/python auth.py`
Expected: a browser opens. The owner clicks "Advanced → Go to Laya Mail Triage (unsafe)" and then Allow. The terminal prints `Authorised <owner>@gmail.com -> …/secrets/token.json`.
Then check the permissions: `ls -l secrets/` shows `-rw-------` for `token.json`.

- [ ] **Step 2: Rebuild the image with the app code**

Run: `docker compose build`
Expected: the build succeeds. Only the `COPY triage` layer changes.

- [ ] **Step 3: Run the read-only eval on the last 500 emails**

Run: `docker compose run --rm triage python -m triage.main eval --limit 500`
Expected: progress lines every 25 emails, then `Wrote ~500 rows to data/eval-YYYYMMDD-HHMMSS.csv`. Gmail shows **no** new labels.

- [ ] **Step 4: Owner hand-labels at least 100 rows**

Open the CSV in Numbers or Excel and fill in two columns:
- `true_type`: one of personal, career, finance, security, travel, orders, updates, newsletters, promotions, suspicious.
- `true_priority`: one of act_now, this_week, none.

Label at least 100 rows, covering every category you can. Save it as CSV with the same columns.

- [ ] **Step 5: Score it**

Run: `.venv/bin/python -m triage.main score data/eval-<ts>.csv`
Expected: a report with the type accuracy, confusions, the False Suspicious count and the Act Now precision.

- [ ] **Step 6: Tune until it meets spec §10**

Repeat until the type accuracy is at least 85%, Act Now precision is at least 80%, and False Suspicious is 0 for real bank, OTP and personal mail:

1. For the top confusion pair, add distinguishing words to both categories' `criteria` in `config/config.yaml`. Keep each criteria string to about 15 words or fewer, because of the option budget.
2. If Act Now is noisy, raise `needs_action` or `urgency_act_now` by 0.1.
3. If False Suspicious is above 0, look at the `rules` column for those rows. A rule caused it if the column is non-empty, so fix the rule in `triage/rules.py` with a new test first. Otherwise, sharpen the `suspicious` criteria.
4. Re-run on the **same** labelled set:
   `docker compose run --rm triage python -m triage.main eval --from data/eval-<ts>.csv`
   then `.venv/bin/python -m triage.main score data/eval-<new-ts>.csv`.

If the accuracy stays below 85% after 3 rounds, apply the spec §12 fallback (a two-level choice) as a new task. Do not lower the bar silently.

- [ ] **Step 7: Commit the tuned config**

```bash
git add config/config.yaml
git commit -m "tune: criteria and thresholds from labelled eval

Type accuracy <A>%, Act Now precision <P>%, false suspicious 0 (n=<N>)."
```

---

### Task 12: Go live in label mode (P3; needs the owner)

**Files:**
- None, unless a fix is needed.

**Interfaces:**
- Consumes: the tuned config and image.
- Produces: an always-on `triage` container that labels new mail every 5 minutes.

- [ ] **Step 1: Do one live pass by hand**

Run: `docker compose run --rm triage python -m triage.main once`
Expected:
- the log line `done: {'labelled': N, 'skipped': M, 'errors': 0}`
- Gmail's sidebar now shows a `Laya` label group with 13 sub-labels

- [ ] **Step 2: Owner checks 20 labelled messages in Gmail**

For each message, confirm that:
- the labels look right
- the message is still in the inbox, still unread or read exactly as before, and not starred, moved or deleted

If any message was changed in another way, **stop**. That's a bug in `gmail_client.py`.

- [ ] **Step 3: Start the service**

Run: `docker compose up -d && docker compose logs -f triage`
Expected: the model loads, and a `done: {...}` line appears roughly every 5 minutes. Stop following the logs with Ctrl-C; the container keeps running.

- [ ] **Step 4: Make it survive reboots**

Owner: Docker Desktop → Settings → General → enable **"Start Docker Desktop when you sign in to your computer"**. Because of `restart: unless-stopped`, the container comes back by itself.
Verify with: `docker compose ps`, which shows `triage` as `running`.

- [ ] **Step 5: Commit any fixes made during go-live, and tag the release**

```bash
git tag v1.0.0
```
