"""Kredo Discovery API — FastAPI application.

Public attestation discovery and verification service.
Signature-only auth: Ed25519 signature IS the authentication.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import os
from typing import Mapping

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from kredo.exceptions import (
    DuplicateAttestationError,
    ExpiredAttestationError,
    InvalidAttestationError,
    InvalidSignatureError,
    KeyNotFoundError,
    StoreError,
    TaxonomyError,
)

from kredo.api.deps import close_store, init_store
from kredo import taxonomy as _taxonomy_module
from kredo.api.routers import (
    attestations,
    profiles,
    ownership,
    registration,
    risk,
    revocations,
    search,
    taxonomy,
    trust_analysis,
)

from kredo import __version__ as _VERSION


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = init_store()
    _taxonomy_module.set_store(store)
    yield
    close_store()


def _parse_csv_env(value: str | None, default: list[str]) -> list[str]:
    if value is None:
        return default
    parts = [part.strip() for part in value.split(",") if part.strip()]
    return parts or default


def _env_truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_cors_settings(environ: Mapping[str, str] | None = None) -> dict:
    env = environ or os.environ
    default_origins = [
        "https://aikredo.com",
        "https://app.aikredo.com",
        "http://localhost:5173",
        "http://localhost:3000",
    ]
    return {
        "allow_origins": _parse_csv_env(
            env.get("KREDO_CORS_ALLOW_ORIGINS"),
            default_origins,
        ),
        "allow_methods": _parse_csv_env(
            env.get("KREDO_CORS_ALLOW_METHODS"),
            ["GET", "POST", "DELETE", "OPTIONS"],
        ),
        "allow_headers": _parse_csv_env(
            env.get("KREDO_CORS_ALLOW_HEADERS"),
            ["Content-Type", "Authorization"],
        ),
        "allow_credentials": _env_truthy(
            env.get("KREDO_CORS_ALLOW_CREDENTIALS"),
            default=False,
        ),
    }


app = FastAPI(
    title="Kredo Discovery API",
    description="Public attestation discovery and verification for the Kredo protocol.",
    version=_VERSION,
    lifespan=lifespan,
)

cors_settings = _get_cors_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_settings["allow_origins"],
    allow_methods=cors_settings["allow_methods"],
    allow_headers=cors_settings["allow_headers"],
    allow_credentials=cors_settings["allow_credentials"],
)


# --- Exception handlers ---

@app.exception_handler(KeyNotFoundError)
async def _key_not_found(request: Request, exc: KeyNotFoundError):
    return JSONResponse(status_code=404, content={"error": str(exc)})


@app.exception_handler(InvalidSignatureError)
async def _invalid_signature(request: Request, exc: InvalidSignatureError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.exception_handler(InvalidAttestationError)
async def _invalid_attestation(request: Request, exc: InvalidAttestationError):
    return JSONResponse(status_code=422, content={"error": str(exc)})


@app.exception_handler(ExpiredAttestationError)
async def _expired_attestation(request: Request, exc: ExpiredAttestationError):
    return JSONResponse(status_code=422, content={"error": str(exc)})


@app.exception_handler(TaxonomyError)
async def _taxonomy_error(request: Request, exc: TaxonomyError):
    return JSONResponse(status_code=422, content={"error": str(exc)})


@app.exception_handler(DuplicateAttestationError)
async def _duplicate_attestation(request: Request, exc: DuplicateAttestationError):
    return JSONResponse(status_code=409, content={"error": str(exc)})


@app.exception_handler(StoreError)
async def _store_error(request: Request, exc: StoreError):
    return JSONResponse(status_code=500, content={"error": "Internal storage error"})


# --- Routers ---

app.include_router(profiles.router)  # before registration (more specific /agents/{pk}/profile)
app.include_router(registration.router)
app.include_router(attestations.router)
app.include_router(search.router)
app.include_router(taxonomy.router)
app.include_router(revocations.router)
app.include_router(ownership.router)
app.include_router(risk.router)
app.include_router(trust_analysis.router)


# --- Health ---

@app.get("/health")
async def health():
    return {"status": "ok", "version": _VERSION}
