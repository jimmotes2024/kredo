"""HTTP client for the Kredo Discovery API.

Uses stdlib urllib — no extra dependencies required.
Default API URL: https://api.aikredo.com (override via KREDO_API_URL env var).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


DEFAULT_API_URL = "https://api.aikredo.com"


class KredoAPIError(Exception):
    """Raised when the Discovery API returns an error."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"HTTP {status_code}: {message}")


class KredoClient:
    """Minimal HTTP client for the Kredo Discovery API."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (
            base_url
            or os.environ.get("KREDO_API_URL")
            or DEFAULT_API_URL
        ).rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> dict:
        url = f"{self.base_url}{path}"
        if params:
            filtered = {k: str(v) for k, v in params.items() if v is not None}
            if filtered:
                url = f"{url}?{urllib.parse.urlencode(filtered)}"

        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data:
            req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                error_body = json.loads(e.read().decode("utf-8"))
                msg = (
                    error_body.get("error")
                    or error_body.get("detail")
                    or str(error_body)
                )
            except Exception:
                msg = e.reason
            raise KredoAPIError(e.code, msg) from e
        except urllib.error.URLError as e:
            raise KredoAPIError(0, f"Connection failed: {e.reason}") from e

    # --- Endpoints ---

    def health(self) -> dict:
        return self._request("GET", "/health")

    def register(
        self, pubkey: str, name: str = "", agent_type: str = "agent"
    ) -> dict:
        return self._request(
            "POST", "/register",
            body={"pubkey": pubkey, "name": name, "type": agent_type},
        )

    def submit_attestation(self, attestation: dict) -> dict:
        return self._request("POST", "/attestations", body=attestation)

    def get_profile(self, pubkey: str) -> dict:
        encoded = urllib.parse.quote(pubkey, safe="")
        return self._request("GET", f"/agents/{encoded}/profile")

    def get_agent(self, pubkey: str) -> dict:
        encoded = urllib.parse.quote(pubkey, safe="")
        return self._request("GET", f"/agents/{encoded}")

    def search(
        self,
        subject: Optional[str] = None,
        attestor: Optional[str] = None,
        domain: Optional[str] = None,
        skill: Optional[str] = None,
        att_type: Optional[str] = None,
        min_proficiency: Optional[int] = None,
        include_revoked: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        params: dict = {
            "subject": subject,
            "attestor": attestor,
            "domain": domain,
            "skill": skill,
            "type": att_type,
            "min_proficiency": min_proficiency,
            "limit": limit,
            "offset": offset,
        }
        if include_revoked:
            params["include_revoked"] = "true"
        return self._request("GET", "/search", params=params)

    def verify(self, document: dict) -> dict:
        return self._request("POST", "/verify", body=document)

    def get_taxonomy(self) -> dict:
        return self._request("GET", "/taxonomy")
