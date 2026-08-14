"""orchestrator — target package of the R3 strangler refactor (ADR_R3_ARCHITECTURE.md).

Legacy monoliths (code_tools.py / orchestrator_server.py / tools_v15.py) migrate
here domain-by-domain; they keep re-export shims so the public tool contract
never changes. See research/ADR_R3_ARCHITECTURE.md §D2/§D6 for the module map
and the 10-step extraction order.
"""
