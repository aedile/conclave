# ADR-0023 — Frontend: React 18 + Vite 6 SPA

**Status:** Accepted
**Date**: 2026-03-15
**Task**: P5-T5.3 — Build Accessible React SPA & "Vault Unseal" Screen
**Authors**: Engineering Team

---

## Context

The Conclave Engine requires a minimal operator UI to unseal the vault and navigate post-unseal
workflows. The UI must:

1. Run in air-gapped environments — zero external CDN, font, or API dependencies at runtime.
2. Meet WCAG 2.1 AA accessibility standards (4.5:1 contrast, keyboard navigation, screen reader
   support).
3. Integrate with the FastAPI backend over the local loopback network.
4. Have 90%+ test coverage enforced by the same CI quality gate as the Python backend.
5. Ship as a small, self-contained static bundle that can be served without a Node.js runtime in
   production.

Several SPA frameworks and build tools were considered. This ADR documents the decision and
rationale for each architectural choice.

---

## Decisions

### 1. React 18 (UI Library)

**Decision**: Use React 18 with TypeScript (strict mode).

**Rationale**:
- React's component model and hooks API are familiar to a broad pool of engineers, reducing
  onboarding friction.
- React 18 introduces concurrent rendering features that improve perceived responsiveness.
- The TypeScript integration is mature and well-supported.
- React is dependency-free at runtime (no runtime framework server required).

**Alternatives considered**:
- Vue 3: comparable DX, but smaller ecosystem for the TypeScript integration layer.
- Svelte: smaller bundle, but less tooling support for the strict TypeScript + accessibility
  test pattern we require.
- Vanilla TypeScript: considered but rejected; the component abstraction layer reduces
  repetition and improves maintainability as the UI grows in Phase 6+.

---

### 2. Vite 6 (Build Tooling)

**Decision**: Use Vite 6 as the build tool and dev server.

**Rationale**:
- Vite provides native ES module hot-module replacement (HMR) for fast development iteration.
- Production builds use Rollup under the hood, producing highly optimised, tree-shaken bundles.
- The `server.proxy` feature in `vite.config.ts` forwards `/api` requests to the FastAPI backend
  during development without CORS configuration (see "Dev Proxy Pattern" section below).
- Vite has first-class TypeScript support with zero configuration.

**Alternatives considered**:
- Create React App (Webpack): deprecated by the React team; slow HMR.
- Next.js: server-side rendering is not needed (and adds operational complexity in air-gapped
  environments). A purely static SPA is the correct shape.
- Parcel: simpler configuration but less control over the bundle splitting strategy.

---

### 3. Vitest + Testing Library (Test Strategy)

**Decision**: Use Vitest as the test runner with `@testing-library/react` and
`@testing-library/user-event` for component tests. Use Playwright for end-to-end tests
(deferred to Phase 6 when multi-route flows exist).

**Rationale**:
- Vitest shares Vite's transform pipeline, so the test environment matches the production build
  exactly (same TypeScript config, same module resolution).
- `@testing-library/react` enforces testing from the user's perspective (accessible roles,
  labels) rather than implementation details, which directly supports WCAG compliance
  verification in tests.
- `@testing-library/user-event` simulates real keyboard and pointer events, enabling accurate
  accessibility interaction testing.
- The 90% coverage gate (matching the Python backend gate) is enforced via `@vitest/coverage-v8`.

**Why not Jest**:
- Jest requires a separate transform configuration (Babel or `ts-jest`), creating a mismatch
  between the Jest transform and the Vite/Rollup transform. This has historically caused
  subtle test-passes-but-build-fails bugs.

**Why Playwright is deferred**:
- E2E tests require a running backend and database. These are not available in the unit-test
  CI job. Playwright will be introduced in Phase 6 as an integration-test gate against the
  Docker Compose stack.

---

### 4. Dev Proxy Pattern (`vite.config.ts`)

**Decision**: Proxy `/api` requests from the Vite dev server to the FastAPI backend at
`http://localhost:8000` during development.

**Rationale**:
- The frontend and backend share the same origin in production (static files served by Nginx or
  FastAPI's `StaticFiles` mount), so no CORS configuration is needed.
- The Vite proxy replicates this same-origin behaviour in development, preventing CORS
  mismatches that only appear in production.

**Configuration**:
```typescript
// vite.config.ts (excerpt)
server: {
  proxy: {
    "/api": "http://localhost:8000",
  },
},
```

---

### 5. Production Serving Strategy

**Decision**: The production bundle (`frontend/dist/`) is served as static files by an Nginx
reverse proxy that sits in front of the FastAPI application. FastAPI does not serve the
frontend directly in production.

**Rationale**:
- Nginx is optimised for serving static assets (sendfile, gzip compression, cache headers).
- Keeping Nginx as the static asset server means the FastAPI process handles only API requests,
  reducing memory and event-loop contention.
- The Docker Compose file mounts `frontend/dist/` into the Nginx container at
  `/usr/share/nginx/html`.

**Development exception**: During development, `npm run dev` starts the Vite dev server on
port 5173. The Nginx container is not used in development.

---

### 6. WOFF2 Font Bundling (Local-Only, No CDN)

**Decision**: The Inter font is bundled as local WOFF2 files under `frontend/src/assets/fonts/`
and loaded via a `@font-face` declaration in `frontend/src/styles/fonts.css`. No external CDN
(Google Fonts, Bunny Fonts, etc.) is used.

**Rationale**:
- **Air-gapped requirement**: The system must operate without any external network access. CDN
  font requests would fail in a production air-gapped environment.
- **Privacy**: Google Fonts leaks client IP addresses to Google. Local fonts prevent this.
- **Performance**: Bundled fonts are served from the same origin with optimal cache headers.
- **CSP compliance**: The Content-Security-Policy header explicitly blocks `font-src` from
  external origins. Local fonts satisfy this policy without exceptions.

**Font source**: Inter is licensed under the SIL Open Font License (OFL-1.1), permitting
redistribution and bundling.

---

### 7. Content Security Policy (CSP) Decisions

**Decision**: The `CSPMiddleware` in `bootstrapper/dependencies/csp.py` adds the following
`Content-Security-Policy` header to every FastAPI response:

```
default-src 'self';
script-src 'self';
style-src 'self' 'unsafe-inline';
font-src 'self';
img-src 'self' data:;
connect-src 'self';
frame-ancestors 'none';
base-uri 'self';
form-action 'self';
```

**Rationale for `style-src 'unsafe-inline'`**:
- React inline styles (the `style` prop in JSX) are rendered as HTML `style` attributes. These
  are classified as inline styles by the browser's CSP engine.
- Removing `'unsafe-inline'` from `style-src` would require migrating all inline styles to
  CSS classes, which is a Phase 6+ refactor.
- Script injection via CSS is a lower-severity vector than script injection via `script-src`.
  `script-src 'self'` (without `'unsafe-inline'`) is maintained as the strict gate.

**`frame-ancestors 'none'`**: Prevents the Conclave Engine UI from being embedded in an
iframe, mitigating clickjacking attacks.

---

## Consequences

### Positive
- Zero external runtime dependencies — operates fully air-gapped.
- 90% test coverage gate mirrors the backend standard, ensuring consistent quality.
- Typed end-to-end (TypeScript strict) reduces runtime errors.
- Accessibility requirements verified in CI via Testing Library's accessible query API.

### Negative / Trade-offs
- `'unsafe-inline'` in `style-src` is a minor CSP weakening. Accepted until Phase 6.
- Playwright E2E tests are deferred; until they exist, multi-route integration behaviour
  is only tested at the unit level.
- Bundle size is slightly larger than a Svelte equivalent due to the React runtime.

---

## References

- [React 18 Release Notes](https://reactjs.org/blog/2022/03/29/react-v18.html)
- [Vite 6 Documentation](https://vite.dev/)
- [Vitest Documentation](https://vitest.dev/)
- [Testing Library — Accessibility Queries](https://testing-library.com/docs/queries/about)
- [Inter Font — SIL Open Font License](https://rsms.me/inter/)
- [WCAG 2.1 AA — Web Content Accessibility Guidelines](https://www.w3.org/TR/WCAG21/)
- [OWASP Content Security Policy Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html)
