"""Boss bridge — kernel access from dimitri profile.

Use via:
    from kernel_bridge import k  # ready-to-use Kernel instance

Requires workspace venv Python (for pydantic v2):
    ~/HERMES_WORKSPACE/.venv/bin/python3 -c "from kernel_bridge import k"
"""
import sys, os

sys.path.insert(0, os.path.expanduser("~/HERMES_WORKSPACE/projects/hermes-kernel"))
from kernel import Kernel  # noqa: E402

PERSIST_DIR = os.path.expanduser("~/.hermes/runtime/kernel")
k = Kernel("dimitri-boss", persist_dir=PERSIST_DIR)
k.start()

__all__ = ["k", "Kernel", "AgentContract", "Task", "Capability", "CapabilityStatus"]
from kernel import AgentContract  # noqa: E402
from kernel.task import Task  # noqa: E402
from kernel.capability import Capability, CapabilityStatus  # noqa: E402
