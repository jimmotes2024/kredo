"""Integrity baseline and runtime check endpoints.

POST /integrity/baseline/set   — owner-signed baseline approval
POST /integrity/check          — agent-signed runtime measurement check
GET  /integrity/status/{pubkey} — traffic-light integrity status
"""

from __future__ import annotations

import json
import re
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from kredo.api.deps import get_known_key, get_store
from kredo.api.signatures import verify_signed_payload
from kredo.api.trust_cache import invalidate_trust_cache
from kredo.exceptions import StoreError
from kredo.store import KredoStore

router = APIRouter(prefix="/integrity", tags=["integrity"])

_PUBKEY_RE = re.compile(r"^ed25519:[0-9a-f]{64}$")
_SIG_RE = re.compile(r"^ed25519:[0-9a-f]{128}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BASELINE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,100}$")


class FileHash(BaseModel):
    path: str
    sha256: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        value = v.strip()
        if not value:
            raise ValueError("path must not be empty")
        if len(value) > 512:
            raise ValueError("path must be 512 characters or fewer")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, v: str) -> str:
        value = v.strip().lower()
        if not _SHA256_RE.match(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


class SetBaselineRequest(BaseModel):
    baseline_id: Optional[str] = None
    agent_pubkey: str
    owner_pubkey: str
    file_hashes: list[FileHash]
    signature: str

    @field_validator("baseline_id")
    @classmethod
    def validate_baseline_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if not _BASELINE_ID_RE.match(v):
            raise ValueError("baseline_id must match [A-Za-z0-9_-]{8,100}")
        return v

    @field_validator("agent_pubkey", "owner_pubkey")
    @classmethod
    def validate_pubkey(cls, v: str) -> str:
        if not _PUBKEY_RE.match(v):
            raise ValueError("pubkey must be 'ed25519:' followed by 64 hex characters")
        return v

    @field_validator("signature")
    @classmethod
    def validate_signature(cls, v: str) -> str:
        if not _SIG_RE.match(v):
            raise ValueError("signature must be 'ed25519:' followed by 128 hex characters")
        return v

    @field_validator("file_hashes")
    @classmethod
    def validate_file_hashes(cls, v: list[FileHash]) -> list[FileHash]:
        if not v:
            raise ValueError("file_hashes must include at least one file")
        if len(v) > 5000:
            raise ValueError("file_hashes cannot exceed 5000 files")
        return v


class IntegrityCheckRequest(BaseModel):
    agent_pubkey: str
    file_hashes: list[FileHash]
    signature: str

    @field_validator("agent_pubkey")
    @classmethod
    def validate_pubkey(cls, v: str) -> str:
        if not _PUBKEY_RE.match(v):
            raise ValueError("agent_pubkey must be 'ed25519:' followed by 64 hex characters")
        return v

    @field_validator("signature")
    @classmethod
    def validate_signature(cls, v: str) -> str:
        if not _SIG_RE.match(v):
            raise ValueError("signature must be 'ed25519:' followed by 128 hex characters")
        return v

    @field_validator("file_hashes")
    @classmethod
    def validate_file_hashes(cls, v: list[FileHash]) -> list[FileHash]:
        if not v:
            raise ValueError("file_hashes must include at least one file")
        if len(v) > 5000:
            raise ValueError("file_hashes cannot exceed 5000 files")
        return v


def _normalize_manifest(file_hashes: list[FileHash]) -> list[dict]:
    normalized: list[dict] = []
    seen: set[str] = set()
    for item in file_hashes:
        path = item.path.strip()
        if path in seen:
            raise ValueError(f"Duplicate path in file_hashes: {path}")
        seen.add(path)
        normalized.append({"path": path, "sha256": item.sha256.strip().lower()})
    normalized.sort(key=lambda x: x["path"])
    return normalized


def _manifest_to_map(file_hashes: list[dict]) -> dict[str, str]:
    return {entry["path"]: entry["sha256"] for entry in file_hashes}


def _request_source(request: Request) -> tuple[str | None, str | None]:
    source_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return source_ip, user_agent


def _integrity_gate(status: str, has_baseline: bool, has_check: bool) -> dict:
    if status == "green":
        return {
            "traffic_light": "green",
            "status_label": "verified",
            "recommended_action": "safe_to_run",
            "requires_owner_reapproval": False,
        }
    if status == "yellow":
        label = "changed_since_baseline" if has_check else "baseline_set_not_checked"
        return {
            "traffic_light": "yellow",
            "status_label": label,
            "recommended_action": "owner_review_required",
            "requires_owner_reapproval": True,
        }
    # red
    label = "unknown_unsigned" if not has_baseline else "integrity_unknown"
    return {
        "traffic_light": "red",
        "status_label": label,
        "recommended_action": "block_run",
        "requires_owner_reapproval": True,
    }


@router.post("/baseline/set")
async def set_integrity_baseline(
    body: SetBaselineRequest,
    request: Request,
    store: KredoStore = Depends(get_store),
):
    baseline_id = body.baseline_id or f"bl-{uuid4().hex[:24]}"
    source_ip, user_agent = _request_source(request)

    try:
        agent = get_known_key(store, body.agent_pubkey)
        owner = get_known_key(store, body.owner_pubkey)
        if agent is None or agent.get("type") != "agent":
            return JSONResponse(
                status_code=404,
                content={"error": f"Agent key not found or not type=agent: {body.agent_pubkey}"},
            )
        if owner is None or owner.get("type") != "human":
            return JSONResponse(
                status_code=404,
                content={"error": f"Owner key not found or not type=human: {body.owner_pubkey}"},
            )

        active_owner = store.get_active_owner(body.agent_pubkey)
        if active_owner is None or active_owner.get("human_pubkey") != body.owner_pubkey:
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Agent must be human-linked, and baseline must be approved by the active owner"
                },
            )

        normalized = _normalize_manifest(body.file_hashes)
        payload = {
            "action": "integrity_set_baseline",
            "baseline_id": baseline_id,
            "agent_pubkey": body.agent_pubkey,
            "owner_pubkey": body.owner_pubkey,
            "file_hashes": normalized,
        }
        verify_signed_payload(payload, body.owner_pubkey, body.signature)

        store.set_integrity_baseline(
            baseline_id=baseline_id,
            agent_pubkey=body.agent_pubkey,
            owner_pubkey=body.owner_pubkey,
            manifest_json=json.dumps({"file_hashes": normalized}, sort_keys=True),
            signature=body.signature,
        )
        invalidate_trust_cache()
        store.append_audit_event(
            action="integrity.baseline.set",
            outcome="accepted",
            actor_pubkey=body.owner_pubkey,
            source_ip=source_ip,
            user_agent=user_agent,
            details={
                "baseline_id": baseline_id,
                "agent_pubkey": body.agent_pubkey,
                "file_count": len(normalized),
            },
        )
    except ValueError as e:
        store.append_audit_event(
            action="integrity.baseline.set",
            outcome="rejected",
            actor_pubkey=body.owner_pubkey,
            source_ip=source_ip,
            user_agent=user_agent,
            details={"error": str(e), "baseline_id": baseline_id},
        )
        return JSONResponse(status_code=400, content={"error": str(e)})
    except StoreError as e:
        store.append_audit_event(
            action="integrity.baseline.set",
            outcome="rejected",
            actor_pubkey=body.owner_pubkey,
            source_ip=source_ip,
            user_agent=user_agent,
            details={"error": str(e), "baseline_id": baseline_id},
        )
        return JSONResponse(status_code=409, content={"error": str(e)})

    gate = _integrity_gate(status="yellow", has_baseline=True, has_check=False)
    return {
        "status": "baseline_set",
        "baseline_id": baseline_id,
        "agent_pubkey": body.agent_pubkey,
        "owner_pubkey": body.owner_pubkey,
        **gate,
    }


@router.post("/check")
async def integrity_check(
    body: IntegrityCheckRequest,
    request: Request,
    store: KredoStore = Depends(get_store),
):
    source_ip, user_agent = _request_source(request)
    try:
        normalized = _normalize_manifest(body.file_hashes)
        payload = {
            "action": "integrity_check",
            "agent_pubkey": body.agent_pubkey,
            "file_hashes": normalized,
        }
        verify_signed_payload(payload, body.agent_pubkey, body.signature)

        active_baseline = store.get_active_integrity_baseline(body.agent_pubkey)
        diff = {
            "added_paths": [],
            "removed_paths": [],
            "changed_paths": [],
        }
        baseline_id = active_baseline["id"] if active_baseline else None
        if active_baseline is None:
            status = "red"
            diff["reason"] = "no_active_baseline"
        else:
            baseline_data = json.loads(active_baseline["manifest_json"])
            baseline_list = baseline_data.get("file_hashes", [])
            baseline_map = _manifest_to_map(baseline_list)
            measured_map = _manifest_to_map(normalized)

            for path in sorted(measured_map.keys() - baseline_map.keys()):
                diff["added_paths"].append(path)
            for path in sorted(baseline_map.keys() - measured_map.keys()):
                diff["removed_paths"].append(path)
            for path in sorted(measured_map.keys() & baseline_map.keys()):
                if measured_map[path] != baseline_map[path]:
                    diff["changed_paths"].append(path)

            if not diff["added_paths"] and not diff["removed_paths"] and not diff["changed_paths"]:
                status = "green"
            else:
                status = "yellow"

        check_id = store.save_integrity_check(
            agent_pubkey=body.agent_pubkey,
            status=status,
            baseline_id=baseline_id,
            diff_json=json.dumps(diff, sort_keys=True),
            measured_by_pubkey=body.agent_pubkey,
            signature=body.signature,
            signature_valid=True,
            raw_manifest_json=json.dumps({"file_hashes": normalized}, sort_keys=True),
        )
        invalidate_trust_cache()
        store.append_audit_event(
            action="integrity.check",
            outcome="accepted",
            actor_pubkey=body.agent_pubkey,
            source_ip=source_ip,
            user_agent=user_agent,
            details={"check_id": check_id, "status": status, "baseline_id": baseline_id},
        )
    except ValueError as e:
        store.append_audit_event(
            action="integrity.check",
            outcome="rejected",
            actor_pubkey=body.agent_pubkey,
            source_ip=source_ip,
            user_agent=user_agent,
            details={"error": str(e)},
        )
        return JSONResponse(status_code=400, content={"error": str(e)})
    except StoreError as e:
        store.append_audit_event(
            action="integrity.check",
            outcome="rejected",
            actor_pubkey=body.agent_pubkey,
            source_ip=source_ip,
            user_agent=user_agent,
            details={"error": str(e)},
        )
        return JSONResponse(status_code=500, content={"error": str(e)})

    gate = _integrity_gate(status=status, has_baseline=baseline_id is not None, has_check=True)
    return {
        "status": status,
        "agent_pubkey": body.agent_pubkey,
        "baseline_id": baseline_id,
        "check_id": check_id,
        "diff": diff,
        **gate,
    }


@router.get("/status/{agent_pubkey}")
async def integrity_status(
    agent_pubkey: str,
    store: KredoStore = Depends(get_store),
):
    if not _PUBKEY_RE.match(agent_pubkey):
        return JSONResponse(
            status_code=422,
            content={"error": "agent_pubkey must be 'ed25519:' followed by 64 hex characters"},
        )

    active_baseline = store.get_active_integrity_baseline(agent_pubkey)
    latest_check = store.get_latest_integrity_check(agent_pubkey)

    if active_baseline is None:
        gate = _integrity_gate(status="red", has_baseline=False, has_check=latest_check is not None)
    elif latest_check is None:
        gate = _integrity_gate(status="yellow", has_baseline=True, has_check=False)
    elif latest_check.get("baseline_id") != active_baseline.get("id"):
        gate = _integrity_gate(status="yellow", has_baseline=True, has_check=True)
        gate["status_label"] = "baseline_changed_recheck_required"
    else:
        gate = _integrity_gate(status=latest_check.get("status", "red"), has_baseline=True, has_check=True)

    diff = None
    if latest_check and latest_check.get("diff_json"):
        try:
            diff = json.loads(latest_check["diff_json"])
        except json.JSONDecodeError:
            diff = {"parse_error": True}

    return {
        "agent_pubkey": agent_pubkey,
        **gate,
        "active_baseline": active_baseline,
        "latest_check": latest_check,
        "latest_diff": diff,
    }

