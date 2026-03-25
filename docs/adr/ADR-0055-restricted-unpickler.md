# ADR-0055: Restricted Unpickler for ModelArtifact Deserialization

**Status:** Accepted
**Date:** 2026-03-25
**Deciders:** PM, Architecture Reviewer, Security Reviewer
**Task:** T55.2 — Replace Pickle with Safe Serialization (Restricted Unpickler)

---

## Context

`ModelArtifact.load()` previously used bare `pickle.loads()` to deserialize trained CTGAN
model artifacts from disk.  While HMAC-SHA256 verification was already in place (ADR-040),
this posed a defense-in-depth gap:

1. **Key compromise risk**: If an attacker obtains the artifact signing key, they can produce
   a valid-HMAC pickle payload that executes arbitrary code on deserialization.
2. **Pickle's execution model**: `pickle.loads()` will invoke `__reduce__` on any class
   found in the stream, making it trivially exploitable via `os.system`, `subprocess.Popen`,
   `importlib.import_module`, etc.
3. **Alternative format constraints**: SDV's `CTGANSynthesizer` embeds complex state
   (PyTorch tensors, Opacus DP wrappers, RDT DataTransformers, scikit-learn transformers)
   that cannot be safely extracted to a non-pickle format (JSON, MessagePack, safetensors)
   without deep SDV internals changes — an out-of-scope rewrite.

The `bandit` scan flagged `B301` on the `pickle.loads()` call as a security risk (P55
critical issues remediation).

---

## Decision

Replace the bare `pickle.loads()` call in `ModelArtifact.load()` with a
`RestrictedUnpickler` subclass of `pickle.Unpickler` that overrides `find_class()` to
enforce an explicit module allowlist.

The allowlist includes only modules required for legitimate `ModelArtifact` deserialization:

| Module prefix | Reason |
|---------------|--------|
| `synth_engine.modules.synthesizer.models` | `ModelArtifact` dataclass itself |
| `builtins` | Python built-in types (list, dict, str, int, float, bool, etc.) |
| `_codecs` | Used by pickle internally for bytes/bytearray reconstruction |
| `collections` | `OrderedDict` used in sklearn/SDV internals |
| `datetime` | datetime objects in SDV model metadata |
| `numpy` | Array dtypes and values in DataTransformer internals |
| `pandas` | dtype reconstruction from Parquet-originated DataFrames |
| `torch` | Model weights (tensors) in CTGANSynthesizer |
| `sdv`, `ctgan`, `rdt` | SDV/CTGAN synthesizer state |
| `copulas` | SDV dependency for statistical distributions |
| `opacus` | DP wrapper around the PyTorch training loop |
| `sklearn` | scikit-learn transformers used by rdt/DataTransformer |
| `scipy` | Statistical distributions used by copulas/SDV |
| `joblib` | Serialization utility used by sklearn |

Any class not on this list raises `SecurityError` **before any bytecode is executed**.

Defense-in-depth order:
1. HMAC verification (existing, ADR-040) — guards integrity and authenticity.
2. `RestrictedUnpickler.find_class()` (this ADR) — guards against class injection.
3. `isinstance(artifact, ModelArtifact)` (existing) — guards type after deserialization.

---

## Consequences

**Positive:**
- Eliminates the pickle RCE vector: even a signed malicious payload cannot invoke
  `os.system`, `subprocess.Popen`, or other dangerous classes.
- Satisfies `bandit B301` finding without removing pickle entirely.
- Zero change to the on-disk artifact format — fully backward-compatible.
- `RestrictedUnpickler` is exposed as a public class for independent testing.
- `bandit` `nosec B301` comment is updated to reference the restricted unpickler.

**Negative / Constraints:**
- Future SDV version upgrades may introduce new dependencies that require allowlist
  expansion.  The allowlist is explicit and must be maintained.
- If a legitimate SDV dependency is missing from the allowlist, artifact loading will
  fail with a `SecurityError` at deserialization time.  This is intentional fail-closed
  behavior — add the module to the allowlist rather than widening to a wildcard.
- The allowlist currently includes broad module prefixes (e.g., `torch.*`, `sdv.*`).
  A future tightening pass could enumerate exact classes, but this is deferred as SDV
  internals are not stable across minor versions.

---

## Alternatives Considered

**1. Migrate to `safetensors` + JSON metadata**
Only torch weights can be saved in `safetensors` format.  SDV's `DataTransformer`,
`CTGANSynthesizer` training state, and RDT transformer state use non-tensor Python objects.
Full migration would require maintaining a fork of SDV internals or waiting for upstream
SDV safe-serialization support.  **Not viable in this phase.**

**2. Remove HMAC and trust filesystem ACLs**
Relies on OS-level access controls to prevent artifact tampering.  Provides no protection
against a compromised application process, supply chain attacks, or S3 bucket
misconfiguration.  **Rejected: weaker than current posture.**

**3. Use `dill` or `cloudpickle` with allowlist**
These libraries extend pickle in ways that are harder to restrict.  The standard
`pickle.Unpickler` subclass approach is well-documented and auditable.  **Rejected:
unnecessary complexity.**

**4. `RestrictedUnpickler` with exact class enumeration**
Would require listing every internal class from SDV/CTGAN/Opacus/torch.  These change
across minor version bumps, making maintenance fragile.  Module-prefix matching (this ADR)
provides acceptable security with lower maintenance burden.  **Deferred: revisit at SDV
stable release.**

---

## References

- ADR-0017: CTGAN + Opacus library selection
- T8.2 / ADR-040: HMAC-SHA256 pickle signing
- T47.6: Harden Model Artifact Signature Verification
- T50.4: Pickle TOCTOU Mitigation
- T55.2: Replace Pickle with Safe Serialization (this task)
- Python docs: [pickle.Unpickler.find_class](https://docs.python.org/3/library/pickle.html#pickle.Unpickler.find_class)
- PyCon talk: "Exploiting Pickle" (various) — motivates the class-level interception approach
