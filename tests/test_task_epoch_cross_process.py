import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; SERVER=ROOT/"orchestrator_server.py"; PYTHON=os.environ.get("ORCHESTRATOR_TEST_PYTHON",sys.executable)
def free_port():
    with socket.socket() as s: s.bind(("127.0.0.1",0)); return s.getsockname()[1]

class MCP:
    def __init__(self,env):
        self.p=subprocess.Popen([PYTHON,str(SERVER),"--mcp"],cwd=ROOT,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding="utf-8",errors="replace",bufsize=1); self.i=0
        self.rpc("initialize",{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"epoch-test","version":"1"}})
        self.p.stdin.write(json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"})+chr(10)); self.p.stdin.flush()
    def rpc(self,method,params):
        self.i+=1; self.p.stdin.write(json.dumps({"jsonrpc":"2.0","id":self.i,"method":method,"params":params})+chr(10)); self.p.stdin.flush()
        while True:
            line=self.p.stdout.readline()
            if not line: raise AssertionError(self.p.stderr.read())
            r=json.loads(line)
            if r.get("id")==self.i:
                if "error" in r: raise AssertionError(r["error"])
                return r["result"]
    def tool(self,name,args):
        wire_name = name if name == "call_tool" else "call_tool"
        wire_args = args if name == "call_tool" else {"name": name, "payload": args}
        r=self.rpc("tools/call",{"name":wire_name,"arguments":wire_args}); text="".join(x.get("text","") for x in r.get("content",[]) if x.get("type")=="text"); value=json.loads(text); return json.loads(value) if isinstance(value,str) else value
    def close(self):
        if self.p.poll() is None: self.p.terminate(); self.p.wait(10)

class CrossProcessTaskEpochTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory(ignore_cleanup_errors=True); self.root=Path(self.t.name); self.db=self.root/"sessions.db"; self.sink=self.root/"sink"; self.sink.mkdir(); self.port=free_port(); self.env=os.environ.copy(); self.env.update(ORCHESTRATOR_PORT=str(self.port),ORCHESTRATOR_LIVEZ_PORT=str(free_port()),ORCHESTRATOR_BASE_URL=f"http://127.0.0.1:{self.port}",ORCHESTRATOR_SESSIONS_DB=str(self.db),ORCHESTRATOR_INSTANCE_LOCK=str(self.root/"orchestrator.lock"),PYTHONUNBUFFERED="1"); self.http=self.start_http(); self.mcp=MCP(self.env)
    def tearDown(self):
        if hasattr(self,"mcp"): self.mcp.close()
        if hasattr(self,"http"): self.stop_http()
        self.t.cleanup()
    def start_http(self):
        p=subprocess.Popen([PYTHON,str(SERVER)],cwd=ROOT,env=self.env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); end=time.time()+30
        while time.time()<end:
            if p.poll() is not None: raise AssertionError(f"HTTP child exited {p.returncode}")
            try:
                if urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/health",timeout=1).status==200:return p
            except OSError: time.sleep(.1)
        p.terminate(); raise AssertionError("HTTP child not ready")
    def stop_http(self):
        if self.http.poll() is None:self.http.terminate(); self.http.wait(10)
    def http_tool(self,name,payload,status=200):
        req=urllib.request.Request(f"http://127.0.0.1:{self.port}/api/call/{name}",data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"},method="POST")
        try:
            with urllib.request.urlopen(req,timeout=30) as r: actual=r.status; value=json.loads(r.read())
        except urllib.error.HTTPError as e: actual=e.code; value=json.loads(e.read())
        self.assertEqual(actual,status,value); return value
    def sink_payload(self,c,text): return {"session_id":"cross-process","agent_id":"owner","task_epoch":c["task_epoch"],"prompt_hash":c["prompt_hash"],"project_path":str(self.sink),"path":"epoch-sink.txt","new_content":text,"overwrite":True}

    def test_http_mcp_share_authoritative_epoch_and_restart(self):
        claim="claim-cross-process-0123456789abcdef0123456789abcdef"; boot=self.http_tool("bootstrap_session",{"session_id":"cross-process","agent_id":"owner","owner_claim_id":claim}); cap=boot["owner_capability"]
        one=self.mcp.tool("begin_task",{"session_id":"cross-process","prompt":"mcp epoch","agent_id":"owner","owner_capability":cap,"owner_claim_id":claim})
        self.assertNotEqual(self.http.pid,self.mcp.p.pid); self.assertEqual(one["prompt_hash"],hashlib.sha256(b"mcp epoch").hexdigest()); self.assertEqual(one["agent_id"],"owner")
        self.assertTrue(self.http_tool("write_new_file",self.sink_payload(one,"mcp-http"))["ok"]); target=self.sink/"epoch-sink.txt"; self.assertEqual(target.read_text(),"mcp-http")
        two=self.http_tool("begin_task",{"session_id":"cross-process","prompt":"http epoch","agent_id":"owner","owner_capability":cap}); self.assertEqual(two["task_epoch"],one["task_epoch"]+1)
        accepted=self.mcp.tool("call_tool",{"name":"write_new_file","payload":self.sink_payload(two,"http-mcp")}); self.assertTrue(accepted["ok"],accepted); self.assertEqual(target.read_text(),"http-mcp")
        stale=self.http_tool("write_new_file",self.sink_payload(one,"stale-http"),409); self.assertEqual(stale["error_name"],"STALE_TASK_EPOCH"); self.assertEqual(target.read_text(),"http-mcp")
        stale=self.mcp.tool("call_tool",{"name":"write_new_file","payload":self.sink_payload(one,"stale-mcp")}); self.assertEqual(stale["error_name"],"STALE_TASK_EPOCH"); self.assertEqual(target.read_text(),"http-mcp")
        hc=self.http_tool("get_task_checkpoint",{"session_id":"cross-process"}); mc=self.mcp.tool("get_task_checkpoint",{"session_id":"cross-process"})
        for field in ("task_epoch","prompt_hash","agent_id"): self.assertEqual(hc[field],mc[field])
        def h(i): return self.http_tool("begin_task",{"session_id":"cross-process","prompt":f"h{i}","agent_id":"owner","owner_capability":cap})
        def m(): return [self.mcp.tool("begin_task",{"session_id":"cross-process","prompt":f"m{i}","agent_id":"owner","owner_capability":cap}) for i in range(4)]
        with ThreadPoolExecutor(max_workers=5) as pool:
            hf=[pool.submit(h,i) for i in range(4)]; mf=pool.submit(m); rows=[x.result() for x in hf]+mf.result()
        epochs=sorted(x["task_epoch"] for x in rows); self.assertEqual(epochs,list(range(3,11)))
        with sqlite3.connect(self.db) as db: stored=db.execute("SELECT payload FROM task_checkpoints WHERE session_key=?",("cross-process",)).fetchall()
        self.assertEqual(len(stored),1); persisted=json.loads(stored[0][0]); self.assertEqual(persisted["task_epoch"],10)
        old=self.http.pid; self.stop_http(); self.http=self.start_http(); self.assertNotEqual(old,self.http.pid); recovered=self.http_tool("get_task_checkpoint",{"session_id":"cross-process"}); self.assertEqual((recovered["task_epoch"],recovered["prompt_hash"],recovered["agent_id"]),(10,persisted["prompt_hash"],"owner"))
