import json, base64, pathlib
src = pathlib.Path(r'D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\tmp_comc1_verify.md').read_text(encoding='utf-8')
b64 = base64.b64encode(src.encode('utf-8')).decode('ascii')
body = {
    "project_path": r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\21. ToolQ",
    "path": r"staging\q-hardening\task0_d_migration\COMC1_FIX_VERIFY.md",
    "new_content": b64,
    "content_encoding": "base64",
    "overwrite": True,
    "dry_run": True,
}
out = pathlib.Path(r'D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\req.json')
out.write_text(json.dumps(body), encoding='ascii')
print('wrote', out, 'len', out.stat().st_size)
