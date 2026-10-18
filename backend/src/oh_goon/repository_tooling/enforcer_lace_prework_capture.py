"""Compatibility import identity for the one installed native issuer owner.

Not a separate publisher: both paths point to the exact same module object.
"""
from importlib import import_module as _import_module
from sys import modules as _modules

_modules[__name__] = _import_module("oh_goon.native_issuer.enforcer_lace_prework_capture")
