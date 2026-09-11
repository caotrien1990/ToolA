import base64
import io
import json
import urllib.request

ROOT = r'D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator'


def call(tool, payload):
    req = urllib.request.Request(
        'http://localhost:8083/api/call/' + tool,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=180).read().decode('utf-8'))


body = io.open(r'.council\_r3_before\report_body.md', encoding='utf-8').read()
b64 = base64.b64encode(body.encode('utf-8')).decode('ascii')

for target in (r'.council\TOOLA_FIX_ARGVFALSE_R3.md',
               r'.council\worker_output\20260827_145425_clau_c46316c1.md'):
    res = call('write_new_file', {
        'path': target,
        'project_path': ROOT,
        'content': b64,
        'content_encoding': 'base64',
        'overwrite': True,
    })
    print(target, '| ok=', res.get('ok'), '| err=', res.get('error_name', ''),
          '| validation=', str(res.get('validation', ''))[:120])
