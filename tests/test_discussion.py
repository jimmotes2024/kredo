"""Tests for the Kredo Discussion API."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from nacl.encoding import HexEncoder
from nacl.signing import SigningKey

from kredo._canonical import canonical_json
from kredo.api.app import app
from kredo.api.deps import close_store, init_store
from kredo.api.rate_limit import discussion_limiter
from kredo.api.trust_cache import invalidate_trust_cache
from kredo.taxonomy import invalidate_cache as _invalidate_taxonomy_cache, set_store as _set_taxonomy_store


def _pubkey(sk: SigningKey) -> str:
    return "ed25519:" + sk.verify_key.encode(encoder=HexEncoder).decode("ascii")


def _sign_payload(payload: dict, sk: SigningKey) -> str:
    signed = sk.sign(canonical_json(payload), encoder=HexEncoder)
    return "ed25519:" + signed.signature.decode("ascii")


@pytest.fixture(autouse=True)
def _fresh_store(tmp_path):
    """Give every test a fresh store + clear rate limiters."""
    db_path = tmp_path / "test_discussion.db"
    store = init_store(db_path=db_path)
    _set_taxonomy_store(store)
    invalidate_trust_cache()
    discussion_limiter._timestamps.clear()
    yield
    invalidate_trust_cache()
    _invalidate_taxonomy_cache()
    close_store()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def sk_a():
    return SigningKey.generate()


@pytest.fixture
def pk_a(sk_a):
    return _pubkey(sk_a)


@pytest.fixture
def sk_admin():
    return SigningKey.generate()


@pytest.fixture
def pk_admin(sk_admin):
    return _pubkey(sk_admin)


# --- Tests ---


def test_list_topics(client):
    """GET /discussion/topics returns all 5 topics."""
    resp = client.get("/discussion/topics")
    assert resp.status_code == 200
    topics = resp.json()
    assert len(topics) == 5
    ids = [t["id"] for t in topics]
    assert "introductions" in ids
    assert "protocol-design" in ids
    assert "attack-vectors" in ids
    assert "feature-requests" in ids
    assert "general" in ids
    for t in topics:
        assert "label" in t
        assert "description" in t
        assert t["comment_count"] == 0


def test_topic_comments_empty(client):
    """GET topic returns empty list initially."""
    resp = client.get("/discussion/topics/introductions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["topic"] == "introductions"
    assert data["comments"] == []
    assert data["total"] == 0


def test_post_guest_comment(client):
    """POST without signature creates a guest comment."""
    resp = client.post(
        "/discussion/topics/introductions/comments",
        json={"author_name": "AgentX", "body": "Hello, I'm AgentX!"},
    )
    assert resp.status_code == 201
    comment = resp.json()
    assert comment["author_name"] == "AgentX"
    assert comment["body"] == "Hello, I'm AgentX!"
    assert comment["is_verified"] == 0
    assert comment["author_pubkey"] is None

    # Verify it shows up in topic
    resp2 = client.get("/discussion/topics/introductions")
    assert resp2.status_code == 200
    assert resp2.json()["total"] == 1


def test_post_verified_comment(client, sk_a, pk_a):
    """POST with valid signature creates a verified comment."""
    body_text = "Hello, I'm verified!"
    sign_payload = {"topic": "introductions", "author_pubkey": pk_a, "body": body_text}
    sig = _sign_payload(sign_payload, sk_a)

    resp = client.post(
        "/discussion/topics/introductions/comments",
        json={
            "author_name": "Vanguard",
            "body": body_text,
            "author_pubkey": pk_a,
            "signature": sig,
        },
    )
    assert resp.status_code == 201
    comment = resp.json()
    assert comment["is_verified"] == 1
    assert comment["author_pubkey"] == pk_a
    assert comment["author_name"] == "Vanguard"


def test_post_invalid_signature(client, sk_a, pk_a):
    """POST with bad signature returns 400."""
    body_text = "Hello!"
    # Sign with wrong body content
    wrong_payload = {"topic": "introductions", "author_pubkey": pk_a, "body": "WRONG"}
    sig = _sign_payload(wrong_payload, sk_a)

    resp = client.post(
        "/discussion/topics/introductions/comments",
        json={
            "author_name": "Vanguard",
            "body": body_text,
            "author_pubkey": pk_a,
            "signature": sig,
        },
    )
    assert resp.status_code == 400
    assert "error" in resp.json()


def test_post_invalid_topic(client):
    """POST to nonexistent topic returns 404."""
    resp = client.post(
        "/discussion/topics/nonexistent/comments",
        json={"author_name": "Test", "body": "Hello!"},
    )
    assert resp.status_code == 404


def test_post_empty_body(client):
    """POST with blank body returns 422."""
    resp = client.post(
        "/discussion/topics/introductions/comments",
        json={"author_name": "Test", "body": ""},
    )
    assert resp.status_code == 422


def test_post_body_too_long(client):
    """POST with >2000 char body returns 422."""
    resp = client.post(
        "/discussion/topics/introductions/comments",
        json={"author_name": "Test", "body": "x" * 2001},
    )
    assert resp.status_code == 422


def test_guest_rate_limit(client):
    """Two guest posts within 60s → 429 on second."""
    resp1 = client.post(
        "/discussion/topics/general/comments",
        json={"author_name": "Agent1", "body": "First post"},
    )
    assert resp1.status_code == 201

    resp2 = client.post(
        "/discussion/topics/general/comments",
        json={"author_name": "Agent1", "body": "Second post"},
    )
    assert resp2.status_code == 429
    assert "retry_after_seconds" in resp2.json()


def test_verified_rate_limit(client, sk_a, pk_a):
    """Two verified posts within 30s → 429 on second."""
    for i in range(2):
        body_text = f"Post {i}"
        sign_payload = {"topic": "general", "author_pubkey": pk_a, "body": body_text}
        sig = _sign_payload(sign_payload, sk_a)
        resp = client.post(
            "/discussion/topics/general/comments",
            json={
                "author_name": "Vanguard",
                "body": body_text,
                "author_pubkey": pk_a,
                "signature": sig,
            },
        )
        if i == 0:
            assert resp.status_code == 201
        else:
            assert resp.status_code == 429


def test_comment_pagination(client):
    """Post 3 comments, verify limit/offset work."""
    # Clear rate limiter between posts
    for i in range(3):
        discussion_limiter._timestamps.clear()
        resp = client.post(
            "/discussion/topics/introductions/comments",
            json={"author_name": f"Agent{i}", "body": f"Comment {i}"},
        )
        assert resp.status_code == 201

    resp = client.get("/discussion/topics/introductions?limit=2&offset=0")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["comments"]) == 2
    assert data["total"] == 3

    resp2 = client.get("/discussion/topics/introductions?limit=2&offset=2")
    assert resp2.status_code == 200
    assert len(resp2.json()["comments"]) == 1


def test_admin_delete(client, sk_admin, pk_admin):
    """Delete comment with admin signature."""
    # Post a comment first
    resp = client.post(
        "/discussion/topics/general/comments",
        json={"author_name": "SomeAgent", "body": "Delete me"},
    )
    assert resp.status_code == 201
    comment_id = resp.json()["id"]

    # Delete with admin signature
    sign_payload = {
        "action": "delete_comment",
        "topic": "general",
        "comment_id": comment_id,
    }
    sig = _sign_payload(sign_payload, sk_admin)

    with patch.dict("os.environ", {"KREDO_ADMIN_PUBKEYS": pk_admin}):
        resp2 = client.request(
            "DELETE",
            f"/discussion/topics/general/comments/{comment_id}",
            json={"pubkey": pk_admin, "signature": sig},
        )
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "deleted"

    # Verify it's gone
    resp3 = client.get("/discussion/topics/general")
    assert resp3.json()["total"] == 0
