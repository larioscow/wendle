"""Pluggable graph→text emitters (the v2 codegen seam). Importing the package registers
the built-in emitters; future emitters (Maestro YAML, Python nav module) register the same
way and inherit the credential-safety contract test automatically."""
from wendle.emit import dot as _dot  # noqa: F401 — registration side-effect
from wendle.emit import flow as _flow  # noqa: F401 — registration side-effect
from wendle.emit import maestro as _maestro  # noqa: F401 — registration side-effect
from wendle.emit import python as _python  # noqa: F401 — registration side-effect
from wendle.emit.base import Emitter, all_emitters, get_emitter, register

__all__ = ["Emitter", "all_emitters", "get_emitter", "register"]
