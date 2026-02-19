# Codex Suggestions for `kredo`

Date: 2026-02-19

## Review scope completed
- Core package: `src/kredo/*`
- API: `src/kredo/api/*`
- Web app: `app/*`
- Marketing/docs site: `site/*`
- LangChain integration: `langchain-kredo/*`
- Docs/specs: `README.md`, `SCOPE.md`, `docs/*`
- Validation runs:
  - `pytest -q` -> 305 passed
  - `pytest -q langchain-kredo/tests` -> 86 passed
  - `npm run build` in `site/` -> successful

## Priority 0 (fix first)

1. Prevent attestation overwrite-by-ID.
- Why: `INSERT OR REPLACE` allows replacing an existing attestation if a caller reuses the same `id`, which can corrupt audit history.
- Where: `src/kredo/store.py:278`, `src/kredo/api/routers/attestations.py:89`
- Suggestion: make attestation IDs immutable (`INSERT` only), return `409 Conflict` on duplicate ID, and add a test that duplicate IDs are rejected.

2. Fix incorrect status code for unknown agent.
- Why: API currently returns `200` with an error tuple/body instead of proper `404`.
- Where: `src/kredo/api/routers/registration.py:106`, `tests/test_api.py:212`
- Suggestion: return `JSONResponse(status_code=404, ...)` and update tests to expect `404`.

3. Fix SPA deep-link regression in Browse view.
- Why: `BrowseView` attempts to call `KredoApp.getHashParam`, but that method is not exported.
- Where: `app/js/app.js:24`, `app/js/app.js:102`, `app/js/views/browse.js:67`
- Suggestion: export `getHashParam` from `KredoApp` or change browse route parsing to use internal hash parsing directly.

4. Harden private-key protection in browser storage.
- Why: plaintext key can be stored in localStorage; passphrase-derived key uses one unsalted SHA-512 pass and is brute-force friendly.
- Where: `app/js/storage.js:35`, `app/js/crypto.js:231`
- Suggestion: default to encrypted key storage, use WebCrypto PBKDF2/Argon2id with salt + iterations, and add explicit warnings when storing plaintext.

## Priority 1 (high-value reliability and security)

5. Lock down CORS for production.
- Why: wildcard CORS on all methods/headers is broad for write endpoints.
- Where: `src/kredo/api/app.py:54`
- Suggestion: configure allowlist origins by env and narrow methods/headers for write paths.

6. Replace in-memory rate limiter for production deployments.
- Why: per-process memory limiter resets on restart and does not protect multi-instance deployments.
- Where: `src/kredo/api/rate_limit.py:1`
- Suggestion: pluggable Redis/token-bucket limiter with shared state and deterministic windows.

7. Reduce key-squatting / profile hijack risk in registration flow.
- Why: registration is unsigned and can overwrite display names for known pubkeys.
- Where: `src/kredo/api/routers/registration.py:54`, `src/kredo/store.py:221`
- Suggestion: keep unsigned bootstrap if desired, but add optional signed claim/challenge mode and protect name changes behind signature verification.

8. Move filtering/pagination into SQL.
- Why: `/search` loads broad result sets in memory then applies `skill/min_proficiency` filter and pagination.
- Where: `src/kredo/api/routers/search.py:42`, `src/kredo/store.py:325`
- Suggestion: add SQL-level filters and pagination in store API for predictable latency at scale.

9. Cache or precompute trust/ring analysis.
- Why: profile and trust endpoints can recompute graph-heavy analytics repeatedly.
- Where: `src/kredo/trust_analysis.py:179`, `src/kredo/trust_analysis.py:357`, `src/kredo/api/routers/profiles.py:112`
- Suggestion: add short TTL cache or materialized analysis table keyed by `pubkey` + data version.

10. Normalize API error semantics.
- Why: some invalid requests return `200` with `{"error": ...}` payload instead of 4xx.
- Where: `src/kredo/api/routers/taxonomy.py:82`, `tests/test_api.py:237`
- Suggestion: standardize on HTTP error codes and a single error envelope schema.

11. Stop API helper usage of private DB handle.
- Why: coupling to `store._conn` bypasses store abstraction and makes refactors risky.
- Where: `src/kredo/api/deps.py:54`
- Suggestion: add public methods to `KredoStore` for known-key listing/counting.

12. Tighten identity import validation.
- Why: import accepts weakly-validated fields and seed import only checks length.
- Where: `app/js/storage.js:98`, `app/js/views/setup.js:241`
- Suggestion: validate `ed25519` format, hex charset, key lengths, and enforce type whitelist.

## Priority 2 (product quality and maintainability)

13. Make web app API base configurable.
- Why: frontend is hardwired to production API URL.
- Where: `app/js/api.js:8`
- Suggestion: derive from runtime config or URL param with safe defaults.

14. Add CSP/security headers for app and site.
- Why: current static frontend uses inline scripts and broad execution model.
- Where: `app/index.html:1`, `site/src/layouts/Base.astro:1`
- Suggestion: move scripts to external files and enforce CSP (`script-src 'self'`, etc.).

15. Remove per-component duplicate copy-button listeners.
- Why: each `CodeBlock` instance binds handlers to all `.copy-btn` elements, causing redundant listeners.
- Where: `site/src/components/CodeBlock.astro:17`
- Suggestion: bind once with event delegation in layout-level script.

16. Unify HTTP behavior in `langchain-kredo` client.
- Why: `list_agents()` bypasses shared client error handling and duplicates transport logic.
- Where: `langchain-kredo/langchain_kredo/_client.py:113`
- Suggestion: add `list_agents` to `KredoClient` and reuse `_request`.

17. Make callback handler concurrency-safe.
- Why: single global `_chain_depth` can misclassify top-level chains under concurrent runs.
- Where: `langchain-kredo/langchain_kredo/callback.py:110`
- Suggestion: track top-level roots by `parent_run_id is None` and run-ID map instead of a global depth counter.

18. Improve trust-gate failure observability.
- Why: `check()` swallows backend exceptions and returns generic fail with no reason.
- Where: `langchain-kredo/langchain_kredo/trust_gate.py:102`
- Suggestion: add optional `error`/`reason` in `TrustCheckResult` for operator troubleshooting.

19. Replace root-hosted deploy script with safer pipeline.
- Why: direct `scp` to `root@IP` in package script is brittle and high risk.
- Where: `site/package.json:9`
- Suggestion: use non-root deploy user, environment-driven host/path, and CI-based publish.

20. Add frontend automated tests.
- Why: backend is well-tested; SPA/site behavior currently lacks regression coverage.
- Where: `app/*`, `site/*`
- Suggestion: add Playwright smoke tests (routing, setup, attest submit preview, browse profile deep-link, verify flow).

## Priority 3 (documentation and operational hygiene)

21. Resolve version drift in docs and generated API guide.
- Why: docs still reference `0.5.0` while repo is `0.6.0`.
- Where: `VERSION:1`, `docs/skill.md:25`, `docs/velo-code/http-functions.js:33`
- Suggestion: generate API guide from one canonical source at release time.

22. Update stale roadmap/spec status.
- Why: `SCOPE.md` still marked "Scoping" with unchecked completed items.
- Where: `SCOPE.md:5`, `SCOPE.md:220`
- Suggestion: split into `CURRENT_STATE.md` + `ROADMAP.md` to keep implementation reality aligned.

23. Reduce duplicated docs to a single source of truth.
- Why: the same long API content exists in multiple places, likely to drift.
- Where: `docs/skill.md:1`, `docs/velo-code/http-functions.js:1`, `README.md:75`
- Suggestion: centralize the API spec in one file and generate derivatives.

24. Add explicit DB migration strategy.
- Why: schema evolves in-place via `_SCHEMA`; no migration history/versioning.
- Where: `src/kredo/store.py:19`
- Suggestion: add migration table and sequential migration scripts for safe upgrades.

25. Add write-path audit events.
- Why: revocations, disputes, taxonomy writes, and registrations are high-value actions.
- Where: `src/kredo/api/routers/*`
- Suggestion: append-only audit table with actor key/IP/timestamp/action/result.

## Suggested execution order
1. P0 items 1-4
2. P1 items 5-12
3. P2 items 13-20
4. P3 items 21-25
