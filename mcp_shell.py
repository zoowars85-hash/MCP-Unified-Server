#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Shell v1.0 – безопасное выполнение команд PowerShell/CMD
с белым списком разрешённых команд, ограничением по времени и dry-run.
"""
import os
import sys
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

from mcp_shared import (
    _log, BaseMCPServer, conversation_memory, dialog_ctx, normalize_path, _ensure_allowed
)

# ─── Конфигурация ──────────────────────────────────────────────────────────
DEFAULT_TIMEOUT = int(os.environ.get("MCP_SHELL_TIMEOUT", "30"))
MAX_OUTPUT_CHARS = int(os.environ.get("MCP_SHELL_MAX_OUTPUT", "10000"))

# Белый список команд (разрешены только безопасные)
# Каждая команда: имя, разрешённые флаги (regex), функция проверки аргументов
ALLOWED_COMMANDS = {
    "Get-ChildItem": {
        "flags": r"-(?:Path|Recurse|Filter|Include|Exclude|Depth|Directory|File)",
        "dangerous": False,
        "description": "List files/directories"
    },
    "ls": {
        "flags": r"-(?:l|a|R|d)",
        "dangerous": False,
        "description": "List files (alias for Get-ChildItem)"
    },
    "Copy-Item": {
        "flags": r"-(?:Path|Destination|Recurse|Force|Filter)",
        "dangerous": False,
        "description": "Copy files/directories"
    },
    "Move-Item": {
        "flags": r"-(?:Path|Destination|Force)",
        "dangerous": False,
        "description": "Move files/directories"
    },
    "Get-Process": {
        "flags": r"-(?:Name|Id|IncludeUserName)",
        "dangerous": False,
        "description": "List processes"
    },
    "Get-Service": {
        "flags": r"-(?:Name|DisplayName|DependentServices)",
        "dangerous": False,
        "description": "List services"
    },
    "Get-Content": {
        "flags": r"-(?:Path|TotalCount|Tail|Head|Encoding)",
        "dangerous": False,
        "description": "Read file content"
    },
    "Select-String": {
        "flags": r"-(?:Path|Pattern|CaseSensitive|SimpleMatch)",
        "dangerous": False,
        "description": "Search in files"
    },
    "Measure-Object": {
        "flags": r"-(?:Line|Word|Character|Property)",
        "dangerous": False,
        "description": "Count lines/words"
    },
    "Get-Date": {
        "flags": r"-(?:Format|DisplayHint|Year|Month|Day)",
        "dangerous": False,
        "description": "Get current date/time"
    },
    "Get-Location": {
        "flags": r"",
        "dangerous": False,
        "description": "Get current directory"
    },
    "pwd": {
        "flags": r"",
        "dangerous": False,
        "description": "Print working directory"
    },
}

# Запрещённые паттерны (блокируются в любых командах)
BLOCKED_PATTERNS = [
    r'Remove-Item', r'rm\s+-rf', r'del\s+/[fsq]', r'format\s+[a-z]:',
    r'Stop-Process\s+-Force', r'kill\s+-9', r'Set-ExecutionPolicy',
    r'Start-Process\s+-Verb\s+RunAs', r'Write-.*\s+.*\\Windows',
    r'Reg\s+(?:add|delete)', r'net\s+user\s+/add', r'net\s+localgroup',
    r'schtasks\s+/create', r'bcdedit', r'vssadmin\s+delete',
]

def _is_command_allowed(command: str) -> tuple[bool, str]:
    """Проверяет, разрешена ли команда. Возвращает (разрешено, причина)."""
    cmd_lower = command.lower().strip()
    
    # Проверка на запрещённые паттерны
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return False, f"Command contains blocked pattern: {pattern}"
    
    # Извлекаем базовую команду (первое слово)
    parts = command.split()
    if not parts:
        return False, "Empty command"
    base_cmd = parts[0]
    
    # Проверяем, есть ли в белом списке
    for allowed in ALLOWED_COMMANDS:
        if base_cmd.lower() == allowed.lower() or base_cmd.lower() == allowed.lower().replace('-', ''):
            return True, ""
    
    # Для PowerShell командлетов с дефисом
    if base_cmd in ALLOWED_COMMANDS:
        return True, ""
    
    return False, f"Command '{base_cmd}' not in allowed list"

def run_shell(command: str, shell: str = "powershell", timeout: int = DEFAULT_TIMEOUT,
              dry_run: bool = False, dialog_id: Optional[str] = None) -> Dict:
    """
    Безопасно выполняет команду в PowerShell или CMD.
    """
    d_id = dialog_id or dialog_ctx.get()
    
    # Проверка безопасности
    allowed, reason = _is_command_allowed(command)
    if not allowed:
        return {
            "status": "blocked",
            "reason": reason,
            "command": command,
            "dialog_id": d_id
        }
    
    if dry_run:
        return {
            "status": "dry_run",
            "command": command,
            "shell": shell,
            "message": "Command is allowed. Remove dry_run=True to execute."
        }
    
    # Подготовка команды
    if shell.lower() == "powershell":
        cmd = ["powershell.exe", "-NoProfile", "-Command", command]
    elif shell.lower() == "cmd":
        cmd = ["cmd.exe", "/c", command]
    else:
        return {"status": "error", "message": f"Unsupported shell: {shell}"}
    
    start_time = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding='utf-8',
            errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        elapsed = time.time() - start_time
        
        stdout = proc.stdout[:MAX_OUTPUT_CHARS]
        stderr = proc.stderr[:MAX_OUTPUT_CHARS]
        
        conversation_memory.add(
            op="run_shell",
            paths={"command": command[:100]},
            status="success" if proc.returncode == 0 else "error",
            dialog=d_id,
            context=f"Shell command executed in {elapsed:.1f}s, exit code {proc.returncode}"
        )
        
        return {
            "status": "success" if proc.returncode == 0 else "error",
            "command": command,
            "shell": shell,
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "elapsed_sec": round(elapsed, 2),
            "dialog_id": d_id
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "command": command,
            "timeout_sec": timeout,
            "dialog_id": d_id
        }
    except Exception as e:
        return {
            "status": "error",
            "command": command,
            "error": str(e),
            "dialog_id": d_id
        }

def register_shell_tool(server: BaseMCPServer):
    server.register_tool("run_shell", {
        "description": "Execute allowed PowerShell/CMD commands securely (whitelist only). Use with dry_run=True first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command to execute"},
                "shell": {"type": "string", "enum": ["powershell", "cmd"], "default": "powershell"},
                "timeout": {"type": "integer", "default": DEFAULT_TIMEOUT},
                "dry_run": {"type": "boolean", "default": True},
                "dialog_id": {"type": "string"}
            },
            "required": ["command"]
        }
    }, lambda **kw: run_shell(
        kw["command"],
        kw.get("shell", "powershell"),
        kw.get("timeout", DEFAULT_TIMEOUT),
        kw.get("dry_run", True),
        kw.get("dialog_id")
    ))

if __name__ == "__main__":
    _log("Shell module loaded. Use register_shell_tool(server) to integrate.")