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
