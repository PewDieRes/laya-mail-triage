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
