# Fair admission ahead of executor lanes.
from __future__ import annotations
import asyncio, contextvars, os, threading, time, uuid
from dataclasses import dataclass

_PRINCIPAL=contextvars.ContextVar('quota_principal',default='anonymous:localhost')
_CONTROL=contextvars.ContextVar('quota_control',default=False)
def set_admission_identity(principal_id:str,*,control_plane:bool=False):
    return _PRINCIPAL.set(principal_id or 'anonymous:localhost'),_CONTROL.set(control_plane)
def reset_admission_identity(tokens):
    _CONTROL.reset(tokens[1]);_PRINCIPAL.reset(tokens[0])
def _enabled(name,default=True):
    return str(os.environ.get(name,'1' if default else '0')).lower() not in {'0','false','off','no'}
def _env_int(name,default):
    try:return max(1,int(os.environ.get(name,default)))
    except (TypeError,ValueError):return default
# Per-lane default fair quotas (per-principal caps; unlisted lanes default to 1).
# council_provider MUST stay in sync with two other files:
#   executor_lanes._LANE_DEFAULTS['council_provider'][0]
#   lifecycle/high_il_launch.HIGH_IL_ENV['ORCH_FAIR_COUNCIL_PROVIDER_QUOTA']
_LANE_QUOTA_DEFAULTS={"read_io":2,"search":2,"command":1,"test":1,
                      "git":1,"backup":1,"council_provider":8}

class FairAdmissionError(RuntimeError):
    error_name='FAIR_ADMISSION_REJECTED';rejection_reason='principal_quota';http_status=429
    def __init__(self,lane,message,metrics,retry_after):
        super().__init__(message);self.lane=lane;self.status=metrics;self.retry_after=max(1,int(retry_after+0.999))
    def as_payload(self):
        return {'ok':False,'error':str(self),'reason':self.rejection_reason,'error_name':self.error_name,
                'lane':self.lane,'metrics':self.status,'retryable':True,'retry_after_seconds':self.retry_after,
                'ca_next_action':'wait_for_retry_after_then_retry_with_backoff; do_not_retry_immediately_or_unchanged'}
class PrincipalQuotaExceeded(FairAdmissionError):
    error_name='PRINCIPAL_QUOTA_EXCEEDED';rejection_reason='principal_quota'
class PrincipalWaiterLimit(FairAdmissionError):
    error_name='PRINCIPAL_WAITER_LIMIT';rejection_reason='waiter_limit'
class GlobalAdmissionExceeded(FairAdmissionError):
    error_name='GLOBAL_ADMISSION_CAP_EXCEEDED';rejection_reason='global_cap';http_status=503

@dataclass
class _State:
    active:int=0;waiting:int=0;last_used:float=0.0
class AdmissionPermit:
    def __init__(self,manager,token,lane,principal,tracked):
        self.manager=manager;self.token=token;self.lane=lane;self.principal=principal;self.tracked=tracked
        self.released=False;self.lock=threading.Lock()
    def release(self):
        with self.lock:
            if self.released:return False
            self.released=True
        self.manager._release(self);return True

class FairAdmissionManager:
    def __init__(self):
        self.lock=threading.RLock();self.principals={};self.permits={};self.global_admitted=0;self.high_watermark=0
        self.rejections={'principal_quota':0,'waiter_limit':0,'global_cap':0};self.closed=False
    def quota(self,lane,principal):
        default=_LANE_QUOTA_DEFAULTS.get(lane,1)
        quota=_env_int(f'ORCH_FAIR_{lane.upper()}_QUOTA',default)
        if principal.startswith('anonymous:'):
            anonymous_default=_env_int('ORCH_FAIR_ANONYMOUS_QUOTA',8)
            return _env_int(f'ORCH_FAIR_ANONYMOUS_{lane.upper()}_QUOTA',min(quota,anonymous_default))
        return quota
    def max_waiters(self,lane,principal):
        default=64 if principal.startswith('anonymous:') else 16
        shared=_env_int('ORCH_FAIR_MAX_WAITERS_PER_PRINCIPAL',default)
        return _env_int(f'ORCH_FAIR_{lane.upper()}_MAX_WAITERS',shared)
    def global_cap(self):return _env_int('ORCH_GLOBAL_CAP',32)
    def _status(self):
        return {'fair_enforcement_enabled':_enabled('ORCH_FAIR_ADMISSION_ENABLED'),
          'global_enforcement_enabled':_enabled('ORCH_GLOBAL_CAP_ENABLED'),'global_cap':self.global_cap(),
          'global_admitted':self.global_admitted,'global_available':max(0,self.global_cap()-self.global_admitted),
          'permit_count':len(self.permits),'high_watermark':self.high_watermark,'principal_cardinality':len(self.principals),
          'anonymous_quota_default':_env_int('ORCH_FAIR_ANONYMOUS_QUOTA',8),
          'principal_waiter_limit_default':_env_int('ORCH_FAIR_MAX_WAITERS_PER_PRINCIPAL',16),
          'principal_active':{f'{p}|{l}':s.active for (p,l),s in self.principals.items() if s.active},
          'principal_waiting':{f'{p}|{l}':s.waiting for (p,l),s in self.principals.items() if s.waiting},
          'rejection_counts':dict(self.rejections),'closed':self.closed}
    def status(self):
        with self.lock:return self._status()
    # F-C3: evict_idle() removed -- cardinality is bounded by agent IDs x 7 lanes,
    # so unbounded growth is not a concern. No sweeper needed (YAGNI).
    # (Former method evicted idle principals; no production callers existed.)
    async def acquire(self,lane,*,deadline,principal=None,control_plane=None):
        principal=str(principal or _PRINCIPAL.get() or "anonymous:localhost")
        control_plane=_CONTROL.get() if control_plane is None else control_plane
        if control_plane:return AdmissionPermit(self,uuid.uuid4().hex,lane,principal,False)
        key=(principal,lane);waiter=False;held=False
        try:
            while True:
                now=time.monotonic()
                with self.lock:
                    state=self.principals.setdefault(key,_State(last_used=now))
                    if self.closed:raise GlobalAdmissionExceeded(lane,"fair admission is shutting down",self._status(),deadline-now)
                    if not _enabled("ORCH_FAIR_ADMISSION_ENABLED") or state.active<self.quota(lane,principal):
                        if waiter:state.waiting=max(0,state.waiting-1);waiter=False
                        state.active+=1;state.last_used=now;held=True;break
                    if not waiter:
                        max_waiters=self.max_waiters(lane,principal)
                        if state.waiting>=max_waiters:
                            self.rejections["waiter_limit"]+=1
                            raise PrincipalWaiterLimit(lane,f"principal waiter limit reached ({max_waiters}) for this lane",self._status(),deadline-now)
                        state.waiting+=1;waiter=True
                    if now>=deadline:
                        self.rejections["principal_quota"]+=1
                        raise PrincipalQuotaExceeded(lane,"principal quota wait budget expired",self._status(),1)
                await asyncio.sleep(min(.01,max(.001,deadline-now)))
            while True:
                now=time.monotonic()
                with self.lock:
                    if not _enabled("ORCH_GLOBAL_CAP_ENABLED") or self.global_admitted<self.global_cap():
                        self.global_admitted+=1;self.high_watermark=max(self.high_watermark,self.global_admitted)
                        permit=AdmissionPermit(self,uuid.uuid4().hex,lane,principal,True)
                        self.permits[permit.token]=permit;return permit
                    if now>=deadline:
                        self.rejections["global_cap"]+=1
                        raise GlobalAdmissionExceeded(lane,"global lane-work cap wait budget expired",self._status(),1)
                await asyncio.sleep(min(.01,max(.001,deadline-now)))
        except BaseException:
            with self.lock:
                state=self.principals.get(key)
                if state and waiter:state.waiting=max(0,state.waiting-1)
                if state and held:state.active=max(0,state.active-1)
                if state:state.last_used=time.monotonic()
            raise
    def _release(self,permit):
        if not permit.tracked:return
        with self.lock:
            if self.permits.pop(permit.token,None) is None:return
            state=self.principals.get((permit.principal,permit.lane))
            if state:state.active=max(0,state.active-1);state.last_used=time.monotonic()
            self.global_admitted=max(0,self.global_admitted-1)
    def close(self):
        with self.lock:self.closed=True
    def reset_for_tests(self):
        with self.lock:
            self.principals.clear();self.permits.clear();self.global_admitted=self.high_watermark=0
            self.rejections={"principal_quota":0,"waiter_limit":0,"global_cap":0};self.closed=False

FAIR_ADMISSION=FairAdmissionManager()
