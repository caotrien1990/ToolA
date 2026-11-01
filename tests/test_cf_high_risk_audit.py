"""Audit recovery must preserve evidence without serializing request admission."""
import threading

from orchestrator.server.audit_writer import DurableAuditWriter, SESSION_EVENT_KIND
from orchestrator.storage.spool import read_spool


def test_cf11_busy_stage_does_not_hold_lifecycle_condition(tmp_path, monkeypatch):
    writer = DurableAuditWriter(tmp_path / 'audit.db', lambda row: None, auto_start=False)
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    original = writer._stage_transaction
    def slow(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)
    monkeypatch.setattr(writer, '_stage_transaction', slow)
    thread = threading.Thread(target=lambda: writer.stage('busy', SESSION_EVENT_KIND, {}))
    thread.start()
    assert entered.wait(1)
    closer = threading.Thread(target=lambda: (writer.close_admission(), closed.set()))
    closer.start()
    try:
        assert closed.wait(.15), 'SQLite retry must not block close_admission'
    finally:
        release.set()
        thread.join(3)
        closer.join(3)
        writer.shutdown(drain=False)


def test_cf20_start_does_not_replay_on_calling_thread(tmp_path, monkeypatch):
    writer = DurableAuditWriter(tmp_path / 'audit.db', lambda row: None, auto_start=False)
    caller = threading.get_ident()
    threads = []
    replayed = threading.Event()
    def replay():
        threads.append(threading.get_ident())
        replayed.set()
        return {'replayed': 0, 'kept': 0, 'corrupt': 0}
    monkeypatch.setattr(writer, 'replay_spool', replay)
    writer.start()
    try:
        assert replayed.wait(1)
        assert threads and caller not in threads
    finally:
        writer.shutdown()


def test_cf20_conflicting_replay_keeps_payload_in_quarantine(tmp_path):
    writer = DurableAuditWriter(tmp_path / 'audit.db', lambda row: None, auto_start=False)
    writer.stage('same', SESSION_EVENT_KIND, {'value': 1})
    assert writer.spool_failed_row('same', SESSION_EVENT_KIND, {'value': 2}, 'busy')
    result = writer.replay_spool()
    assert result['replayed'] == 1
    quarantined, _ = read_spool(writer.spool_path.with_suffix('.quarantine.jsonl'))
    assert quarantined and quarantined[0]['payload'] == {'value': 2}
    writer.shutdown(drain=False)


def test_cf11_idle_wait_accounts_for_in_flight_stage(tmp_path, monkeypatch):
    writer = DurableAuditWriter(tmp_path / 'audit.db', lambda row: None, auto_start=False)
    entered, release = threading.Event(), threading.Event()
    original = writer._stage_transaction
    def delayed(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)
    monkeypatch.setattr(writer, '_stage_transaction', delayed)
    thread = threading.Thread(target=lambda: writer.stage('late', SESSION_EVENT_KIND, {}))
    thread.start()
    try:
        assert entered.wait(1)
        assert not writer.wait_until_idle(.05)
    finally:
        release.set()
        thread.join(3)
        writer.shutdown(drain=False)


def test_cf20_transient_replay_refusal_stops_batch(tmp_path, monkeypatch):
    from orchestrator.server.audit_writer import AuditStorageBusy
    writer = DurableAuditWriter(tmp_path / 'audit.db', lambda row: None, auto_start=False)
    for i in range(4):
        assert writer.spool_failed_row(str(i), SESSION_EVENT_KIND, {}, 'busy')
    calls = []
    def busy(*a, **kw):
        calls.append(a)
        raise AuditStorageBusy('busy')
    monkeypatch.setattr(writer, 'stage', busy)
    result = writer.replay_spool()
    assert len(calls) == 1 and result['kept'] == 4
    writer.shutdown(drain=False)
