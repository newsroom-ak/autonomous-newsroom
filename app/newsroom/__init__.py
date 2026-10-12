"""Newsroom package.

This package provides the core functionality for the autonomous newsroom
application: database handling, feed fetching, and a refresh CLI entry point.
"""

# The package deliberately exports only the public helpers that
# other tickets may import. Keeping the __all__ small helps with
# discoverability.
__all__ = [
    "db",
    "feed",
    "refresh",
]

