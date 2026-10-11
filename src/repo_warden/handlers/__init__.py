"""Capability handlers and the default registry."""

from __future__ import annotations

from ..capabilities import Registry
from . import fs, search


def default_registry() -> Registry:
    return Registry([fs.READ, fs.LIST, search.make_search()])
