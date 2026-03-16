# ADR-0024 — Pure ASGI Body-Replay Pattern for Body-Inspecting Middleware

**Status:** Accepted
**Date**: 2026-03-15
**Task**: P6-T6.2 — NIST SP 800-88 Erasure, OWASP validation, LLM Fuzz Testing
**Authors**: Engineering Team

---

## Context

The Conclave Engine requires middleware that reads the HTTP request body before passing the
request to the inner application. Specifically, `RequestBodyLimitMiddleware` must:

1. Check whether the body exceeds the 1 MiB size limit (HTTP 413 rejection).
2. Check whether a JSON body exceeds 100 nesting levels (HTTP 400 rejection).
3. Forward the full, unmodified body to the inner application if neither limit is breached.

The natural implementation approach — subclassing `BaseHTTPMiddleware` from Starlette — has a
fundamental flaw when the middleware reads the request body.

### Problem: `BaseHTTPMiddleware` drains the ASGI receive channel

`BaseHTTPMiddleware.dispatch` wraps the ASGI lifecycle and exposes a `Request` object to the
developer. When `dispatch` reads the body via `request.body()` or `request.stream()`, it
consumes bytes from the underlying ASGI `receive` channel. This channel is a single-pass
iterator: once drained, subsequent calls to `receive()` return an empty `http.request` message.

The consequence is that the inner application — which also calls `receive()` to obtain the
request body — receives an empty body. This is a silent data-loss bug: the route handler sees
a valid HTTP request but with no payload, causing spurious 422 validation errors or incorrect
processing.

This behaviour is a known limitation of `BaseHTTPMiddleware` and is documented in the Starlette
source. It cannot be worked around within `BaseHTTPMiddleware` without monkey-patching internals.

---

## Decision

All middleware that **reads the HTTP request body** MUST be implemented as a **pure ASGI
callable** using the **body-replay pattern**:

1. Implement `__call__(self, scope, receive, send)` directly — do not subclass
   `BaseHTTPMiddleware`.
2. **Accumulate** body bytes by consuming `http.request` ASGI messages from the `receive`
   callable, checking limits incrementally as bytes arrive.
3. **Inspect** the complete buffered body (e.g., measure JSON depth).
4. **Replay** the buffered body to the inner application by wrapping `receive` with a closure
   that, on the first call, yields an `http.request` message containing the full buffered body
   with `more_body=False`. Subsequent calls fall through to the original `receive` (for
   disconnect events and WebSocket upgrades).

The replay closure pattern:

```python
body_sent = False

async def _replay_receive() -> Message:
    nonlocal body_sent
    if not body_sent:
        body_sent = True
        return {"type": "http.request", "body": body_bytes, "more_body": False}
    return await receive()

await self._app(scope, _replay_receive, send)
```

This ensures the inner application always receives the complete request body exactly once,
regardless of how many chunks the original `receive` stream used.

---

## Consequence

### Positive

- The inner application always receives the full, unmodified request body.
- Body-limit rejections (413, 400) fire before any inner middleware or route handler processes
  the body, satisfying the CONSTITUTION Priority 0 DoS protection mandate.
- Pure ASGI callables are simpler to reason about than `BaseHTTPMiddleware` subclasses: there
  is no hidden Starlette machinery between the middleware and the ASGI primitives.
- The pattern is well-established in the ASGI ecosystem and compatible with all ASGI servers
  (uvicorn, hypercorn, daphne).

### Negative / Constraints

- Pure ASGI callables are more verbose than `BaseHTTPMiddleware` subclasses. The developer must
  manually handle the `http.disconnect` message type and the `more_body` flag.
- The entire request body must be buffered in memory before the inner application receives it.
  For this engine, the 1 MiB size limit bounds the memory footprint per request.

### Hard Constraint

This is a **hard constraint enforced by code review and architecture review**.
`BaseHTTPMiddleware` is acceptable only for middleware that does **NOT** read the request body
(e.g., adding response headers, timing middleware, logging middleware that reads only scope
metadata). Any middleware that calls `request.body()`, `request.stream()`, or otherwise reads
bytes from the ASGI `receive` channel MUST use the pure ASGI body-replay pattern.

Violations of this constraint will be caught at architecture review as a blocker finding.

---

## References

- [Starlette `BaseHTTPMiddleware` source — receive channel draining caveat](https://github.com/encode/starlette/blob/master/starlette/middleware/base.py)
- [ASGI specification — HTTP Connection Scope](https://asgi.readthedocs.io/en/latest/specs/www.html#http-connection-scope)
- [OWASP — Denial of Service via Oversized Payloads](https://owasp.org/www-community/attacks/Denial_of_Service)
- `src/synth_engine/bootstrapper/dependencies/request_limits.py` — reference implementation
