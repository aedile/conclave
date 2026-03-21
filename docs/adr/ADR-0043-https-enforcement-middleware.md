# ADR-0043: HTTPS Enforcement Middleware

**Status**: Accepted
**Date**: 2026-03-21
**Task**: T42.2 — Add HTTPS Enforcement & Deployment Safety Checks
**Deciders**: Engineering team

---

## Context

The Conclave Engine streams synthetic data Parquet files to operators over its
download endpoint.  Prior to T42.2 there was no application-layer enforcement
requiring that connections arrive over HTTPS.  A misconfigured or inadvertent
HTTP deployment would silently stream synthetic data — which may carry residual
statistical signal about the original dataset — in cleartext over the network.

Several complementary controls already existed (nginx TLS termination in the
reference deployment, `CONCLAVE_SSL_REQUIRED` for the ingestion validator), but
none of them produced an application-layer rejection of plaintext HTTP requests
to the API itself.  Relying solely on the reverse proxy means a misconfigured
proxy, a direct connection to the app port, or a testing environment that bypasses
nginx would all result in unprotected data exposure without any visible error.

---

## Decision

### 1. Application-layer HTTPS enforcement via Starlette middleware

`HTTPSEnforcementMiddleware` is a `BaseHTTPMiddleware` subclass registered in
`bootstrapper/middleware.py`.  In production mode it inspects every incoming
request and rejects any whose effective scheme is not `https`.

This is **defence-in-depth**: even if the reverse proxy is misconfigured, the
application itself refuses to serve synthetic data over plaintext.

### 2. 421 Misdirected Request rather than 301/302 redirect

The middleware returns HTTP 421 (RFC 7231 §6.5.11) when a plaintext request is
rejected.  A redirect to HTTPS (301 or 302) was explicitly rejected because:

- A redirect transmits the full request line, headers, and any request body
  over the cleartext connection before the client receives the redirect
  response.  In a man-in-the-middle scenario the attacker sees everything
  before the client ever switches to HTTPS — the classic SSL-stripping attack.
- 421 causes the client to fail immediately without exposing request content.
  It forces the operator to fix the deployment configuration rather than
  silently degrading.
- RFC 7231 §6.5.11 defines 421 as the semantically correct status code for
  "the server is not able to produce a response for this combination of scheme,
  authority, and request target", which precisely describes this situation.

### 3. X-Forwarded-Proto trust model

The middleware reads the `X-Forwarded-Proto` header as the authoritative scheme
indicator, falling back to `request.url.scheme` when the header is absent.

The header value is normalised with `.strip().lower()` before comparison so that
mixed-case values (`HTTPS`) and whitespace-padded values (`  https  `) are
treated correctly.

**Security assumption**: the reverse proxy MUST strip any `X-Forwarded-Proto`
header supplied by the client before forwarding the request to the application.
A proxy that does not strip this header allows a client to bypass enforcement by
sending `X-Forwarded-Proto: https` on a plaintext connection.  This is a
standard, well-documented requirement for the trusted-proxy pattern; it is
covered in `docs/PRODUCTION_DEPLOYMENT.md`.

### 4. Middleware stack position

`HTTPSEnforcementMiddleware` is registered last in `setup_middleware()`.
Starlette processes middleware in LIFO order, so the last-registered middleware
is the first to receive each request.  This makes HTTPS enforcement the
outermost gate: plaintext requests are rejected before any other middleware
(authentication, rate limiting, CSP) processes them.

### 5. Development mode exemption

When `CONCLAVE_ENV != "production"`, the middleware passes all requests through
unchanged.  This allows operators to run the application over plain HTTP during
local development and integration testing without requiring a local TLS setup.
The production flag defaults to `get_settings().is_production()` but can be
overridden explicitly in tests.

### 6. Startup misconfiguration warning

`warn_if_ssl_misconfigured()` is called from `config_validation.validate_config()`
during application startup.  It emits a `WARNING` log when `CONCLAVE_SSL_REQUIRED`
is `True` but `CONCLAVE_TLS_CERT_PATH` is absent, indicating that the operator
has declared SSL required but has not configured a local cert path.  No exception
is raised — the warning is advisory and does not block startup, because TLS may
be terminated externally without a local certificate file.

---

## Consequences

### Positive

- Synthetic data cannot be transmitted in cleartext from a production deployment,
  even if the reverse proxy is misconfigured or bypassed.
- 421 forces immediate, visible failure rather than silent SSL-stripping exposure.
- Mixed-case and whitespace-padded `X-Forwarded-Proto` values are handled
  correctly, preventing accidental bypass from non-standard proxy behaviour.
- The outermost-middleware position ensures no other middleware processes a
  rejected request — authentication and rate-limit logic do not run for
  plaintext requests, reducing attack surface.
- Development and CI workflows are unaffected by the exemption for non-production
  environments.

### Negative / Constraints

- **Trusted-proxy requirement**: deployments without a reverse proxy that strips
  client-supplied `X-Forwarded-Proto` headers are vulnerable to a trivial bypass
  (client sends `X-Forwarded-Proto: https` on a plaintext connection).
  `docs/PRODUCTION_DEPLOYMENT.md` documents this requirement explicitly.
- **No TLS termination at the application layer**: the Conclave Engine does not
  handle TLS certificates directly.  Operators must supply a reverse proxy.
  This is an existing architectural constraint, not one introduced by T42.2.

---

## Alternatives Considered

| Option | Rejected Because |
|--------|-----------------|
| 301/302 redirect to HTTPS | Sends request content over cleartext before the redirect fires — SSL-stripping attack surface. |
| Rely solely on reverse proxy | Defence-in-depth requires the application to enforce its own security invariants. A misconfigured proxy silently exposes data. |
| HSTS header only | HSTS protects returning clients but does not protect first visits or clients that ignore the header. Not a substitute for rejection. |
| `ssl_required` flag at the Uvicorn level | Uvicorn TLS configuration requires certificate files on disk; incompatible with the reverse-proxy deployment model and air-gapped cert management. |

---

## References

- RFC 7231 §6.5.11 — 421 Misdirected Request
- `src/synth_engine/bootstrapper/dependencies/https_enforcement.py` — middleware implementation
- `src/synth_engine/bootstrapper/middleware.py` — middleware registration
- `src/synth_engine/bootstrapper/config_validation.py` — startup warning wiring
- `docs/PRODUCTION_DEPLOYMENT.md` — reverse proxy configuration requirements
- ADR-0039 — JWT Bearer Token Authentication (authentication layer sits inside HTTPS enforcement)
- ADR-0041 — Data Retention (synthetic data sensitivity context)
