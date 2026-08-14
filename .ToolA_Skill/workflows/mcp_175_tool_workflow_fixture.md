# mcp_175_tool_workflow_fixture

## Step 1
### Create baseline evidence
Do: call `workflow_start(name="mcp_175_tool_workflow_fixture")`, then call `workflow_checkpoint(workflow_name="mcp_175_tool_workflow_fixture", label="after_start", expected_state="current_step=1")`.

Evidence: `workflow_start.ok=true`, `current_step=1`, checkpoint has `drift_detected=false` or structured warnings only.

## Step 2
### Submit with branch
Do: submit this step with `workflow_submit_step(name="mcp_175_tool_workflow_fixture", step=2, status="done", evidence="branch to step 4", next_step=4)`.

Evidence: result advances to `current_step=4`, not step 3.

## Step 3
### Branch target intentionally skipped
This step should only be reached after `workflow_goto_step` or `workflow_reset`. If reached naturally after Step 2, record BUG: branch failed.

Evidence: if manually visited, submit done with `evidence="manual visit for goto/skip coverage"`.

## Step 4
### Fail, retry, and skip path
Do: submit fail for current step: `workflow_submit_step(name="mcp_175_tool_workflow_fixture", step=4, status="fail", evidence="intentional fail for retry test")`. Then call `workflow_retry_step(name="mcp_175_tool_workflow_fixture")`. Then call `workflow_skip_step(name="mcp_175_tool_workflow_fixture", reason="intentional skip after retry coverage")`.

Evidence: fail pauses workflow, retry clears paused state, skip advances to step 5.

## Step 5
### Reset and goto coverage
Do: call `workflow_goto_step(name="mcp_175_tool_workflow_fixture", target_step=3, reason="goto coverage")`, verify current step 3, then call `workflow_reset(name="mcp_175_tool_workflow_fixture")`.

Evidence: goto logs jump to 3; reset returns current_step=1. Terminal cleanup: do not leave this workflow active for production use.
