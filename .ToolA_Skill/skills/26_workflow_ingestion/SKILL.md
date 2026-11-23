---
version: "1.0.0"
last_verified: "2026-11-22"
verified_head: "dd41e2fc8f78761f8f7b9a104fb5cd98fdf1cad0"
name: workflow_ingestion
description: |
  Run a user-provided workflow by its registered name. Check availability before starting.
trigger:
  - task_pattern: "chạy workflow | run workflow | execute workflow | workflow X | testgf | test4_manual"
  - tool: "workflow_list | workflow_start | workflow_get_current | workflow_submit_step | workflow_retry_step | workflow_skip_step | workflow_status | workflow_create_from_ui"
  - file_pattern: "*.md trong workflows/"
preconditions:
  - "open_session done"
  - "Workflow MD tồn tại ở <project>/.orchestrator_cache/workflows/ HOẶC <[A]>/.ToolA_Skill/workflows/"
related_skills:
  - 18_runtime_dom_T4
  - 22_electron_automation
risk_level: medium
---

# Run a registered workflow

Use workflow_list for the active project. If the requested workflow exists, use workflow_start and workflow_get_current. Follow the returned current step and record its actual result through workflow_submit_step. If the name is absent, report it and request the workflow definition.

The regression fixture is self-provisioning. tests/test_workflow_current_step_canonical_e01.py defines WORKFLOW_NAME and creates WORKFLOW_NAME + ".md" in its temporary directory. Preserve that constant and the temporary setup. The test does not require a permanent global workflow.
