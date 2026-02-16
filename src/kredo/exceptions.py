"""Kredo error hierarchy."""


class KredoError(Exception):
    """Base exception for all Kredo errors."""


class KeyNotFoundError(KredoError):
    """Requested key does not exist in the store."""


class InvalidSignatureError(KredoError):
    """Ed25519 signature verification failed."""


class InvalidAttestationError(KredoError):
    """Attestation fails schema or business-rule validation."""


class ExpiredAttestationError(KredoError):
    """Attestation has passed its expiry date."""


class TaxonomyError(KredoError):
    """Invalid domain or skill per the bundled taxonomy."""


class StoreError(KredoError):
    """SQLite storage operation failed."""
