"""Dependency-free pieces shared by the CLI and the lxml-based modules."""

from __future__ import annotations

DEFAULT_LABEL_TEMPLATE = "Return to reference {number} in {chapter}"


class EpubError(ValueError):
    """A book cannot be safely inspected or patched by this adapter."""
