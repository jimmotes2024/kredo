"""Kredo Discovery API — FastAPI application.

Public attestation discovery and verification service.
Signature-only auth: Ed25519 signature IS the authentication.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from kredo.exceptions import (
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
    registration,
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


app = FastAPI(
    title="Kredo Discovery API",
    description="Public attestation discovery and verification for the Kredo protocol.",
    version=_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
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
app.include_router(trust_analysis.router)


# --- Health ---

@app.get("/health")
async def health():
    return {"status": "ok", "version": _VERSION}
