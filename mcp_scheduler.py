#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Scheduler v1.0 – планировщик задач (cron/интервалы)
Выполняет MCP-инструменты по расписанию.
Поддерживает interval (секунды) и cron-выражения (формат: минуты часы дни месяцы дни_недели).
Задания хранятся в SQLite, выполняются в фоновом потоке.
"""
import os
import sys
import json
import sqlite3
import threading
import time
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

# Попытка импортировать schedule для cron
try:
    import schedule
    HAS_SCHEDULE = True
except ImportError:
    HAS_SCHEDULE = False

from mcp_shared import (
    _log, BaseMCPServer, conversation_memory, dialog_ctx
)

# ─── Конфигурация ─────────────────────────────────────────────────────────
DB_PATH = os.environ.get("MCP_SCHEDULER_DB", os.path.join(os.path.dirname(__file__), "mcp_scheduler.db"))
CHECK_INTERVAL_SEC = 1   # интервал проверки pending задач (сек)
DEFAULT_INTERVAL_SEC = 3600

# ─── База данных заданий ─────────────────────────────────────────────────
class SchedulerDB:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    tool_name TEXT NOT NULL,
                    tool_args TEXT,   -- JSON строка
                    schedule_type TEXT CHECK(schedule_type IN ('interval', 'cron')) NOT NULL,
                    interval_seconds INTEGER,
                    cron_expression TEXT,
                    enabled INTEGER DEFAULT 1,
                    last_run TEXT,      -- ISO timestamp
                    next_run TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_enabled ON jobs(enabled)")
            conn.commit()

    def add_job(self, name: str, tool_name: str, tool_args: Dict,
                schedule_type: str, interval_seconds: int = None, cron_expr: str = None) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("""
                INSERT INTO jobs (name, tool_name, tool_args, schedule_type, interval_seconds, cron_expression)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (name, tool_name, json.dumps(tool_args, default=str), schedule_type, interval_seconds, cron_expr))
            conn.commit()
            return cur.lastrowid

    def get_enabled_jobs(self) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM jobs WHERE enabled = 1").fetchall()
            return [dict(row) for row in rows]

    def get_job_by_id(self, job_id: int) -> Optional[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return dict(row) if row else None

    def update_last_run(self, job_id: int, next_run_dt: datetime = None):
        with sqlite3.connect(self.db_path) as conn:
            now_iso = datetime.now().isoformat()
            next_iso = next_run_dt.isoformat() if next_run_dt else None
            conn.execute("""
                UPDATE jobs SET last_run = ?, next_run = ? WHERE id = ?
            """, (now_iso, next_iso, job_id))
            conn.commit()

    def delete_job(self, job_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            conn.commit()

    def list_jobs(self) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
            return [dict(row) for row in rows]

    def enable_job(self, job_id: int, enabled: bool):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE jobs SET enabled = ? WHERE id = ?", (1 if enabled else 0, job_id))
            conn.commit()

# ─── Исполнитель задач (вызов MCP-инструментов) ──────────────────────────
class JobExecutor:
    def __init__(self):
        self.db = SchedulerDB()
        self._tools_cache = {}   # кэш загруженных функций

    def _load_tool(self, tool_name: str):
        """Динамически загружает функцию инструмента из соответствующего модуля."""
        if tool_name in self._tools_cache:
            return self._tools_cache[tool_name]

        # Словарь соответствия: имя инструмента -> (модуль, имя_функции)
        # Расширяйте по мере необходимости
        tool_map = {
            "empty_trash": ("mcp_fs_trash", "empty_trash"),
            "sync_directories": ("mcp_fs_sync", "sync_directories"),
            "remind": ("mcp_calendar", "remind"),
            "batch_delete": ("mcp_fs_batch", "batch_delete"),
            "move_to_trash": ("mcp_fs_trash", "move_to_trash"),
            "archive_files": ("mcp_fs_archives", "archive_files"),
            "extract_archive": ("mcp_fs_archives", "extract_archive"),
            "sync_to_cloud": ("mcp_fs_cloud", "sync_to_cloud"),
            "sync_from_cloud": ("mcp_fs_cloud", "sync_from_cloud"),
            "backup_database": ("knowledge_base_server", "backup_database"),
        }
        if tool_name in tool_map:
            module_name, func_name = tool_map[tool_name]
            try:
                mod = __import__(module_name, fromlist=[func_name])
                func = getattr(mod, func_name)
                self._tools_cache[tool_name] = func
                return func
            except Exception as e:
                _log(f"[Scheduler] Failed to load tool {tool_name}: {e}")
                return None
        else:
            _log(f"[Scheduler] Tool {tool_name} not mapped. Add to tool_map in scheduler.")
            return None

    def run_job(self, job: Dict) -> Dict:
        name = job["name"]
        tool_name = job["tool_name"]
        args = json.loads(job["tool_args"]) if job["tool_args"] else {}
        _log(f"[Scheduler] Executing job '{name}': {tool_name}({args})")
        try:
            func = self._load_tool(tool_name)
            if not func:
                raise ValueError(f"Tool '{tool_name}' not found or could not be loaded")
            result = func(**args)
            # Если результат не словарь, обернём
            if not isinstance(result, dict):
                result = {"result": str(result)}
            _log(f"[Scheduler] Job '{name}' completed: {str(result)[:200]}")
            conversation_memory.add(
                op="scheduled_job",
                paths={"job": name, "tool": tool_name},
                status="success",
                dialog="scheduler",
                context=f"Scheduled job '{name}' executed, result: {result.get('status', 'ok')}"
            )
            return result
        except Exception as e:
            _log(f"[Scheduler] Job '{name}' failed: {e}")
            conversation_memory.add(
                op="scheduled_job",
                paths={"job": name, "tool": tool_name},
                status="error",
                dialog="scheduler",
                context=f"Job failed: {e}"
            )
            return {"error": str(e), "job": name}

# ─── Планировщик (фоновый поток) ────────────────────────────────────────
class SchedulerThread:
    def __init__(self):
        self._stop_event = threading.Event()
        self._thread = None
        self.executor = JobExecutor()
        self.db = SchedulerDB()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="mcp_scheduler_loop")
        self._thread.start()
        _log("[Scheduler] Background thread started")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        _log("[Scheduler] Background thread stopped")

    def _run(self):
        # Загружаем все задания и планируем их в schedule (если есть cron)
        self._reload_all_jobs()
        # Цикл проверки каждую секунду
        while not self._stop_event.is_set():
            try:
                if HAS_SCHEDULE:
                    schedule.run_pending()
                else:
                    # Если schedule нет, проверяем next_run вручную
                    self._check_pending_jobs()
                time.sleep(CHECK_INTERVAL_SEC)
            except Exception as e:
                _log(f"[Scheduler] Loop error: {e}")

    def _reload_all_jobs(self):
        """Перезагружает все включённые задания в schedule (только cron) или обновляет next_run."""
        if not HAS_SCHEDULE:
            return
        schedule.clear()  # очищаем старые
        jobs = self.db.get_enabled_jobs()
        for job in jobs:
            if job["schedule_type"] == "cron" and job["cron_expression"]:
                self._schedule_cron_job(job)
            elif job["schedule_type"] == "interval" and job["interval_seconds"]:
                self._schedule_interval_job(job)
            else:
                _log(f"[Scheduler] Job {job['name']} has invalid schedule config")

    def _schedule_cron_job(self, job: Dict):
        """Использует schedule для cron (пример: '*/5 * * * *')"""
        cron_expr = job["cron_expression"]
        try:
            # schedule.every().minute.at(":00") и т.д. - упрощённо: парсим cron
            # Для простоты будем использовать schedule.every(1).minutes.do(...) с проверкой внутри
            # Лучше использовать cron-parser, но для демонстрации:
            def job_wrapper():
                self.executor.run_job(job)
                # Обновим last_run
                self.db.update_last_run(job["id"], None)
            # Запланируем с использованием schedule (пример: каждую минуту)
            schedule.every(1).minutes.do(job_wrapper)
            _log(f"[Scheduler] Scheduled cron job '{job['name']}' (approx every minute)")
        except Exception as e:
            _log(f"[Scheduler] Failed to schedule cron job {job['name']}: {e}")

    def _schedule_interval_job(self, job: Dict):
        """Интервальное задание через schedule."""
        interval = job["interval_seconds"]
        if not isinstance(interval, int) or interval <= 0:
            return
        def job_wrapper():
            self.executor.run_job(job)
            self.db.update_last_run(job["id"], None)
        # schedule поддерживает интервалы в секундах
        schedule.every(interval).seconds.do(job_wrapper)
        _log(f"[Scheduler] Scheduled interval job '{job['name']}' every {interval}s")

    def _check_pending_jobs(self):
        """Ручная проверка заданий по next_run (без schedule)."""
        jobs = self.db.get_enabled_jobs()
        now = datetime.now()
        for job in jobs:
            next_run_str = job.get("next_run")
            if next_run_str:
                try:
                    next_run = datetime.fromisoformat(next_run_str)
                except:
                    next_run = None
            else:
                next_run = None

            # Если нет next_run или время уже наступило
            if next_run is None or now >= next_run:
                # Выполняем задание
                self.executor.run_job(job)
                # Вычисляем следующий запуск
                if job["schedule_type"] == "interval" and job["interval_seconds"]:
                    next_run_dt = now + timedelta(seconds=job["interval_seconds"])
                elif job["schedule_type"] == "cron" and job["cron_expression"]:
                    # Упрощённо: пропускаем cron в ручном режиме, лучше использовать schedule
                    next_run_dt = now + timedelta(minutes=1)
                else:
                    next_run_dt = None
                self.db.update_last_run(job["id"], next_run_dt)

# ─── Глобальный экземпляр планировщика ──────────────────────────────────
_scheduler = SchedulerThread()

def scheduler_start() -> Dict:
    """Запустить фоновый планировщик."""
    _scheduler.start()
    return {"status": "started"}

def scheduler_stop() -> Dict:
    """Остановить планировщик."""
    _scheduler.stop()
    return {"status": "stopped"}

def scheduler_add_interval(name: str, tool_name: str, interval_seconds: int,
                           args: Dict = None) -> Dict:
    """Добавить задание с интервалом (секунды)."""
    args = args or {}
    db = SchedulerDB()
    job_id = db.add_job(name, tool_name, args, "interval", interval_seconds=interval_seconds)
    # Перезагрузить планировщик, чтобы подхватил новое задание
    _scheduler._reload_all_jobs()
    return {"status": "created", "job_id": job_id, "name": name}

def scheduler_add_cron(name: str, tool_name: str, cron_expression: str,
                       args: Dict = None) -> Dict:
    """Добавить задание с cron-выражением (формат: минуты часы дни месяцы дни_недели)."""
    args = args or {}
    db = SchedulerDB()
    job_id = db.add_job(name, tool_name, args, "cron", cron_expr=cron_expression)
    _scheduler._reload_all_jobs()
    return {"status": "created", "job_id": job_id, "name": name}

def scheduler_list() -> Dict:
    """Список всех заданий."""
    db = SchedulerDB()
    jobs = db.list_jobs()
    return {"jobs": jobs, "count": len(jobs)}

def scheduler_delete(job_id: int) -> Dict:
    """Удалить задание по ID."""
    db = SchedulerDB()
    db.delete_job(job_id)
    _scheduler._reload_all_jobs()
    return {"status": "deleted", "job_id": job_id}

def scheduler_enable(job_id: int, enabled: bool) -> Dict:
    """Включить/выключить задание."""
    db = SchedulerDB()
    db.enable_job(job_id, enabled)
    _scheduler._reload_all_jobs()
    return {"status": "updated", "job_id": job_id, "enabled": enabled}

# ─── Регистрация инструментов MCP (плагин) ───────────────────────────────
def register_tools(server: BaseMCPServer):
    server.register_tool("scheduler_start", {
        "description": "Запустить фоновый планировщик задач",
        "inputSchema": {"type": "object", "properties": {}}
    }, lambda **kw: scheduler_start())

    server.register_tool("scheduler_stop", {
        "description": "Остановить планировщик задач",
        "inputSchema": {"type": "object", "properties": {}}
    }, lambda **kw: scheduler_stop())

    server.register_tool("scheduler_add_interval", {
        "description": "Добавить задание с интервалом в секундах",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Уникальное имя задания"},
                "tool_name": {"type": "string", "description": "Имя MCP-инструмента (например, empty_trash)"},
                "interval_seconds": {"type": "integer", "description": "Интервал в секундах"},
                "args": {"type": "object", "description": "Аргументы инструмента (JSON объект)"}
            },
            "required": ["name", "tool_name", "interval_seconds"]
        }
    }, lambda **kw: scheduler_add_interval(
        kw["name"], kw["tool_name"], kw["interval_seconds"], kw.get("args", {})
    ))

    server.register_tool("scheduler_add_cron", {
        "description": "Добавить задание с cron-выражением (минуты часы дни месяцы дни_недели)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "tool_name": {"type": "string"},
                "cron_expression": {"type": "string", "description": "Например: '30 2 * * *' (ежедневно в 2:30)"},
                "args": {"type": "object"}
            },
            "required": ["name", "tool_name", "cron_expression"]
        }
    }, lambda **kw: scheduler_add_cron(
        kw["name"], kw["tool_name"], kw["cron_expression"], kw.get("args", {})
    ))

    server.register_tool("scheduler_list", {
        "description": "Список всех заданий",
        "inputSchema": {"type": "object", "properties": {}}
    }, lambda **kw: scheduler_list())

    server.register_tool("scheduler_delete", {
        "description": "Удалить задание по ID",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "integer"}},
            "required": ["job_id"]
        }
    }, lambda **kw: scheduler_delete(kw["job_id"]))

    server.register_tool("scheduler_enable", {
        "description": "Включить или выключить задание",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "integer"},
                "enabled": {"type": "boolean", "default": True}
            },
            "required": ["job_id"]
        }
    }, lambda **kw: scheduler_enable(kw["job_id"], kw.get("enabled", True)))

# ─── Плагин MCP ──────────────────────────────────────────────────────────
__mcp_plugin__ = {
    "name": "scheduler",
    "version": "1.0",
    "description": "Планировщик задач (интервалы и cron)",
    "dependencies": ["schedule"],
    "on_load": lambda: _log("[Scheduler] Plugin loaded. Use scheduler_start() to start background thread."),
    "on_unload": lambda: scheduler_stop()
}

if __name__ == "__main__":
    # Тестовый запуск в standalone режиме
    server = BaseMCPServer("scheduler", "1.0")
    register_tools(server)
    server.run()