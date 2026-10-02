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
