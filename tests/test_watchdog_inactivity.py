import importlib
import json
import threading
import time

from orchestrator.server.executor_lanes import ExecutorLane


def test_oldest_active_age_grows_then_resets(monkeypatch):
    lane = ExecutorLane("age", max_workers=1, max_queue=0)
    gate = threading.Event()
    clock = {"now": 10.0}
    monkeypatch.setattr("orchestrator.server.executor_lanes.time.monotonic", lambda: clock["now"])
    future = lane.submit(lambda: gate.wait())
    for _ in range(100):
        if lane.status()["active"]:
            break
        time.sleep(0.001)
    clock["now"] = 12.5
    assert lane.status()["oldest_active_age_ms"] == 2500.0
    gate.set(); future.result(timeout=2)
    assert lane.status()["oldest_active_age_ms"] == 0.0
    lane._executor.shutdown(wait=True)


def _health(**kw):
    d={"ok":True,"service":"project-orchestrator","generation":"g1","executor_accepting":True,"executor_active_total":1,"executor_queued_total":0,"executor_oldest_active_age_ms":0.0}
    d.update(kw); a=d["executor_active_total"]; q=d["executor_queued_total"]
    d["executor_lanes"]=[{"name":"test","active":a,"queued":q,"capacity":d.get("capacity",2),"accepting":d["executor_accepting"],"completed":d.get("completed",0),"failed":d.get("failed",0),"rejected":d.get("rejected",0),"oldest_wait_ms":d.get("oldest_wait_ms",0.0),"oldest_active_age_ms":d["executor_oldest_active_age_ms"]}]
    return d


def _run(monkeypatch,tmp_path,samples,now=100.0,fp=None):
    import watchdog_a
    wd=importlib.reload(watchdog_a); monkeypatch.setattr(wd,"STATE",str(tmp_path/"state.json")); monkeypatch.setattr(wd,"LOG",str(tmp_path/"watchdog.log")); monkeypatch.setattr(wd,"INACTIVITY_WINDOW_SEC",10.0); monkeypatch.setattr(wd,"FAILURE_INTERVAL_SEC",0); monkeypatch.setattr(wd.time,"time",lambda:now)
    if fp is not None: monkeypatch.setattr(wd,"_progress_fingerprint",lambda p:fp)
    it=iter(samples); monkeypatch.setattr(wd,"probe",lambda:next(it)); calls={"restart":0}
    class R: returncode=0; stdout=""; stderr=""
    def run(*a,**k): calls["restart"]+=1; return R()
    monkeypatch.setattr(wd.subprocess,"run",run); rc=wd.main(); p=tmp_path/"state.json"; l=tmp_path/"watchdog.log"
    return rc,calls,json.loads(p.read_text()) if p.exists() else {},l.read_text() if l.exists() else ""


def test_busy_inactive_warns_without_restart(monkeypatch,tmp_path):
    s=("healthy","g1","livez_v1",_health(executor_oldest_active_age_ms=20000),True)
    _,_,state,_=_run(monkeypatch,tmp_path,[s]); state["last_progress_at"]=80; (tmp_path/"state.json").write_text(json.dumps(state))
    rc,calls,state,text=_run(monkeypatch,tmp_path,[s]); assert rc==0 and calls["restart"]==0; assert state["inactive_samples"]==1; assert "WATCHDOG_INACTIVITY" in text and "oldest_active_age_ms" in text


def test_queue_full_livez_healthy_degraded_no_restart(monkeypatch,tmp_path):
    h=_health(executor_queued_total=1,capacity=2,oldest_wait_ms=40000); s=("degraded","g1","executor_saturated",h,True)
    rc,calls,state,_=_run(monkeypatch,tmp_path,[s]); assert rc==0 and calls["restart"]==0 and state["last_classification"]=="degraded"


def test_three_inactive_livez_healthy_never_restart(monkeypatch,tmp_path):
    s=("healthy","g1","livez_v1",_health(executor_oldest_active_age_ms=50000),True); fp="same"
    (tmp_path/"state.json").write_text(json.dumps({"generation":"g1","last_fp":fp,"last_progress_at":0,"inactive_samples":2}))
    rc,calls,state,_=_run(monkeypatch,tmp_path,[s],fp=fp); assert rc==0 and calls["restart"]==0 and state["inactive_samples"]>=3


def test_three_inactive_down_samples_restart(monkeypatch,tmp_path):
    s=("down","g1","livez_failed;health_not_ready",_health(executor_oldest_active_age_ms=50000),False); fp="same"
    (tmp_path/"state.json").write_text(json.dumps({"generation":"g1","last_fp":fp,"last_progress_at":0,"inactive_samples":2}))
    rc,calls,state,_=_run(monkeypatch,tmp_path,[s,s,s],fp=fp); assert rc==1 and calls["restart"]==1 and state["last_classification"]=="down"


def test_completed_failed_change_resets_progress(monkeypatch,tmp_path):
    _,_,_,_=_run(monkeypatch,tmp_path,[("healthy","g1","livez_v1",_health(completed=1),True)],100)
    _,_,state,_=_run(monkeypatch,tmp_path,[("healthy","g1","livez_v1",_health(completed=2,failed=1),True)],120)
    assert state["last_progress_at"]==120 and state["inactive_samples"]==0