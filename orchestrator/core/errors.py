"""Typed error taxonomy for [A] (ADR R3 §D3).

Replaces blanket ``except Exception`` blocks domain-by-domain during the
strangler migration. Every error carries the structured fields the CA contract
already exposes (``error_name``, HTTP ``status``, ``ca_next_action``) so the
response shaper can serialize without per-site string building.

Swallowing policy (HARD, ADR §D3):
  * MAY swallow (with a structured warning log): best-effort telemetry, cache
    delta after a successful primary op, optional dashboard metrics, temp-file
    cleanup.
  * MUST NOT swallow: write/backup/atomic-replace/auto-revert, token/origin/
    session guards, schema/manifest drift, command policy, council reply
    protocol, user-requested runtime actions.
"""

from __future__ import annotations


class OrchestratorError(Exception):
    """Base of all typed [A] errors."""
    error_name = "ORCHESTRATOR_ERROR"
    status = 500
    recoverable = False
    ca_next_action = "inspect_error"

    def __init__(self, message: str = "", **fields):
        super().__init__(message or self.error_name)
        self.message = message or self.error_name
        self.fields = fields

    def to_payload(self) -> dict:
        """Serialize to the standard CA error envelope (merged by the shaper)."""
        return {
            "ok": False,
            "error": self.message,
            "error_name": self.error_name,
            "recoverable": self.recoverable,
            "ca_next_action": self.ca_next_action,
            **self.fields,
        }


class UserInputError(OrchestratorError):
    error_name = "USER_INPUT_ERROR"; status = 400; recoverable = True
    ca_next_action = "fix_request_fields"


class ContractError(OrchestratorError):
    error_name = "CONTRACT_ERROR"; status = 400; recoverable = True
    ca_next_action = "read_live_schema"


class AuthError(OrchestratorError):
    error_name = "AUTH_ERROR"; status = 401; recoverable = True
    ca_next_action = "acquire_token"


class PermissionPolicyError(OrchestratorError):
    error_name = "PERMISSION_POLICY"; status = 403
    ca_next_action = "request_user_authorization"


class NotFoundError(OrchestratorError):
    error_name = "NOT_FOUND"; status = 404; recoverable = True
    ca_next_action = "verify_path_or_name"


class ConflictError(OrchestratorError):
    error_name = "CONFLICT"; status = 409; recoverable = True
    ca_next_action = "refresh_state_then_retry_once"


class ExternalRuntimeError(OrchestratorError):
    error_name = "EXTERNAL_RUNTIME_ERROR"; status = 502; recoverable = True
    ca_next_action = "inspect_runtime_target"


class InternalBugError(OrchestratorError):
    error_name = "INTERNAL_BUG"; status = 500
    ca_next_action = "report_bug_do_not_retry"


# ── Schema / contract ─────────────────────────────────────────────────────────
class SchemaViolation(ContractError):
    error_name = "SCHEMA_VIOLATION"


class UnsupportedField(SchemaViolation):
    error_name = "UNSUPPORTED_FIELD"; ca_next_action = "remove_unsupported_field"


class MissingRequiredField(SchemaViolation):
    error_name = "MISSING_REQUIRED_FIELD"; ca_next_action = "add_required_field"


class InvalidToolName(ContractError):
    error_name = "INVALID_TOOL_NAME"; ca_next_action = "list_tools_then_retry"


class ManifestDrift(InternalBugError):
    error_name = "MANIFEST_DRIFT"; ca_next_action = "run_audit_tool_matrix"


# ── Path / file ───────────────────────────────────────────────────────────────
class PathOutsideProject(PermissionPolicyError):
    error_name = "PATH_OUTSIDE_PROJECT"; ca_next_action = "use_project_relative_path"


class FileChangedOutsideOrchestrator(ConflictError):
    error_name = "FILE_CHANGED_OUTSIDE_ORCHESTRATOR"
    ca_next_action = "read_file_range_then_refresh_map"


class BinaryFileRejected(UserInputError):
    error_name = "BINARY_FILE_REJECTED"; ca_next_action = "use_binary_safe_tool"


class InvalidEncoding(UserInputError):
    error_name = "INVALID_ENCODING"; ca_next_action = "use_base64_encoding"


# ── Auth / guard ──────────────────────────────────────────────────────────────
class MutationTokenRequired(AuthError):
    error_name = "MUTATION_TOKEN_REQUIRED"


class OriginNotAllowed(PermissionPolicyError):
    error_name = "ORIGIN_BLOCKED"


class SessionRequired(ContractError):
    error_name = "SESSION_REQUIRED"; ca_next_action = "open_session_first"


# ── Write / cache ─────────────────────────────────────────────────────────────
class PatchApplyFailed(ConflictError):
    error_name = "PATCH_APPLY_FAILED"


class ValidationFailed(UserInputError):
    error_name = "VALIDATION_FAILED"; ca_next_action = "fix_then_revalidate"


class BackupFailed(InternalBugError):
    error_name = "BACKUP_FAILED"


class AtomicWriteFailed(InternalBugError):
    error_name = "ATOMIC_WRITE_FAILED"


# ── Command ───────────────────────────────────────────────────────────────────
class CommandPolicyViolation(PermissionPolicyError):
    error_name = "COMMAND_POLICY_VIOLATION"


class CommandTimeout(ExternalRuntimeError):
    error_name = "COMMAND_TIMEOUT"; ca_next_action = "raise_timeout_or_background"


# ── Runtime (T4) ──────────────────────────────────────────────────────────────
class CdpAttachFailed(ExternalRuntimeError):
    error_name = "CDP_ATTACH_FAILED"; ca_next_action = "launch_electron_with_cdp_first"


class DesktopWindowNotFound(ExternalRuntimeError):
    error_name = "DESKTOP_WINDOW_NOT_FOUND"; ca_next_action = "desktop_window_list_first"


class UipiBlocked(ExternalRuntimeError):
    error_name = "UIPI_BLOCKED"; recoverable = False
    ca_next_action = "do_not_retry_focus_use_file_bridge"


# ── Council ───────────────────────────────────────────────────────────────────
class InvalidAgent(UserInputError):
    error_name = "INVALID_AGENT"; ca_next_action = "use_roster_agent_id"


class AgentDisabled(UserInputError):
    error_name = "AGENT_DISABLED"; ca_next_action = "enable_in_council_config"


class CouncilModeNotSupported(UserInputError):
    error_name = "MODE_NOT_SUPPORTED"; ca_next_action = "use_broker_mode"


class WorkerTimeout(ExternalRuntimeError):
    error_name = "WORKER_TIMEOUT"; ca_next_action = "retry_once_or_split_task"


class WorkerUnavailable(ExternalRuntimeError):
    error_name = "AGENT_ERROR"; ca_next_action = "retry_once_then_report"


class BrokerAppendFailed(InternalBugError):
    error_name = "BROKER_APPEND_FAILED"


class ReplyParseFailed(InternalBugError):
    error_name = "REPLY_PARSE_FAILED"


# ── Storage ───────────────────────────────────────────────────────────────────
class JsonStoreCorrupt(InternalBugError):
    error_name = "JSON_STORE_CORRUPT"; ca_next_action = "restore_from_backup"


class SqliteUnavailable(ExternalRuntimeError):
    error_name = "SQLITE_UNAVAILABLE"
