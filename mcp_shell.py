#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Shell v2.0 – безопасное выполнение команд PowerShell/CMD
Теперь без белого списка: можно выполнять любые команды.
Опасные действия помечаются предупреждением и требуют явного подтверждения.
"""
import os
import sys
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from mcp_shared import (
    _log, BaseMCPServer, conversation_memory, dialog_ctx, normalize_path, _ensure_allowed
)

# ─── Конфигурация ──────────────────────────────────────────────────────────
DEFAULT_TIMEOUT = int(os.environ.get("MCP_SHELL_TIMEOUT", "30"))
MAX_OUTPUT_CHARS = int(os.environ.get("MCP_SHELL_MAX_OUTPUT", "10000"))

# Паттерны опасных команд (только для предупреждения, не блокировки)
DANGEROUS_PATTERNS = [
    # Удаление
    (r'Remove-Item', "Удаление файлов/папок (Remove-Item)"),
    (r'rm\s+-rf', "Удаление файлов/папок (rm -rf)"),
    (r'del\s+/[fsq]', "Удаление файлов (del)"),
    (r'format\s+[a-z]:', "Форматирование диска"),
    # Завершение процессов
    (r'Stop-Process\s+-Force', "Принудительное завершение процессов"),
    (r'kill\s+-9', "Принудительное завершение процессов (kill -9)"),
    # Изменение политик
    (r'Set-ExecutionPolicy', "Изменение политики выполнения PowerShell"),
    # Повышение привилегий
    (r'Start-Process\s+-Verb\s+RunAs', "Запуск от имени администратора"),
    # Запись в системные папки
    (r'Write-.*\s+.*\\Windows', "Запись в папку Windows"),
    # Реестр
    (r'Reg\s+(?:add|delete)', "Изменение реестра"),
    # Управление пользователями
    (r'net\s+user\s+/add', "Добавление пользователя"),
    (r'net\s+localgroup', "Изменение групп"),
    # Планировщик задач
    (r'schtasks\s+/create', "Создание задачи в планировщике"),
    # Системные утилиты
    (r'bcdedit', "Изменение конфигурации загрузки"),
    (r'vssadmin\s+delete', "Удаление теневых копий"),
    # Скачивание и выполнение
    (r'wget\s+.*\|', "Скачивание и выполнение через конвейер"),
    (r'curl\s+.*\|', "Скачивание и выполнение через конвейер"),
    (r'Invoke-Expression', "Динамическое выполнение кода (Invoke-Expression)"),
    # Отключение защиты
    (r'Set-MpPreference', "Изменение настроек Defender"),
    (r'Disable-.*Defender', "Отключение защиты"),
]

def _is_dangerous_command(command: str) -> Tuple[bool, str]:
    """
    Проверяет команду на наличие опасных паттернов.
    Возвращает (опасно, причина).
    """
    cmd_lower = command.lower()
    for pattern, description in DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return True, description
    return False, ""

def run_shell(command: str, shell: str = "powershell", timeout: int = DEFAULT_TIMEOUT,
              dry_run: bool = False, confirm_dangerous: bool = False,
              dialog_id: Optional[str] = None) -> Dict:
    """
    Выполняет команду в PowerShell или CMD.
    
    Args:
        command: Команда для выполнения
        shell: "powershell" или "cmd"
        timeout: Таймаут в секундах
        dry_run: Если True, только проверяет команду, не выполняя
        confirm_dangerous: Если True и команда опасна, выполняет её (требуется явное согласие пользователя)
        dialog_id: ID диалога для логирования
    """
    d_id = dialog_id or dialog_ctx.get()
    
    # Проверка на опасность
    dangerous, reason = _is_dangerous_command(command)
    
    if dry_run:
        result = {
            "status": "dry_run",
            "command": command,
            "shell": shell,
            "message": "Command checked."
        }
        if dangerous:
            result["warning"] = f"This command appears dangerous: {reason}"
            result["requires_confirmation"] = True
        return result
    
    # Если команда опасна и подтверждение не получено
    if dangerous and not confirm_dangerous:
        return {
            "status": "blocked",
            "reason": f"Dangerous command detected: {reason}",
            "command": command,
            "dialog_id": d_id,
            "requires_confirmation": True,
            "message": "To execute this dangerous command, call again with confirm_dangerous=True"
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
            "dialog_id": d_id,
            "dangerous_executed": dangerous
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
        "description": "Execute any PowerShell/CMD command. Dangerous commands require confirm_dangerous=True.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command to execute"},
                "shell": {"type": "string", "enum": ["powershell", "cmd"], "default": "powershell"},
                "timeout": {"type": "integer", "default": DEFAULT_TIMEOUT},
                "dry_run": {"type": "boolean", "default": True,
                           "description": "If True, only check command safety without executing"},
                "confirm_dangerous": {"type": "boolean", "default": False,
                                      "description": "Set to True to allow execution of dangerous commands"},
                "dialog_id": {"type": "string"}
            },
            "required": ["command"]
        }
    }, lambda **kw: run_shell(
        kw["command"],
        kw.get("shell", "powershell"),
        kw.get("timeout", DEFAULT_TIMEOUT),
        kw.get("dry_run", True),
        kw.get("confirm_dangerous", False),
        kw.get("dialog_id")
    ))

if __name__ == "__main__":
    _log("Shell module v2.0 loaded. Full command execution with dangerous warnings.")
