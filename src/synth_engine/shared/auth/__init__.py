"""Zero-Trust authentication and RBAC scope utilities.

This sub-package provides JWT validation with client-binding and
role-based access control scopes for the Conclave Engine API.

Cross-cutting concern: shared by bootstrapper (FastAPI middleware) and
any future module that needs to validate caller identity.
"""
