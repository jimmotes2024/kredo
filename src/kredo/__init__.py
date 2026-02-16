"""Kredo — Portable agent attestation protocol."""

from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parent.parent.parent / "VERSION"
__version__ = _VERSION_FILE.read_text().strip() if _VERSION_FILE.exists() else "0.0.0"

from kredo.exceptions import (
    KredoError,
    KeyNotFoundError,
    InvalidSignatureError,
    InvalidAttestationError,
    ExpiredAttestationError,
    TaxonomyError,
    StoreError,
)

__all__ = [
    "__version__",
    "KredoError",
    "KeyNotFoundError",
    "InvalidSignatureError",
    "InvalidAttestationError",
    "ExpiredAttestationError",
    "TaxonomyError",
    "StoreError",
]
