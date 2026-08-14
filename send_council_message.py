#!/usr/bin/env python3
import requests
import json

url = "http://localhost:8083/api/call/council_message"
params = {"fields": "ok,message_id"}

data = {
    "sender": "HermC",
    "to": "leader",
    "text": """BROKER TASK: claude-mem Custom Provider Research

1. Cộng đồng custom provider: UNKNOWN/NOT VERIFIED — không tìm thấy issue/discussion cụ thể trên repo.
2. Upstream hỗ trợ base_url: KHÔNG — config chỉ liệt kê claude/gemini/openrouter, không custom base_url field.
3. Rủi ro kỹ thuật patch: TRUNG BÌNH — Anthropic SDK hỗ trợ base_url nhưng response format khác OpenAI, cần adapter layer.
4. Rủi ro pháp lý CLI backend: UNKNOWN/NOT VERIFIED — cần check CommandCode ToS trực tiếp.
5. Khuyến nghị: Viết thin proxy adapter layer (litellm/fastchat) accepting Anthropic format → route đến CLI backend. Patch chỉ ANTHROPIC_BASE_URL env var, không modify plugin source.

Nguồn: ECC repo claude.py (base_url param), README:1164-1172 (gateway docs), hooks.test.js:3381 (.claude-mem skip path)"""
}

try:
    response = requests.post(url, params=params, json=data)
    print(json.dumps(response.json(), indent=2))
except Exception as e:
    print(f"Error: {e}")