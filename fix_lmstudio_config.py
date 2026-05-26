#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Полностью перезаписывает mcp.json, создавая один унифицированный сервер mcp_fs_server.py.
"""
import json
from pathlib import Path

def generate_correct_config():
    script_dir = Path(__file__).parent
    venv_python = script_dir / ".venv" / "Scripts" / "python.exe"
    server_script = script_dir / "mcp_fs_server.py"
    
    if not venv_python.exists():
        raise FileNotFoundError(f"Python not found: {venv_python}")
    if not server_script.exists():
        raise FileNotFoundError(f"Server script not found: {server_script}")
    
    python_path = str(venv_python).replace("\\", "\\\\")
    server_path = str(server_script).replace("\\", "\\\\")
    
    config = {
        "mcpServers": {
            "mcp_unified": {
                "command": python_path,
                "args": [server_path],
                "env": {
                    "PYTHONIOENCODING": "utf-8",
                    "MCP_MEMORY_PATH": str(script_dir / "mcp_memory.db").replace("\\", "\\\\"),
                    "MCP_MEMORY_MAX_ENTRIES": "1000000",
                    "MCP_MEMORY_TTL_DAYS": "90",
                    "MCP_AUTO_MEMORY": "true",
                    "EVENT_BUS_URL": "http://localhost:8080/publish",
                    "LLM_ENDPOINT": "http://localhost:1234/v1/chat/completions"
                }
            }
        }
    }
    return config

def fix_lmstudio_config():
    lm_config = Path.home() / ".lmstudio" / "mcp.json"
    
    if not lm_config.parent.exists():
        print(f"[ERROR] .lmstudio folder not found: {lm_config.parent}")
        return 1
    
    new_config = generate_correct_config()
    
    # Резервная копия старого файла
    if lm_config.exists():
        backup = lm_config.with_suffix(".json.backup")
        try:
            with open(lm_config, "r", encoding="utf-8") as f:
                old = json.load(f)
            with open(backup, "w", encoding="utf-8") as f:
                json.dump(old, f, indent=2, ensure_ascii=False)
            print(f"[OK] Backup created: {backup}")
        except Exception as e:
            print(f"[WARN] Could not backup: {e}")
    
    # Запись правильной конфигурации
    try:
        with open(lm_config, "w", encoding="utf-8") as f:
            json.dump(new_config, f, indent=2, ensure_ascii=False)
        print("[SUCCESS] LM Studio config replaced with unified server.")
        print("   Only 'mcp_unified' server remains.")
        print("   Please restart LM Studio.")
        return 0
    except Exception as e:
        print(f"[ERROR] Cannot write config: {e}")
        return 1

if __name__ == "__main__":
    exit(fix_lmstudio_config())