"""Path pattern matching for resources (WP-09A).

Two forms only: an exact relative path, or ``dir/**`` meaning everything under
``dir/``. Any other wildcard matches nothing (fail closed).
"""

from __future__ import annotations


def matches(pattern: str, path: str) -> bool:
    if ".." in pattern.split("/") or ".." in path.split("/") or "*" in path:
        return False
    if pattern.endswith("/**"):
        prefix = pattern[:-2]
        return "*" not in prefix and path.startswith(prefix) and len(path) > len(prefix)
    if "*" in pattern:
        return False
    return pattern == path


def any_match(patterns: tuple[str, ...], path: str) -> bool:
    return any(matches(p, path) for p in patterns)
