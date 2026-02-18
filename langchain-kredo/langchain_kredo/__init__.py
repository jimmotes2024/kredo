"""langchain-kredo: LangChain integration for the Kredo attestation protocol."""

from langchain_kredo._client import KredoSigningClient
from langchain_kredo.callback import ChainRecord, KredoCallbackHandler, ToolRecord
from langchain_kredo.simple import attest
from langchain_kredo.tools import (
    KredoCheckTrustTool,
    KredoGetTaxonomyTool,
    KredoSearchAttestationsTool,
    KredoSubmitAttestationTool,
)
from langchain_kredo.trust_gate import (
    InsufficientTrustError,
    KredoTrustGate,
    TrustCheckResult,
)

__all__ = [
    "attest",
    "KredoSigningClient",
    "KredoCallbackHandler",
    "ChainRecord",
    "ToolRecord",
    "KredoCheckTrustTool",
    "KredoSearchAttestationsTool",
    "KredoSubmitAttestationTool",
    "KredoGetTaxonomyTool",
    "KredoTrustGate",
    "TrustCheckResult",
    "InsufficientTrustError",
]
