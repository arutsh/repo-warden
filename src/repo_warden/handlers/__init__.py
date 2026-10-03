"""Capability handlers and the default registry."""

from __future__ import annotations

from ..capabilities import Registry
from . import fs


def default_registry() -> Registry:
    return Registry([fs.READ])
