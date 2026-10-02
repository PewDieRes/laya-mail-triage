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
