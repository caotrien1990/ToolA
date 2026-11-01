"""Admission and recovery must report storage failure instead of inventing absence."""
import orchestrator_server as server
from orchestrator.server.audit_writer import AuditStorageBusy
from orchestrator.storage.spool import read_spool


def test_cf17_constructor_busy_parks_row_with_explicit_ack(tmp_path, monkeypatch):
    db = tmp_path / 'audit.db'
    monkeypatch.setattr(server, '_durable_audit_db_path', lambda: db)
    def unavailable(**kwargs):
        raise AuditStorageBusy('schema locked')
    monkeypatch.setattr(server, '_get_durable_audit_writer', unavailable)
    ack = server._stage_durable_session_event(
        'cf17-id', 'write_fix', {}, {'ok': True}, 1, 'ok', '', {},
        admitted_session_id='session-cf17')
    assert ack.state == 'spooled' and ack.durable and not ack.applied
    rows, _ = read_spool(db.with_name('audit_spool.jsonl'))
    assert rows[0]['payload']['session_id'] == 'session-cf17'


def test_cf13_gate_uses_shared_contention_contract():
    import json
    from orchestrator.session.store import SessionStoreContentionError
    response = server._session_gate_storage_error('write_fix', SessionStoreContentionError('busy'), {}, {})
    body = json.loads(response.text)
    assert response.status == 503 and response.headers['Retry-After'] == '1'
    assert body['error_name'] == 'SESSION_STORE_CONTENTION' and body['retryable']
    response = server._session_gate_storage_error('write_fix', ValueError('broken'), {}, {})
    assert response.status == 500


def test_cf10_pending_commit_never_becomes_safe_to_resend(monkeypatch):
    import json
    import code_tools
    from orchestrator.session.store import SessionStoreCommitPendingError
    exc = SessionStoreCommitPendingError('still running')
    payload = json.loads(code_tools._json_error(exc))
    assert payload['commit_state'] == 'unknown' and not payload['safe_to_retry']
    assert server._session_contention_error_payload('write_fix', exc, {})['commit_state'] == 'unknown'
    rec = {'status': 'failed', 'result': payload}
    assert server._write_ledger_commit_state(rec) == 'unknown'


def test_cf13_real_dispatch_lookup_contention_is_503(tmp_path, monkeypatch):
    from tests.test_dispatch_identity_field_type_gate import _post
    from orchestrator.session.store import SessionStoreContentionError
    def locked(*a, **kw):
        raise SessionStoreContentionError('read contention')
    monkeypatch.setattr(server, '_lookup_caller_session', locked)
    response, result = _post('rename_session', {'project_path': str(tmp_path), 'new_title': 'CF13'})
    assert response.status == 503, result
    assert result['error_name'] == 'SESSION_STORE_CONTENTION'
    assert response.headers['Retry-After'] == '1'


def test_cf13_real_dispatch_auto_open_contention_is_503(tmp_path, monkeypatch):
    from tests.test_dispatch_identity_field_type_gate import _post
    from orchestrator.session.store import SessionStoreContentionError
    monkeypatch.setattr(server, '_lookup_caller_session', lambda *a: (None, str(tmp_path), None))
    monkeypatch.setattr(server, 'AUTO_WRITE_MODE', True)
    def locked(*a, **kw):
        raise SessionStoreContentionError('open contention')
    monkeypatch.setattr(server.ct, '_auto_open_session', locked)
    response, result = _post('write_new_file', {'project_path': str(tmp_path), 'path': 'x.py', 'content': 'x=1'})
    assert response.status == 503, result
    assert result['error_name'] == 'SESSION_STORE_CONTENTION'
    assert not (tmp_path / 'x.py').exists()
