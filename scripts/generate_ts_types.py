"""TypeScript interface generation from FastAPI OpenAPI schema.

Uses ``datamodel-code-generator`` to read the FastAPI application's OpenAPI
JSON schema and output TypeScript interfaces to ``frontend/src/types/api.ts``.

This script guarantees frontend/backend type synchronisation: the TypeScript
interfaces are derived directly from the Pydantic models defined in
``bootstrapper/schemas/``.

Usage::

    poetry run generate-ts-types

Or run directly::

    poetry run python3 scripts/generate_ts_types.py [--output PATH]

Configuration via environment variables:
    CONCLAVE_TS_OUTPUT: Override the default output path.

Task: P5-T5.1 — Task Orchestration API Core
"""

# ruff: noqa: S603, S607  — subprocess with fixed args is intentional here (codegen CLI)

from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404 — subprocess used with fixed args (codegen CLI call), not user input
import sys
import tempfile
from pathlib import Path

#: Default output path for generated TypeScript interfaces.
_DEFAULT_OUTPUT_PATH: str = "frontend/src/types/api.ts"

#: Environment variable to override the default output path.
_OUTPUT_ENV_VAR: str = "CONCLAVE_TS_OUTPUT"


def _get_openapi_schema() -> dict[object, object]:
    """Extract the OpenAPI JSON schema from the FastAPI application.

    Imports the FastAPI app and calls ``app.openapi()`` to get the schema
    without starting the HTTP server.

    Returns:
        The OpenAPI schema as a Python dict.
    """
    # Import within function to avoid polluting module scope with app state.
    from synth_engine.bootstrapper.main import create_app

    app = create_app()
    return app.openapi()  # type: ignore[no-any-return]


def generate(output_path: str) -> None:
    """Generate TypeScript interfaces from the FastAPI OpenAPI schema.

    Writes the OpenAPI schema to a temporary JSON file, then invokes
    ``datamodel-code-generator`` to produce TypeScript output.

    Args:
        output_path: Destination file path for the TypeScript output.
    """
    print(f"[generate-ts-types] Extracting OpenAPI schema from FastAPI app…")
    schema = _get_openapi_schema()

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        json.dump(schema, tmp, indent=2)
        tmp_path = tmp.name

    try:
        print(f"[generate-ts-types] Running datamodel-code-generator → {output_path}")
        result = subprocess.run(  # nosec B603 — fixed args from sys.executable (not user input)
            [
                sys.executable,
                "-m",
                "datamodel_code_generator",
                "--input",
                tmp_path,
                "--input-file-type",
                "openapi",
                "--output",
                str(output),
                "--output-model-type",
                "typescript",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            print(f"[generate-ts-types] ERROR: {result.stderr}", file=sys.stderr)
            sys.exit(result.returncode)

        print(f"[generate-ts-types] TypeScript interfaces written to {output_path}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def main() -> None:
    """Entry point for the generate-ts-types CLI command.

    Parses ``--output`` argument (or reads from ``CONCLAVE_TS_OUTPUT``
    environment variable) and calls :func:`generate`.
    """
    parser = argparse.ArgumentParser(
        description="Generate TypeScript interfaces from FastAPI OpenAPI schema."
    )
    parser.add_argument(
        "--output",
        default=os.environ.get(_OUTPUT_ENV_VAR, _DEFAULT_OUTPUT_PATH),
        help=f"Output path for TypeScript interfaces (default: {_DEFAULT_OUTPUT_PATH})",
    )
    args = parser.parse_args()
    generate(args.output)


if __name__ == "__main__":
    main()
