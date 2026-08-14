#!/usr/bin/env python3
"""Bootstrap script to load core rule from Project-Orchestrator [A]"""
import requests
import json

def load_core_rule():
    try:
        response = requests.post(
            "http://localhost:8083/api/call/get_rule_core_text",
            json={"agent_id": "hermes"},
        )
        if response.status_code == 200:
            data = response.json()
            if data.get("ok"):
                if data.get("already_fetched"):
                    print("Core rule already loaded in context")
                else:
                    print(f"Core rule loaded (SHA256: {data.get('sha256', 'unknown')[:8]}...)")
                    # Store the rule text for reference
                    with open("core_rule_loaded.md", "w") as f:
                        f.write(data.get("text", ""))
                return True
            else:
                print(f"Failed to load core rule: {data}")
        else:
            print(f"HTTP error: {response.status_code} {response.text}")
    except Exception as e:
        print(f"Connection error: {e}")
    return False

if __name__ == "__main__":
    print("Loading core rule from Project-Orchestrator [A]...")
    if load_core_rule():
        print("Bootstrap complete. Ready for BROKER TASK.")
    else:
        print("Bootstrap failed. Check if [A] server is running.")