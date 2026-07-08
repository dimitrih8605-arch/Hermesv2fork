"""Auto-init Hermes Kernel on profile boot.

ponytail: module-level import triggers init. No hooks needed.
"""
import sys, os

_VENV = os.path.expanduser("~/HERMES_WORKSPACE/.venv/lib/python3.13/site-packages")
_WKSP = os.path.expanduser("~/HERMES_WORKSPACE/projects/hermes-kernel")

for _p in [_VENV, _WKSP]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from kernel_bridge import k  # noqa: F401
    _OK = True
except ImportError:
    # ponytail: no kernel installed? silently skip.
    # Before reporting error, try direct import from workspace path
    try:
        sys.path.insert(0, os.path.expanduser("~/.hermes/profiles/dimitri/scripts"))
        from kernel_bridge import k  # noqa: F401
        _OK = True
    except ImportError:
        _OK = False


def register() -> dict:
    """Hermes plugin registration. Returns metadata."""
    return {
        "name": "kernel-bridge",
        "description": "Hermes Kernel auto-init",
        "initialized": _OK,
    }
