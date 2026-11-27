"""Shared kernel: IDs, values, records, errors and serialization.

Lowest layer. It depends only on the standard library and Pydantic, never on
ports, services or adapters (import-linter contract in pyproject.toml).
Nothing in this package grants authority: a valid record is data, not a
permission (AGENTS.md 4.12).
"""
