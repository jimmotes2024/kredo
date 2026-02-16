"""IPFS support for Kredo — optional content-addressed pinning.

Attestations, revocations, and disputes can be pinned to IPFS for permanence.
The CID is deterministic: same document → same canonical JSON → same CID.

Configuration via environment variables:
  KREDO_IPFS_PROVIDER  — "local" or "remote" (unset = disabled)
  KREDO_IPFS_API       — Local daemon URL (default: http://localhost:5001)
  KREDO_IPFS_REMOTE_URL   — Remote pinning service URL
  KREDO_IPFS_REMOTE_TOKEN — Bearer token for remote pinning

Zero new dependencies — stdlib urllib only.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from kredo._canonical import _normalize
from kredo.exceptions import IPFSError


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def ipfs_enabled() -> bool:
    """Check whether IPFS is configured."""
    return bool(os.environ.get("KREDO_IPFS_PROVIDER"))


def _get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(key, default)


# ---------------------------------------------------------------------------
# Canonical JSON (full document, including signature)
# ---------------------------------------------------------------------------

def canonical_json_full(doc: dict) -> bytes:
    """Produce canonical JSON bytes for an entire document, including signature.

    Uses the same _normalize() as the signing path, but does NOT strip the
    signature field.  Same attestation → same bytes → same CID.
    """
    normalized = _normalize(doc)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# Provider protocol + implementations
# ---------------------------------------------------------------------------

class IPFSProvider(Protocol):
    """Interface for IPFS pinning backends."""

    @property
    def name(self) -> str: ...

    def pin(self, data: bytes) -> str:
        """Pin raw bytes to IPFS. Returns the CID."""
        ...

    def fetch(self, cid: str) -> bytes:
        """Fetch raw bytes from IPFS by CID."""
        ...


class LocalIPFSProvider:
    """Talks to a local IPFS daemon via its HTTP API."""

    def __init__(self, api_url: Optional[str] = None):
        self._api = (api_url or _get_env("KREDO_IPFS_API", "http://localhost:5001")).rstrip("/")

    @property
    def name(self) -> str:
        return "local"

    def pin(self, data: bytes) -> str:
        """Add + pin data via the local daemon. Returns CIDv0/v1."""
        url = f"{self._api}/api/v0/add?pin=true&quiet=true"
        # IPFS add expects multipart/form-data
        boundary = "----KredoBoundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="kredo.json"\r\n'
            f"Content-Type: application/json\r\n\r\n"
        ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")

        req = Request(
            url,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
                cid = result.get("Hash")
                if not cid:
                    raise IPFSError(f"IPFS daemon returned no Hash: {result}")
                return cid
        except HTTPError as e:
            raise IPFSError(f"IPFS daemon error: {e.code} {e.reason}") from e
        except URLError as e:
            raise IPFSError(f"Cannot reach IPFS daemon at {self._api}: {e.reason}") from e

    def fetch(self, cid: str) -> bytes:
        """Fetch content by CID from the local daemon."""
        url = f"{self._api}/api/v0/cat?arg={cid}"
        req = Request(url, method="POST")
        try:
            with urlopen(req, timeout=30) as resp:
                return resp.read()
        except HTTPError as e:
            raise IPFSError(f"IPFS fetch error: {e.code} {e.reason}") from e
        except URLError as e:
            raise IPFSError(f"Cannot reach IPFS daemon at {self._api}: {e.reason}") from e


class RemotePinningProvider:
    """Talks to a Pinata-compatible remote pinning service."""

    def __init__(
        self,
        remote_url: Optional[str] = None,
        remote_token: Optional[str] = None,
    ):
        self._url = (remote_url or _get_env("KREDO_IPFS_REMOTE_URL", "")).rstrip("/")
        self._token = remote_token or _get_env("KREDO_IPFS_REMOTE_TOKEN", "")
        if not self._url:
            raise IPFSError("KREDO_IPFS_REMOTE_URL is required for remote pinning")
        if not self._token:
            raise IPFSError("KREDO_IPFS_REMOTE_TOKEN is required for remote pinning")

    @property
    def name(self) -> str:
        return "remote"

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def pin(self, data: bytes) -> str:
        """Pin via remote pinning service (Pinata-compatible /pinning/pinFileToIPFS)."""
        url = f"{self._url}/pinning/pinFileToIPFS"
        boundary = "----KredoBoundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="kredo.json"\r\n'
            f"Content-Type: application/json\r\n\r\n"
        ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")

        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            **self._auth_headers(),
        }
        req = Request(url, data=body, headers=headers, method="POST")
        try:
            with urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())
                cid = result.get("IpfsHash") or result.get("Hash")
                if not cid:
                    raise IPFSError(f"Remote pinning returned no hash: {result}")
                return cid
        except HTTPError as e:
            raise IPFSError(f"Remote pinning error: {e.code} {e.reason}") from e
        except URLError as e:
            raise IPFSError(f"Cannot reach remote pinning service at {self._url}: {e.reason}") from e

    def fetch(self, cid: str) -> bytes:
        """Fetch from IPFS gateway (remote services typically expose a gateway)."""
        # Try the service's gateway endpoint
        url = f"{self._url}/gateway/{cid}"
        headers = self._auth_headers()
        req = Request(url, headers=headers, method="GET")
        try:
            with urlopen(req, timeout=30) as resp:
                return resp.read()
        except HTTPError as e:
            raise IPFSError(f"Remote fetch error: {e.code} {e.reason}") from e
        except URLError as e:
            raise IPFSError(f"Cannot reach remote service at {self._url}: {e.reason}") from e


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def get_provider() -> IPFSProvider:
    """Create the configured IPFS provider. Raises IPFSError if misconfigured."""
    provider_type = _get_env("KREDO_IPFS_PROVIDER", "")
    if provider_type == "local":
        return LocalIPFSProvider()
    elif provider_type == "remote":
        return RemotePinningProvider()
    else:
        raise IPFSError(
            f"Unknown IPFS provider: '{provider_type}'. "
            f"Set KREDO_IPFS_PROVIDER to 'local' or 'remote'."
        )


# ---------------------------------------------------------------------------
# High-level operations
# ---------------------------------------------------------------------------

def pin_document(doc: dict, doc_type: str, provider: Optional[IPFSProvider] = None) -> str:
    """Pin a Kredo document (attestation/revocation/dispute) to IPFS.

    Args:
        doc: The full document dict (including signature).
        doc_type: One of "attestation", "revocation", "dispute".
        provider: Optional provider override; uses get_provider() if None.

    Returns:
        The CID string.
    """
    if provider is None:
        provider = get_provider()
    data = canonical_json_full(doc)
    try:
        return provider.pin(data)
    except IPFSError:
        raise
    except Exception as e:
        raise IPFSError(f"Pin failed: {e}") from e


def fetch_document(cid: str, provider: Optional[IPFSProvider] = None) -> dict:
    """Fetch a Kredo document from IPFS by CID.

    Args:
        cid: The IPFS content identifier.
        provider: Optional provider override; uses get_provider() if None.

    Returns:
        The parsed document dict.
    """
    if provider is None:
        provider = get_provider()
    try:
        raw = provider.fetch(cid)
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise IPFSError(f"IPFS content is not valid JSON: {e}") from e
    except IPFSError:
        raise
    except Exception as e:
        raise IPFSError(f"Fetch failed: {e}") from e
