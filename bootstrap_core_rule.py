#!/usr/bin/env python3
import requests
import json

url = "http://localhost:8083/api/call/get_rule_core_text"
params = {"fields": "ok,already_fetched,text,sha256"}
data = {"agent_id": "hermes"}

try:
    response = requests.post(url, params=params, json=data)
    result = response.json()
    print(json.dumps(result, indent=2))
except Exception as e:
    print(f"Error: {e}")
