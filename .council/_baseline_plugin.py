"""Preload `orchestrator_server` from the pre-patch baseline copy.

The copy lives in the orchestrator ROOT (not .council) on purpose: the module
derives paths from Path(__file__).parent, so loading it from a subdirectory
would change BASE_DIR and invent differences the patch never caused.
The live orchestrator_server.py is never touched.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "_baseline_orchestrator_server_20260827.py"
assert BASE.is_file(), BASE
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("orchestrator_server", BASE)
mod = importlib.util.module_from_spec(spec)
sys.modules["orchestrator_server"] = mod
spec.loader.exec_module(mod)
print("BASELINE orchestrator_server <-", BASE.name, flush=True)
