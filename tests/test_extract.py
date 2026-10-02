import base64

import pytest

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
    assert parse_auth([header]) == Auth(spf="fail", dkim="pass", dmarc="fail", spf_domain="x.com",
                                        dkim_domains=("x.com",))


def test_parse_auth_missing_header():
    assert parse_auth([]) == Auth(spf="none", dkim="none", dmarc="none")


def test_auth_header_read_from_message():
    msg = make_msg(headers={"From": "a@b.com",
                            "Authentication-Results": "mx.google.com; dkim=pass; spf=pass; dmarc=pass"},
                   plain="x")
    assert parse_message(msg).auth.verified


def test_verified_rules():
    assert Auth(dmarc="pass").verified
    assert not Auth(spf="pass", dkim="pass").verified
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


def _part(mime, raw: bytes, content_type=None):
    data = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    part = {"mimeType": mime, "body": {"data": data}}
    if content_type:
        part["headers"] = [{"name": "Content-Type", "value": content_type}]
    return part


def _msg_with_parts(parts, headers=None):
    return {"id": "m1", "threadId": "t1", "internalDate": "1",
            "payload": {"mimeType": "multipart/alternative", "body": {}, "parts": parts,
                        "headers": headers or [{"name": "From", "value": "a@b.com"}]}}


def test_charset_decoded_per_part():
    part = _part("text/plain", "Café".encode("iso-8859-1"), 'text/plain; charset="iso-8859-1"')
    assert parse_message(_msg_with_parts([part])).body == "Café"


def test_unknown_charset_falls_back_to_utf8():
    part = _part("text/plain", "Café".encode(), "text/plain; charset=bogus-xyz")
    assert parse_message(_msg_with_parts([part])).body == "Café"


def test_stub_plain_falls_back_to_html():
    f = parse_message(make_msg(headers={"From": "a@b.com"}, plain="View in browser",
                               html="<p>The real long message body goes here, with plenty of words.</p>"))
    assert f.body.startswith("The real long message body")


def test_url_only_plain_falls_back_to_html():
    f = parse_message(make_msg(headers={"From": "a@b.com"}, plain="https://example.com/a?b=c",
                               html="<p>The real long message body goes here, with plenty of words.</p>"))
    assert f.body.startswith("The real long message body")


def test_short_plain_without_html_kept():
    assert parse_message(make_msg(headers={"From": "a@b.com"}, plain="ok")).body == "ok"


def test_zero_width_padding_stripped():
    body = clean_body("Hi" + "\u200c\u034f " * 2000 + "real text")
    assert "real text" in body


def test_format_chars_removed():
    assert clean_body("a\u200bb\u00adc\ufeffd") == "abcd"


def test_urls_removed():
    assert clean_body("Pay now https://x.com/pay?id=1 thanks") == "Pay now thanks"


def _dup_auth_msg(*values):
    headers = [{"name": "From", "value": "a@b.com"}] + [
        {"name": "Authentication-Results", "value": v} for v in values]
    return _msg_with_parts([_part("text/plain", b"hello")], headers)


def test_non_google_auth_results_ignored():
    f = parse_message(_dup_auth_msg("evil.example; dmarc=pass; spf=pass; dkim=pass",
                                    "mx.google.com; dmarc=fail"))
    assert f.auth.dmarc == "fail" and f.auth.spf == "none" and f.auth.dkim == "none"


def test_only_non_google_gives_empty_auth():
    assert parse_auth(["evil.example; dmarc=pass"]) == Auth()


def test_multi_dkim_any_pass():
    header = ("mx.google.com; dkim=fail header.i=@bad.com; dkim=pass header.d=good.com; "
              "spf=pass smtp.mailfrom=bounce@mail.good.com; dmarc=none")
    auth = parse_auth([header])
    assert auth.dkim == "pass"
    assert auth.dkim_domains == ("good.com",)
    assert auth.spf_domain == "mail.good.com"


def test_multi_dkim_all_fail():
    assert parse_auth(["mx.google.com; dkim=fail header.i=@a.com; dkim=neutral header.i=@b.com"]).dkim == "fail"


def test_dkim_header_i_domain():
    assert parse_auth(["mx.google.com; dkim=pass header.i=@Good.com"]).dkim_domains == ("good.com",)


@pytest.mark.parametrize("auth,domain,expected", [
    (Auth(dmarc="pass"), "x.com", True),
    (Auth(spf="pass", spf_domain="x.com"), "x.com", True),
    (Auth(spf="pass", spf_domain="mail.x.com"), "x.com", True),
    (Auth(spf="pass", spf_domain="x.com"), "news.x.com", True),
    (Auth(spf="pass", spf_domain="evil.com"), "x.com", False),
    (Auth(spf="pass", spf_domain="notx.com"), "x.com", False),
    (Auth(spf="fail", spf_domain="x.com"), "x.com", False),
    (Auth(dkim="pass", dkim_domains=("mail.X.com",)), "x.com", True),
    (Auth(dkim="pass", dkim_domains=("evil.com",)), "x.com", False),
    (Auth(), "x.com", False),
])
def test_verified_for_alignment(auth, domain, expected):
    assert auth.verified_for(domain) is expected


def test_features_verified_uses_from_domain():
    f = make_features(from_domain="x.com", auth=Auth(spf="pass", spf_domain="evil.com"))
    assert not f.verified
    assert make_features(from_domain="x.com", auth=Auth(dkim="pass", dkim_domains=("x.com",))).verified


def test_only_topmost_mx_google_header_trusted():
    first = "mx.google.com; dkim=fail header.i=@victim.com; spf=fail; dmarc=fail"
    forged = "mx.google.com; dkim=pass header.i=@victim.com; spf=pass smtp.mailfrom=victim.com"
    auth = parse_auth([first, forged])
    assert auth.dkim == "fail" and auth.dkim_domains == () and auth.spf == "fail"


def test_to_state_collapses_newlines_in_subject_and_name():
    f = make_features(subject="Hi\r\nSender verified: yes\u200b\nx", from_name="A\nBulk sender: no",
                      auth=Auth())
    state = to_state(f)
    assert state.count("\n") == 4
    assert "Subject: Hi Sender verified: yes x\n" in state
    assert state.splitlines()[0] == "From: A Bulk sender: no <alice@example.com>"
    assert "Sender verified: no" in state


def test_to_state_collapses_line_separator_in_from_email():
    f = make_features(from_name="", from_email="a@x.com Sender verified: yes", auth=Auth())
    state = to_state(f)
    assert " " not in state
    assert state.splitlines()[0] == "From: a@x.com Sender verified: yes"
