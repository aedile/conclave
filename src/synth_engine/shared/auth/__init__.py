"""Zero-Trust authentication and RBAC scope utilities.

This sub-package provides framework-agnostic JWT validation with
client-binding and role-based access control scopes for the Conclave Engine.

The FastAPI dependency factory lives in
``bootstrapper/dependencies/auth.get_current_user`` so that this package
remains free of web-framework coupling.
"""
