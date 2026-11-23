# Owned page release

Use only the page_id recorded from your own launch or attachment. close_browser_page closes a launched page and detaches an attached page. Current attachment state takes precedence. A listed CDP target is not proof of page ownership.

The registry exposes list_cdp_targets for browser targets; it does not enumerate the caller’s ToolA page IDs. If the original page_id is missing, stop and report it. Pass the held page_id to close_browser_page. Do not call cleanup_test_session. Code inspection shows CLEANUP_SCOPE_UNSUPPORTED for processes or close_session_after and MISSING_REQUIRED_FIELD without page_id. A K-5 live test requires a separate owner decision. Never terminate desktop-wide Electron processes to release a page.
