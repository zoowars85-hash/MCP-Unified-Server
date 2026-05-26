#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Verbose Mode v2.0 — прогресс-уведомления с агрегацией в batch-режиме.

Ключевое изменение v2.0:
- Batch mode больше НЕ подавляет уведомления полностью
- Вместо этого включается BatchAggregator, который накапливает прогресс
  от всех задач и раз в MCP_BATCH_AGGREGATE_INTERVAL секунд отправляет
  ОДНО сводное уведомление: "Batch: 12/50 done, 3 failed, ETA 45s"
- Rate limiting для обычных (не-batch) уведомлений сохранён
- SQLite-backed настройки verbose (shared между процессами)
"""
import os
import sys
import sqlite3
import threading
import time
from collections import defaultdict, deque
from typing import Dict, List, Optional

# ─── Конфигурация ─────────────────────────────────────────────────────────
VERBOSE_DB_PATH = os.environ.get(
    "MCP_VERBOSE_DB",
    os.environ.get("MCP_MEMORY_PATH", r"C:\Tools\mcp_memory.db")
)
CACHE_TTL_SECONDS = float(os.environ.get("MCP_VERBOSE_CACHE_TTL", "2.0"))
VERBOSE_DEFAULT = os.environ.get("MCP_VERBOSE_DEFAULT", "false").lower() == "true"

# Rate Limiting (для обычных, не-batch уведомлений)
RATE_LIMIT = int(os.environ.get("MCP_VERBOSE_RATE_LIMIT", "2"))
RATE_WINDOW = float(os.environ.get("MCP_VERBOSE_RATE_WINDOW", "5.0"))

# Batch Detection & Aggregation
BATCH_THRESHOLD = int(os.environ.get("MCP_BATCH_THRESHOLD", "5"))
BATCH_WINDOW = float(os.environ.get("MCP_BATCH_WINDOW", "10.0"))
BATCH_COOLDOWN = float(os.environ.get("MCP_BATCH_COOLDOWN", "30.0"))
BATCH_AGGREGATE_INTERVAL = float(os.environ.get("MCP_BATCH_AGGREGATE_INTERVAL", "5.0"))


# ─── Rate Limiter (Sliding Window) ────────────────────────────────────────
class RateLimiter:
    def __init__(self, max_events: int, window_sec: float):
        self.max_events = max_events
        self.window_sec = window_sec
        self._history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=max(max_events * 10, 100))
        )
        self._lock = threading.Lock()
        self._dropped: Dict[str, int] = defaultdict(int)
        self._allowed: Dict[str, int] = defaultdict(int)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hist = self._history[key]
            cutoff = now - self.window_sec
            while hist and hist[0] < cutoff:
                hist.popleft()
            if len(hist) >= self.max_events:
                self._dropped[key] += 1
                return False
            hist.append(now)
            self._allowed[key] += 1
            return True

    def stats(self) -> Dict:
        with self._lock:
            return {
                "max_events_per_window": self.max_events,
                "window_sec": self.window_sec,
                "tracked_keys": len(self._history),
                "total_allowed": sum(self._allowed.values()),
                "total_dropped": sum(self._dropped.values()),
            }

    def reset(self):
        with self._lock:
            self._history.clear()
            self._dropped.clear()
            self._allowed.clear()


# ─── Batch Aggregator ─────────────────────────────────────────────────────
class BatchAggregator:
    """
    Накапливает прогресс от множества задач в batch-режиме и
    периодически отправляет одно сводное уведомление.
    
    Потокобезопасен: несколько фоновых потоков задач могут вызывать
    update() одновременно.
    """
    def __init__(self, aggregate_interval: float):
        self.aggregate_interval = aggregate_interval
        self._lock = threading.Lock()
        # Состояние каждой задачи: {task_id: {"current": N, "total": M, "stage": "...", "status": "running"}}
        self._tasks: Dict[str, Dict] = {}
        self._last_emit_time: float = 0.0
        self._total_emitted: int = 0
        self._total_updates_received: int = 0
        self._active_dialog_id: Optional[str] = None

    def set_dialog(self, dialog_id: str):
        """Установить dialog_id для aggregated-уведомлений."""
        with self._lock:
            self._active_dialog_id = dialog_id

    def update(self, task_id: str, current: int = None, total: int = None,
               stage: str = None, status: str = "running"):
        """
        Обновить прогресс задачи. Если прошло достаточно времени —
        триггерит emit().
        """
        now = time.monotonic()
        should_emit = False
        with self._lock:
            self._total_updates_received += 1
            self._tasks[task_id] = {
                "current": current,
                "total": total,
                "stage": stage,
                "status": status,
                "updated_at": now
            }
            if now - self._last_emit_time >= self.aggregate_interval:
                should_emit = True
                self._last_emit_time = now

        if should_emit:
            self._emit()

    def mark_completed(self, task_id: str, success: bool = True):
        """Отметить задачу как завершённую."""
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["status"] = "completed" if success else "failed"
                self._tasks[task_id]["updated_at"] = time.monotonic()

    def remove_task(self, task_id: str):
        """Удалить задачу из агрегатора (после завершения и финального emit)."""
        with self._lock:
            self._tasks.pop(task_id, None)

    def _emit(self):
        """Сформировать и отправить сводное уведомление."""
        with self._lock:
            if not self._tasks:
                return
            dialog_id = self._active_dialog_id
            tasks_snapshot = dict(self._tasks)
            self._total_emitted += 1

        if not dialog_id:
            return

        # Агрегируем статистику
        total_tasks = len(tasks_snapshot)
        completed = sum(1 for t in tasks_snapshot.values() if t["status"] == "completed")
        failed = sum(1 for t in tasks_snapshot.values() if t["status"] == "failed")
        running = total_tasks - completed - failed

        # Считаем общий прогресс (только для задач с known total)
        total_current = 0
        total_total = 0
        stages = set()
        for t in tasks_snapshot.values():
            if t["current"] is not None:
                total_current += t["current"]
            if t["total"] is not None:
                total_total += t["total"]
            if t["stage"]:
                stages.add(t["stage"])

        # Формируем сообщение
        parts = [f"Batch: {completed}/{total_tasks} done"]
        if failed > 0:
            parts.append(f"{failed} failed")
        if running > 0:
            parts.append(f"{running} running")
        if total_total > 0:
            pct = int(total_current / total_total * 100) if total_total > 0 else 0
            parts.append(f"overall {pct}%")
        if stages:
            stage_str = ", ".join(list(stages)[:3])
            parts.append(f"stages: [{stage_str}]")

        message = " | ".join(parts)

        # Отправляем через STDERR напрямую (минуя rate limiter и is_verbose check,
        # т.к. агрегатор сам контролирует частоту)
        output = f"[PROGRESS][{dialog_id}] {message}\n"
        sys.stderr.write(output)
        sys.stderr.flush()

        # Логируем в память
        try:
            from mcp_shared import conversation_memory
            conversation_memory.add(
                op="_batch_progress",
                paths={"dialog": dialog_id},
                status="aggregated",
                dialog=dialog_id,
                context=message[:300]
            )
        except Exception:
            pass

    def force_emit(self):
        """Принудительная отправка (например, при выходе из batch mode)."""
        self._emit()

    def has_active_tasks(self) -> bool:
        with self._lock:
            return any(
                t["status"] == "running" for t in self._tasks.values()
            )

    def stats(self) -> Dict:
        with self._lock:
            return {
                "aggregate_interval_sec": self.aggregate_interval,
                "active_tasks": len(self._tasks),
                "running": sum(1 for t in self._tasks.values() if t["status"] == "running"),
                "completed": sum(1 for t in self._tasks.values() if t["status"] == "completed"),
                "failed": sum(1 for t in self._tasks.values() if t["status"] == "failed"),
                "total_updates_received": self._total_updates_received,
                "total_aggregated_emits": self._total_emitted,
                "compression_ratio": round(
                    self._total_updates_received / max(self._total_emitted, 1), 1
                )
            }

    def reset(self):
        with self._lock:
            self._tasks.clear()
            self._last_emit_time = 0.0
            self._total_emitted = 0
            self._total_updates_received = 0


# ─── Batch Detector (детектор "шторма" tools) ─────────────────────────────
class BatchDetector:
    def __init__(self, threshold: int, window_sec: float, cooldown_sec: float):
        self.threshold = threshold
        self.window_sec = window_sec
        self.cooldown_sec = cooldown_sec
        self._calls: deque = deque(maxlen=max(threshold * 5, 100))
        self._batch_until: float = 0.0
        self._batch_activations: int = 0
        self._lock = threading.Lock()

    def notify_tool_call(self, tool_name: str = ""):
        now = time.monotonic()
        with self._lock:
            self._calls.append((now, tool_name))
            cutoff = now - self.window_sec
            while self._calls and self._calls[0][0] < cutoff:
                self._calls.popleft()
            if len(self._calls) >= self.threshold and now > self._batch_until:
                self._batch_until = now + self.cooldown_sec
                self._batch_activations += 1

    def is_batch_mode(self) -> bool:
        return time.monotonic() < self._batch_until

    def seconds_remaining(self) -> float:
        return max(0.0, self._batch_until - time.monotonic())

    def force_batch_mode(self, seconds: float = None):
        with self._lock:
            duration = seconds if seconds is not None else self.cooldown_sec
            self._batch_until = time.monotonic() + duration

    def stats(self) -> Dict:
        with self._lock:
            return {
                "threshold": self.threshold,
                "window_sec": self.window_sec,
                "cooldown_sec": self.cooldown_sec,
                "is_batch_mode": self.is_batch_mode(),
                "batch_seconds_remaining": round(self.seconds_remaining(), 1),
                "total_batch_activations": self._batch_activations,
                "recent_calls_count": len(self._calls)
            }

    def reset(self):
        with self._lock:
            self._calls.clear()
            self._batch_until = 0.0


# ─── Глобальные экземпляры ───────────────────────────────────────────────
_rate_limiter = RateLimiter(RATE_LIMIT, RATE_WINDOW)
_batch_detector = BatchDetector(BATCH_THRESHOLD, BATCH_WINDOW, BATCH_COOLDOWN)
_batch_aggregator = BatchAggregator(BATCH_AGGREGATE_INTERVAL)


# ─── SQLite helpers ───────────────────────────────────────────────────────
_verbose_cache: Dict[str, bool] = {}
_cache_updated_at: float = 0.0
_lock = threading.Lock()
_db_initialized = False


def _get_conn():
    conn = sqlite3.connect(VERBOSE_DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def _ensure_db():
    global _db_initialized
    if _db_initialized:
        return
    os.makedirs(os.path.dirname(os.path.abspath(VERBOSE_DB_PATH)) or ".", exist_ok=True)
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS verbose_settings (
                dialog_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_verbose_enabled ON verbose_settings(enabled)")
        conn.commit()
    _db_initialized = True


def _sync_from_db():
    global _cache_updated_at
    _ensure_db()
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT dialog_id, enabled FROM verbose_settings"
            ).fetchall()
            with _lock:
                _verbose_cache.clear()
                for row in rows:
                    _verbose_cache[row[0]] = bool(row[1])
                _cache_updated_at = time.monotonic()
    except Exception:
        pass


def _cache_is_fresh() -> bool:
    return (time.monotonic() - _cache_updated_at) < CACHE_TTL_SECONDS


# ─── Public API: verbose settings ─────────────────────────────────────────
def set_verbose(dialog_id: str, enabled: bool = True):
    _ensure_db()
    now = time.time()
    with _lock:
        _verbose_cache[dialog_id] = enabled
    try:
        with _get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO verbose_settings (dialog_id, enabled, updated_at) VALUES (?, ?, ?)",
                (dialog_id, 1 if enabled else 0, now)
            )
            conn.commit()
    except Exception:
        pass


def is_verbose(dialog_id: str) -> bool:
    if not _cache_is_fresh():
        _sync_from_db()
    with _lock:
        if dialog_id in _verbose_cache:
            return _verbose_cache[dialog_id]
    return VERBOSE_DEFAULT


def list_verbose_dialogs() -> List[str]:
    if not _cache_is_fresh():
        _sync_from_db()
    with _lock:
        return [d_id for d_id, enabled in _verbose_cache.items() if enabled]


def list_disabled_dialogs() -> List[str]:
    if not _cache_is_fresh():
        _sync_from_db()
    with _lock:
        return [d_id for d_id, enabled in _verbose_cache.items() if not enabled]


def clear_verbose_all():
    global _cache_updated_at
    with _lock:
        _verbose_cache.clear()
    try:
        _ensure_db()
        with _get_conn() as conn:
            conn.execute("DELETE FROM verbose_settings")
            conn.commit()
        _cache_updated_at = time.monotonic()
    except Exception:
        pass


def verbose_stats() -> Dict:
    return {
        "global_verbose_default": VERBOSE_DEFAULT,
        "explicitly_enabled_dialogs": len(list_verbose_dialogs()),
        "explicitly_disabled_dialogs": len(list_disabled_dialogs()),
        "enabled_dialog_ids": list_verbose_dialogs(),
        "disabled_dialog_ids": list_disabled_dialogs(),
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "db_path": VERBOSE_DB_PATH,
        "cache_fresh": _cache_is_fresh(),
        "rate_limiter": _rate_limiter.stats(),
        "batch_detector": _batch_detector.stats(),
        "batch_aggregator": _batch_aggregator.stats()
    }


# ─── Batch mode public API ───────────────────────────────────────────────
def notify_tool_call(tool_name: str = ""):
    _batch_detector.notify_tool_call(tool_name)


def is_batch_mode() -> bool:
    return _batch_detector.is_batch_mode()


def force_batch_mode(seconds: float = None):
    _batch_detector.force_batch_mode(seconds)


def get_rate_limiter() -> RateLimiter:
    return _rate_limiter


def get_batch_detector() -> BatchDetector:
    return _batch_detector


def get_batch_aggregator() -> BatchAggregator:
    return _batch_aggregator


# ─── Отправка прогресса (v2.0: с агрегацией вместо подавления) ───────────
def send_progress(dialog_id: str, message: str, level: str = "info",
                  task_id: str = None, current: int = None,
                  total: int = None, stage: str = None):
    """
    Универсальная точка входа для прогресс-уведомлений.
    
    В обычном режиме: rate-limited отправка в STDERR.
    В batch-режиме: перенаправление в BatchAggregator.
    
    Args:
        task_id: если указан И активен batch mode → агрегация
        current/total/stage: метрики для агрегатора
    """
    if not is_verbose(dialog_id):
        return

    # ─── Batch mode: перенаправляем в агрегатор ──────────────────────
    if _batch_detector.is_batch_mode() and task_id:
        _batch_aggregator.set_dialog(dialog_id)
        _batch_aggregator.update(
            task_id=task_id,
            current=current,
            total=total,
            stage=stage or message[:50],
            status="running"
        )
        return

    # ─── Обычный режим: rate-limited отправка ────────────────────────
    if not _rate_limiter.allow(dialog_id):
        return

    output = f"[PROGRESS][{dialog_id}] {message}\n"
    sys.stderr.write(output)
    sys.stderr.flush()

    try:
        from mcp_shared import conversation_memory
        conversation_memory.add(
            op="_progress",
            paths={"dialog": dialog_id},
            status="progress",
            dialog=dialog_id,
            context=message[:200]
        )
    except Exception:
        pass


# ─── Монки-патч BaseMCPServer ─────────────────────────────────────────────
def patch_base_server():
    try:
        from mcp_shared import BaseMCPServer, dialog_ctx
    except Exception:
        return
    if not hasattr(BaseMCPServer, 'send_progress'):
        def send_progress_method(self, message: str, level: str = "info",
                                 task_id: str = None, current: int = None,
                                 total: int = None, stage: str = None):
            send_progress(
                dialog_ctx.get(), message, level,
                task_id=task_id, current=current, total=total, stage=stage
            )
        BaseMCPServer.send_progress = send_progress_method


# ─── Инициализация ────────────────────────────────────────────────────────
_ensure_db()
_sync_from_db()