# Server schema fixtures, 2026-11-05

Source before implementation: UPGRADE_MUSE1_EASY_ITEMS_CODO_HANDOFF_20261105.md and the existing handlers at worktree base b2d227a.
- git_status / git_diff: handoff live captures, payload values retained.
- council_message_async success and the two WORKER_MODEL_NOT_SET errors: handler-derived handoff fixtures.
- council_message success: replaced the modeled handoff with the actual sync result-builder output. The regression test calls council_message and its real dispatch result builder; only worker execution is mocked. No message was sent.
- council_worker_model get_one/get_all: complete HTTP captures using action=get, generation 988b01c0bf1c on 2026-11-05. No model setting was changed. Empty current, nullable observed fields, string valid entries and dispatcher stamps are retained.
- restart success: derived from tools_v15.restart_orchestrator_via_bat payload, including its previously omitted middle keys. Fixture paths are examples. No restart was performed. Error fixture is handler-derived.

x-audit is the existing schema activation marker with a canonical hash and measured fixture depth. Codo checked these shapes against the evidence above. Independent blind review of this implementation remains pending; this file does not certify such a review.
