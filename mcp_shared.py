#!/usr/bin/env python3
"""
MCP Shared Core v5.3 — FULL AUTO MEMORY + ДОЛГОСРОЧНЫЙ АРХИВ + SQLITE CHUNK CACHE
Автоматическое: сохранение, сжатие, очистка (с перемещением в архив),
вспоминание, оптимизация. Добавлен бессрочный архив для записей старше TTL.
Добавлен SQLite-бэкенд для chunk cache и автоматическая архивация по лимитам.
"""
import os
import re
import sys
import json
import time
import uuid
import zlib
import threading
import sqlite3
import hashlib
import functools
import subprocess
import urllib.request
import urllib.error
import contextvars
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Union, Tuple

# ─── Context Vars ─────────────────────────────────────────────────────────
dialog_ctx = contextvars.ContextVar('dialog_id', default='default')
_server_name = contextvars.ContextVar('server_name', default='unknown')

# ─── Environment ──────────────────────────────────────────────────────────
SEARCH_TIMEOUT = int(os.environ.get("MCP_SEARCH_TIMEOUT", "60"))
ANALYSIS_TIMEOUT = int(os.environ.get("MCP_ANALYSIS_TIMEOUT", "120"))
CHUNK_SIZE = int(os.environ.get("MCP_CHUNK_SIZE", "1000"))
ENABLE_PAGINATION = os.environ.get("MCP_ENABLE_PAGINATION", "true").lower() == "true"
AUTO_DISCOVER_UNC = os.environ.get("MCP_AUTO_DISCOVER_UNC", "false").lower() == "true"
MAX_READ_BYTES = int(os.environ.get("MCP_MAX_READ_BYTES", "500000"))
MEMORY_DB_PATH = os.environ.get("MCP_MEMORY_PATH", r"C:\Tools\mcp_memory.db")
MAX_MEMORY_ENTRIES = int(os.environ.get("MCP_MEMORY_MAX_ENTRIES", "1000000"))
DEFAULT_TTL_DAYS = int(os.environ.get("MCP_MEMORY_TTL_DAYS", "90"))

# ─── ARCHIVE SETTINGS (долгосрочное хранение) ────────────────────────────
ARCHIVE_ENABLED = os.environ.get("MCP_ARCHIVE_ENABLED", "true").lower() == "true"
ARCHIVE_TTL_DAYS = int(os.environ.get("MCP_ARCHIVE_TTL_DAYS", str(DEFAULT_TTL_DAYS)))

# ---- Автоматическая архивация и ограничения (Patch 2) ----
ACTIVE_ENTRIES_LIMIT = int(os.environ.get("MCP_ACTIVE_ENTRIES_LIMIT", "5000"))
ACTIVE_DB_SIZE_MB = int(os.environ.get("MCP_ACTIVE_DB_SIZE_MB", "200"))
ARCHIVE_BATCH_SIZE = int(os.environ.get("MCP_ARCHIVE_BATCH_SIZE", "1000"))
AUTO_CLEANUP_OFF_HOURS = os.environ.get("MCP_AUTO_CLEANUP_OFF_HOURS", "2-5")
AUTO_CLEANUP_ENABLED = os.environ.get("MCP_AUTO_CLEANUP_ENABLED", "true").lower() == "true"
CHUNK_CACHE_BACKEND = os.environ.get("MCP_CHUNK_CACHE_BACKEND", "sqlite")
CHUNK_TTL_SEC = int(os.environ.get("MCP_CHUNK_TTL_SEC", "120"))
COMPRESS_ARCHIVE = os.environ.get("MCP_COMPRESS_ARCHIVE", "true").lower() == "true"
COMPRESS_LEVEL = int(os.environ.get("MCP_COMPRESS_LEVEL", "6"))

# ─── AUTO MEMORY SETTINGS ────────────────────────────────────────────────
AUTO_SNAPSHOT_INTERVAL = int(os.environ.get("MCP_AUTO_SNAPSHOT_SEC", "300"))
AUTO_CLEANUP_INTERVAL = int(os.environ.get("MCP_AUTO_CLEANUP_SEC", "3600"))
AUTO_COMPRESS_THRESHOLD = int(os.environ.get("MCP_AUTO_COMPRESS_ENTRIES", "10000"))
AUTO_VACUUM_INTERVAL = int(os.environ.get("MCP_AUTO_VACUUM_SEC", "86400"))
ENABLE_AUTO_MEMORY = os.environ.get("MCP_AUTO_MEMORY", "false").lower() == "true"  # Changed default to false

EVENT_BUS_URL = os.environ.get("EVENT_BUS_URL", "")
COMPRESSION_THRESHOLD = 1024

# ─── Logging ──────────────────────────────────────────────────────────────
_log_lock = threading.Lock()

def _log(msg: str):
    with _log_lock:
        ts = datetime.now().strftime("%H:%M:%S")
        srv = _server_name.get("")
        prefix = f"[{ts}][{srv}]" if srv else f"[{ts}]"
        print(f"{prefix} {msg}", file=sys.stderr, flush=True)

def _format_size(b: int) -> str:
    if b < 0:
        return "unknown"
    for unit in ('B', 'KB', 'MB', 'GB', 'TB', 'PB'):
        if abs(b) < 1024.0:
            s = f"{b:.2f} {unit}"
            return s.replace(".00 ", " ")
        b /= 1024.0
    return f"{b:.2f} EB"

# ─── Path Normalization ───────────────────────────────────────────────────
@functools.lru_cache(maxsize=8192)
def normalize_path(path: str) -> str:
    if not isinstance(path, str):
        return str(path) if path is not None else ""
    path = path.strip().strip('"\'')
    if path.startswith('~'):
        path = os.path.expanduser(path)
    if path.startswith('\\') or path.startswith('//'):
        path = path.replace('/', '\\')
        while '\\\\' in path[2:]:
            path = path.replace('\\\\', '\\')
        path = '\\\\' + path.lstrip('\\').rstrip('\\')
        return path
    if sys.platform == 'win32':
        path = path.replace('/', '\\')
        while '\\\\' in path:
            path = path.replace('\\\\', '\\')
        path = path.rstrip('\\')
        if len(path) == 2 and path[1] == ':':
            path += '\\'
        return path
    else:
        while '//' in path:
            path = path.replace('//', '/')
        return path.rstrip('/')

# ─── Drive/UNC Discovery ─────────────────────────────────────────────────
_drive_cache: Tuple[Optional[float], List[Path]] = (None, [])
_unc_cache: Tuple[Optional[float], List[str]] = (None, [])
_CACHE_TTL_SECONDS = 30
_cache_lock = threading.Lock()

def _detect_local_drives() -> List[Path]:
    global _drive_cache
    now = time.monotonic()
    with _cache_lock:
        if _drive_cache[0] and (now - _drive_cache[0] < _CACHE_TTL_SECONDS):
            return _drive_cache[1]
    drives = []
    if sys.platform == 'win32':
        import string
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            try:
                if drive.exists() and drive.is_dir() and os.access(drive, os.R_OK):
                    drives.append(drive.resolve())
            except Exception:
                continue
    else:
        for p in [Path("/"), Path.home(), Path("/mnt"), Path("/media")]:
            if p.exists() and p.is_dir():
                drives.append(p.resolve())
    with _cache_lock:
        _drive_cache = (now, drives)
    return drives

def _detect_unc_shares() -> List[str]:
    global _unc_cache
    now = time.monotonic()
    with _cache_lock:
        if _unc_cache[0] and (now - _unc_cache[0] < _CACHE_TTL_SECONDS):
            return _unc_cache[1]
    shares = set()
    if sys.platform == 'win32':
        try:
            kwargs = {"capture_output": True, "text": True, "timeout": 10,
                      "creationflags": getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
            proc = subprocess.run(['net', 'use'], **kwargs)
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    if 'OK' in line and '\\\\' in line:
                        matches = re.findall(r'(\\\\[^\s"\'\\]+(?:\\[^\s"\'\\]+)*)', line)
                        shares.update(m for m in matches if len(m) > 2)
        except Exception:
            pass
    result = list(shares)
    with _cache_lock:
        _unc_cache = (now, result)
    return result

# ─── Path Security ────────────────────────────────────────────────────────
_allowed_paths_lock = threading.Lock()
_resolved_local: Optional[List[Path]] = None
_resolved_unc: Optional[List[str]] = None

def _resolve_allowed_paths_force():
    global _resolved_local, _resolved_unc
    env_paths_str = os.environ.get("MCP_ALLOWED_PATHS", "")
    env_unc_str = os.environ.get("MCP_ALLOWED_UNC_PATHS", "")
    env_paths = [Path(p.strip()).resolve() for p in env_paths_str.split(";") if p.strip()]
    local = env_paths if env_paths else _detect_local_drives()
    env_unc = [p.strip() for p in env_unc_str.split(";") if p.strip()]
    auto_unc = _detect_unc_shares() if AUTO_DISCOVER_UNC else []
    _resolved_local = local
    _resolved_unc = list(set(env_unc + auto_unc))

def get_allowed_paths():
    global _resolved_local, _resolved_unc
    if _resolved_local is None:
        with _allowed_paths_lock:
            if _resolved_local is None:
                _resolve_allowed_paths_force()
    return _resolved_local, _resolved_unc

_BLOCKED_PATTERNS = [
    r'^[a-z]:\\windows($|\\)',
    r'^[a-z]:\\program files($|\\)',
    r'^[a-z]:\\program files \(x86\)($|\\)',
    r'^[a-z]:\\programdata($|\\)',
    r'^[a-z]:\\recovery($|\\)',
    r'^[a-z]:\\system volume information($|\\)',
    r'^[a-z]:\\\$recycle\.bin($|\\)',
    r'^[a-z]:\\perflogs($|\\)',
    r'^[a-z]:\\\$.*',
    r'^[a-z]:\\(pagefile\.sys|hiberfil\.sys|swapfile\.sys|bootmgr|bootmgr\.efi)$'
]
_BLOCKED_REGEX = [re.compile(p, re.IGNORECASE) for p in _BLOCKED_PATTERNS]

def _is_path_allowed(resolved_path: Path, op: str):
    res_str = str(resolved_path)
    res_lower = res_str.lower()
    if res_lower.startswith('\\\\'):
        ps_norm = res_lower.rstrip('\\')
        _, allowed_unc = get_allowed_paths()
        if not any(ps_norm == a.lower().rstrip('\\') or ps_norm.startswith(a.lower().rstrip('\\') + '\\')
                   for a in allowed_unc):
            raise PermissionError(f"{op}: UNC path not allowed")
        return
    allowed_local, _ = get_allowed_paths()
    if not any(os.path.commonpath([res_str, str(r)]).lower() == str(r).lower() for r in allowed_local):
        raise PermissionError(f"{op}: path outside allowed drives")
    for rx in _BLOCKED_REGEX:
        if rx.match(res_lower):
            raise PermissionError(f"{op}: system path denied")

def _ensure_allowed(path: Path, op: str):
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    _is_path_allowed(resolved, op)

# ─── Event Bus ────────────────────────────────────────────────────────────
def publish_event(topic: str, payload: Dict) -> bool:
    if not EVENT_BUS_URL:
        return False
    try:
        data = json.dumps({"topic": topic, "payload": payload}).encode('utf-8')
        req = urllib.request.Request(EVENT_BUS_URL, data=data,
                                     headers={"Content-Type": "application/json"}, method='POST')
        urllib.request.urlopen(req, timeout=1.0)
        return True
    except Exception:
        return False

# ─── Base MCP Server (уникальные dialog_id) ──────────────────────────────
class BaseMCPServer:
    def __init__(self, name: str, version: str):
        self.name = name
        self.version = version
        self.tools = []
        self._handlers = {}
        self._lock = threading.Lock()
        self._current_dialog_id = None
        self._register_dialog_tools()

    def _register_dialog_tools(self):
        self.register_tool(
            "set_dialog_title",
            {
                "description": "Установить понятное название для текущего диалога.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"]
                }
            },
            self._set_dialog_title
        )
        self.register_tool(
            "get_current_dialog_id",
            {
                "description": "Получить текущий идентификатор диалога",
                "inputSchema": {"type": "object", "properties": {}}
            },
            self._get_current_dialog_id
        )

    def _set_dialog_title(self, title: str) -> Dict:
        new_id = self._make_dialog_id_from_title(title)
        self._current_dialog_id = new_id
        dialog_ctx.set(new_id)
        _log(f"Dialog title set to '{title}', new dialog_id = {new_id}")
        try:
            conversation_memory.add(
                op="set_dialog_title",
                paths={"title": title},
                status="ok",
                dialog=new_id,
                context=f"Dialog renamed to '{title}'"
            )
        except Exception:
            pass
        return {"status": "ok", "dialog_id": new_id, "title": title}

    def _get_current_dialog_id(self) -> Dict:
        current = self._current_dialog_id or dialog_ctx.get()
        return {"dialog_id": current}

    def _make_dialog_id_from_title(self, title: str) -> str:
        safe = re.sub(r'[^\w\-_]+', '_', title.strip().replace(' ', '_'))
        if not safe:
            safe = "dialog"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{safe}_{timestamp}"

    def _generate_unique_dialog_id(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_uuid = uuid.uuid4().hex[:8]
        return f"dialog_{timestamp}_{short_uuid}"

    def register_tool(self, name: str, schema: dict, handler):
        self.tools.append({
            "name": name,
            "description": schema.get("description", ""),
            "inputSchema": schema.get("inputSchema", {"type": "object", "properties": {}})
        })
        self._handlers[name] = handler

    def handle_initialize(self, req_id):
        self._current_dialog_id = self._generate_unique_dialog_id()
        dialog_ctx.set(self._current_dialog_id)
        _log(f"New dialog initialized with ID: {self._current_dialog_id}")
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.name, "version": self.version}
            }
        }

    def handle_tools_list(self, req_id):
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": self.tools}}

    def handle_tool_call(self, req_id, params):
        name = params.get("name")
        args = params.get("arguments", {})

        # ─── Batch Detector: уведомляем о каждом вызове tool ─────────────
        try:
            from mcp_verbose import notify_tool_call
            notify_tool_call(name)
        except Exception:
            pass
        # ─────────────────────────────────────────────────────────────────

        explicit_dialog = args.get("dialog_id")
        if explicit_dialog:
            d_id = explicit_dialog
        elif self._current_dialog_id is not None:
            d_id = self._current_dialog_id
        else:
            d_id = dialog_ctx.get()

        token = dialog_ctx.set(d_id)
        try:
            if name not in self._handlers:
                return {"jsonrpc": "2.0", "id": req_id,
                        "error": {"code": -32601, "message": f"Unknown tool: {name}"}}
            _log(f"CALL {name} | dialog={d_id}")
            result = self._handlers[name](**args)
            conversation_memory.add(
                op=name,
                paths=args if args else {"tool": name},
                status="success",
                dialog=d_id,
                context=f"Called {name} with {len(args)} args"
            )
            return {
                "jsonrpc": "2.0", "id": req_id,
                "result": {"content": [{"type": "text",
                                         "text": json.dumps(result, ensure_ascii=False, default=str)}]}
            }
        except Exception as e:
            _log(f"ERROR {name}: {e}")
            conversation_memory.add(
                op=name,
                paths={"error": str(e)},
                status="error",
                dialog=d_id,
                context=f"Error in {name}: {e}"
            )
            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}}
        finally:
            dialog_ctx.reset(token)

    def handle_request(self, req: dict) -> Optional[dict]:
        method = req.get("method")
        rid = req.get("id")
        params = req.get("params", {})
        if method == "initialize":
            return self.handle_initialize(rid)
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return self.handle_tools_list(rid)
        if method == "tools/call":
            return self.handle_tool_call(rid, params)
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"Unknown method: {method}"}}

    def run(self):
        token = _server_name.set(self.name)
        _log(f"v{self.version} started | MEM: {MEMORY_DB_PATH} | MAX: {MAX_MEMORY_ENTRIES} | TTL: {DEFAULT_TTL_DAYS}d")
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                    if isinstance(req, list):
                        responses = [self.handle_request(r) for r in req]
                        responses = [r for r in responses if r is not None]
                        if responses:
                            print(json.dumps(responses, ensure_ascii=False, default=str), flush=True)
                    else:
                        resp = self.handle_request(req)
                        if resp:
                            print(json.dumps(resp, ensure_ascii=False, default=str), flush=True)
                except json.JSONDecodeError:
                    print(json.dumps({"jsonrpc": "2.0",
                                      "error": {"code": -32700, "message": "Parse error"}}), flush=True)
        finally:
            _server_name.reset(token)

# ─── Chunk Cache ──────────────────────────────────────────────────────────
class ChunkCache:
    def __init__(self, ttl_seconds: int = 300, max_entries: int = 100):
        self._cache = {}
        self._times = {}
        self._lock = threading.Lock()
        self.ttl = ttl_seconds
        self.max_entries = max_entries

    def get(self, chunk_id: str):
        with self._lock:
            if chunk_id in self._cache:
                if time.monotonic() - self._times[chunk_id] < self.ttl:
                    return self._cache[chunk_id]
                else:
                    del self._cache[chunk_id]
                    del self._times[chunk_id]
            return None

    def set(self, chunk_id: str, data: Any):
        with self._lock:
            if len(self._cache) >= self.max_entries:
                oldest = min(self._times, key=self._times.get)
                del self._cache[oldest]
                del self._times[oldest]
            self._cache[chunk_id] = data
            self._times[chunk_id] = time.monotonic()

    def delete(self, chunk_id: str):
        with self._lock:
            self._cache.pop(chunk_id, None)
            self._times.pop(chunk_id, None)


class SQLiteChunkCache:
    """SQLite-based chunk cache with TTL and auto-cleanup."""
    def __init__(self, db_path: str, ttl_seconds: int = 120):
        self.db_path = db_path
        self.ttl = ttl_seconds
        self._lock = threading.Lock()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def get(self, chunk_id: str):
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT data_json FROM chunk_cache WHERE chunk_id = ? AND expires_at > ?",
                    (chunk_id, time.time())
                ).fetchone()
                return json.loads(row[0]) if row else None
            finally:
                conn.close()

    def set(self, chunk_id: str, data: Any):
        with self._lock:
            conn = self._get_conn()
            try:
                now = time.time()
                expires = now + self.ttl
                data_json = json.dumps(data, default=str)
                conn.execute(
                    """INSERT OR REPLACE INTO chunk_cache 
                       (chunk_id, data_json, created_at, expires_at, size_bytes) 
                       VALUES (?, ?, ?, ?, ?)""",
                    (chunk_id, data_json, now, expires, len(data_json))
                )
                conn.commit()
            finally:
                conn.close()

    def delete(self, chunk_id: str):
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("DELETE FROM chunk_cache WHERE chunk_id = ?", (chunk_id,))
                conn.commit()
            finally:
                conn.close()

    def cleanup(self):
        """Удаляет просроченные чанки (вызывается из фонового потока)."""
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute("DELETE FROM chunk_cache WHERE expires_at < ?", (time.time(),))
                conn.commit()
                if cursor.rowcount > 0:
                    _log(f"CHUNK CACHE: cleaned up {cursor.rowcount} expired entries")
            finally:
                conn.close()


# ─── CONVERSATION MEMORY v5.3 — FULL AUTO + ДОЛГОСРОЧНЫЙ АРХИВ + SQLITE CACHE ────────────
class ConversationMemory:
    """
    Автоматическая память диалогов с бессрочным архивом.
    Записи старше TTL перемещаются в архив (не удаляются).
    """

    def __init__(self, db_path: str = MEMORY_DB_PATH, max_entries: int = MAX_MEMORY_ENTRIES):
        self.db_path = db_path
        self.max_entries = max_entries
        self._lock = threading.RLock()  # Changed from Lock to RLock to prevent deadlock
        self._auto_threads_started = False
        self._init_db()
        self._migrate()
        # Инициализация chunk cache в зависимости от бэкенда
        if CHUNK_CACHE_BACKEND == "sqlite":
            self.chunk_cache = SQLiteChunkCache(self.db_path, ttl_seconds=CHUNK_TTL_SEC)
        else:
            self.chunk_cache = ChunkCache(ttl_seconds=CHUNK_TTL_SEC)
        if ENABLE_AUTO_MEMORY:
            self._start_auto_threads()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA cache_size=-64000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)) or ".", exist_ok=True)
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS entries (
                        id TEXT PRIMARY KEY,
                        ts TEXT NOT NULL,
                        dialog TEXT NOT NULL,
                        op TEXT NOT NULL,
                        paths_json TEXT,
                        context TEXT,
                        meta_json TEXT,
                        status TEXT,
                        related_json TEXT,
                        checksum TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_dialog ON entries (dialog)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_op ON entries (op)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON entries (ts)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_dialog_op ON entries (dialog, op)")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS mem_snapshots (
                        id TEXT PRIMARY KEY,
                        ts TEXT NOT NULL,
                        dialog TEXT NOT NULL,
                        note TEXT,
                        state_json TEXT NOT NULL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_snap_dialog ON mem_snapshots (dialog)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_snap_ts ON mem_snapshots (ts)")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS compressed_history (
                        dialog TEXT NOT NULL,
                        ts TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        original_count INTEGER,
                        compressed_count INTEGER
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_comp_dialog ON compressed_history (dialog)")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS archived_entries (
                        id TEXT PRIMARY KEY,
                        ts TEXT NOT NULL,
                        dialog TEXT NOT NULL,
                        op TEXT NOT NULL,
                        paths_json TEXT,
                        context TEXT,
                        meta_json TEXT,
                        status TEXT,
                        related_json TEXT,
                        checksum TEXT,
                        archived_at TEXT NOT NULL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_arch_dialog ON archived_entries (dialog)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_arch_ts ON archived_entries (ts)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_arch_op ON archived_entries (op)")

                # Chunk Cache (SQLite backend)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS chunk_cache (
                        chunk_id TEXT PRIMARY KEY,
                        data_json TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        expires_at REAL NOT NULL,
                        size_bytes INTEGER
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_chunk_expires ON chunk_cache(expires_at)")

                conn.commit()
            finally:
                conn.close()

    def _migrate(self):
        """Безопасная миграция схемы БД при запуске."""
        with self._lock:
            conn = self._get_conn()
            try:
                # 1. Добавляем колонки для сжатия в архив (если их нет)
                cols = [row[1] for row in conn.execute("PRAGMA table_info(archived_entries)").fetchall()]
                if "context_blob" not in cols:
                    conn.execute("ALTER TABLE archived_entries ADD COLUMN context_blob BLOB")
                    _log("MIGRATION: added context_blob to archived_entries")
                if "original_length" not in cols:
                    conn.execute("ALTER TABLE archived_entries ADD COLUMN original_length INTEGER")
                    _log("MIGRATION: added original_length to archived_entries")

                # 2. Создаем таблицу метаданных (если нет)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
                conn.execute("INSERT OR IGNORE INTO metadata (key, value) VALUES ('version', '5.3')")

                conn.commit()
            except Exception as e:
                _log(f"MIGRATION error: {e}")
            finally:
                conn.close()

    @staticmethod
    def _compress_text(text: str) -> bytes:
        if not text:
            return b''
        return zlib.compress(text.encode('utf-8'), level=COMPRESS_LEVEL)

    @staticmethod
    def _decompress_text(data: bytes) -> str:
        if not data:
            return ''
        try:
            return zlib.decompress(data).decode('utf-8')
        except Exception:
            return ""

    def _is_off_hours(self) -> bool:
        """Проверяет, находимся ли мы в диапазоне 'тихих часов' для фоновых задач."""
        try:
            start, end = map(int, AUTO_CLEANUP_OFF_HOURS.split('-'))
            current_hour = datetime.now().hour
            if start <= end:
                return start <= current_hour < end
            else:  # переход через полночь (например, 22-6)
                return current_hour >= start or current_hour < end
        except Exception:
            return False

    def _start_auto_threads(self):
        if self._auto_threads_started:
            return
        self._auto_threads_started = True
        threading.Thread(target=self._auto_snapshot_loop, daemon=True, name="auto_snapshot").start()
        threading.Thread(target=self._auto_cleanup_loop, daemon=True, name="auto_cleanup").start()
        threading.Thread(target=self._auto_compress_loop, daemon=True, name="auto_compress").start()
        threading.Thread(target=self._auto_vacuum_loop, daemon=True, name="auto_vacuum").start()
        _log("AUTO MEMORY: snapshot={}s cleanup={}s compress={} vacuum={}s".format(
            AUTO_SNAPSHOT_INTERVAL, AUTO_CLEANUP_INTERVAL,
            AUTO_COMPRESS_THRESHOLD, AUTO_VACUUM_INTERVAL))

    def _auto_snapshot_loop(self):
        while True:
            time.sleep(AUTO_SNAPSHOT_INTERVAL)
            try:
                with self._lock:
                    conn = self._get_conn()
                    try:
                        cutoff = (datetime.now() - timedelta(seconds=AUTO_SNAPSHOT_INTERVAL * 2)).isoformat()
                        dialogs = conn.execute(
                            "SELECT DISTINCT dialog FROM entries WHERE ts > ?", (cutoff,)
                        ).fetchall()
                        for d in dialogs:
                            dialog = d[0]
                            count = conn.execute(
                                "SELECT COUNT(*) FROM entries WHERE dialog = ?", (dialog,)
                            ).fetchone()[0]
                            if count > 0:
                                snap_id = f"auto_{int(time.time())}_{dialog[:20]}"
                                state = json.dumps({
                                    "dialog": dialog,
                                    "entries": count,
                                    "timestamp": datetime.now().isoformat(),
                                    "auto": True
                                })
                                conn.execute(
                                    "INSERT INTO mem_snapshots (id, ts, dialog, note, state_json) VALUES (?, ?, ?, ?, ?)",
                                    (snap_id, datetime.now().isoformat(), dialog, f"Auto-snapshot ({count} entries)", state)
                                )
                                conn.commit()
                                _log(f"AUTO SNAPSHOT: {dialog} ({count} entries)")
                    finally:
                        conn.close()
            except Exception as e:
                _log(f"AUTO SNAPSHOT error: {e}")

    def _auto_cleanup_loop(self):
        while True:
            time.sleep(AUTO_CLEANUP_INTERVAL)
            try:
                if not AUTO_CLEANUP_ENABLED:
                    continue

                # Chunk cache cleanup - always
                if hasattr(self, 'chunk_cache') and hasattr(self.chunk_cache, 'cleanup'):
                    self.chunk_cache.cleanup()

                if not self._is_off_hours():
                    continue  # Пропускаем тяжёлые операции вне тихих часов

                if ARCHIVE_ENABLED:
                    moved = self._archive_old_entries()
                    if moved > 0:
                        _log(f"AUTO ARCHIVE: moved {moved} old records to archive")
                else:
                    removed = self._cleanup_ttl()
                    if removed > 0:
                        _log(f"AUTO CLEANUP: removed {removed} old records")
            except Exception as e:
                _log(f"AUTO CLEANUP/ARCHIVE error: {e}")

    def _auto_compress_loop(self):
        while True:
            time.sleep(AUTO_CLEANUP_INTERVAL)
            try:
                with self._lock:
                    conn = self._get_conn()
                    try:
                        rows = conn.execute("""
                            SELECT dialog, COUNT(*) as cnt
                            FROM entries
                            GROUP BY dialog
                            HAVING cnt > ?
                        """, (AUTO_COMPRESS_THRESHOLD,)).fetchall()
                        for row in rows:
                            dialog = row[0]
                            count = row[1]
                            last_comp = conn.execute(
                                "SELECT MAX(ts) FROM compressed_history WHERE dialog = ?", (dialog,)
                            ).fetchone()[0]
                            if last_comp:
                                last_comp_dt = datetime.fromisoformat(last_comp)
                                if (datetime.now() - last_comp_dt).seconds < AUTO_CLEANUP_INTERVAL:
                                    continue
                            entries = conn.execute(
                                "SELECT context FROM entries WHERE dialog = ? ORDER BY ts DESC LIMIT 1000",
                                (dialog,)
                            ).fetchall()
                            if entries:
                                contexts = [e[0] for e in entries if e[0]]
                                summary = self._generate_summary(contexts[:100])
                                conn.execute(
                                    "INSERT INTO compressed_history (dialog, ts, summary, original_count, compressed_count) VALUES (?, ?, ?, ?, ?)",
                                    (dialog, datetime.now().isoformat(), summary, count, len(contexts))
                                )
                                conn.commit()
                                _log(f"AUTO COMPRESS: {dialog} ({count} → summary)")
                    finally:
                        conn.close()
            except Exception as e:
                _log(f"AUTO COMPRESS error: {e}")

    def _auto_vacuum_loop(self):
        while True:
            time.sleep(AUTO_VACUUM_INTERVAL)
            try:
                with self._lock:
                    conn = self._get_conn()
                    try:
                        before = os.path.getsize(self.db_path)
                        conn.execute("VACUUM")
                        conn.commit()
                        after = os.path.getsize(self.db_path)
                        if before != after:
                            _log(f"AUTO VACUUM: {_format_size(before)} → {_format_size(after)}")
                    finally:
                        conn.close()
            except Exception as e:
                _log(f"AUTO VACUUM error: {e}")

    def _generate_summary(self, contexts: List[str]) -> str:
        if not contexts:
            return "Empty"
        words = []
        for ctx in contexts[:50]:
            words.extend(ctx.split()[:10])
        from collections import Counter
        word_freq = Counter(w.lower() for w in words if len(w) > 3)
        top_words = [w for w, _ in word_freq.most_common(20)]
        return " | ".join(top_words[:10])

    # ─── АРХИВАЦИЯ: вместо удаления ───────────────────────────────────────
    def _archive_old_entries(self) -> int:
        """Перемещает записи старше ARCHIVE_TTL_DAYS в архив."""
        if ARCHIVE_TTL_DAYS <= 0:
            return 0
        with self._lock:
            conn = self._get_conn()
            try:
                cutoff = (datetime.now() - timedelta(days=ARCHIVE_TTL_DAYS)).isoformat()
                rows = conn.execute("""
                    SELECT id, ts, dialog, op, paths_json, context, meta_json, status, related_json, checksum
                    FROM entries WHERE ts < ? LIMIT ?
                """, (cutoff, ARCHIVE_BATCH_SIZE)).fetchall()
                if not rows:
                    return 0
                now_iso = datetime.now().isoformat()
                for row in rows:
                    context_text = row[5] or ""
                    context_blob = self._compress_text(context_text) if COMPRESS_ARCHIVE else None
                    orig_len = len(context_text)

                    conn.execute("""
                        INSERT INTO archived_entries
                        (id, ts, dialog, op, paths_json, context, meta_json, status, related_json, checksum, archived_at, context_blob, original_length)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9],
                          now_iso, context_blob, orig_len))

                ids = [r[0] for r in rows]
                placeholders = ','.join('?' * len(ids))
                conn.execute(f"DELETE FROM entries WHERE id IN ({placeholders})", ids)
                conn.commit()
                return len(rows)
            finally:
                conn.close()

    def _archive_overflow_dialog(self, dialog: str) -> int:
        """Перемещает лишние записи диалога (превысившие ACTIVE_ENTRIES_LIMIT) в архив."""
        if ACTIVE_ENTRIES_LIMIT <= 0:
            return 0
        with self._lock:
            conn = self._get_conn()
            try:
                count = conn.execute("SELECT COUNT(*) FROM entries WHERE dialog = ?", (dialog,)).fetchone()[0]
                if count <= ACTIVE_ENTRIES_LIMIT:
                    return 0

                # Архивируем с запасом (20%), чтобы не вызывать архивацию при каждом новом сообщении
                excess = count - ACTIVE_ENTRIES_LIMIT + (ACTIVE_ENTRIES_LIMIT // 5)
                rows = conn.execute(
                    """SELECT id, ts, dialog, op, paths_json, context, meta_json, status, related_json, checksum 
                       FROM entries WHERE dialog = ? ORDER BY ts ASC LIMIT ?""",
                    (dialog, excess)
                ).fetchall()

                if not rows:
                    return 0

                now_iso = datetime.now().isoformat()
                for row in rows:
                    context_text = row[5] or ""
                    context_blob = self._compress_text(context_text) if COMPRESS_ARCHIVE else None
                    orig_len = len(context_text)

                    conn.execute(
                        """INSERT INTO archived_entries 
                           (id, ts, dialog, op, paths_json, context, meta_json, status, related_json, checksum, archived_at, context_blob, original_length)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9],
                         now_iso, context_blob, orig_len)
                    )

                ids = [r[0] for r in rows]
                placeholders = ','.join('?' * len(ids))
                conn.execute(f"DELETE FROM entries WHERE id IN ({placeholders})", ids)
                conn.commit()
                _log(f"OVERFLOW ARCHIVE: moved {len(rows)} entries from dialog {dialog}")
                return len(rows)
            except Exception as e:
                _log(f"OVERFLOW ARCHIVE error: {e}")
                return 0
            finally:
                conn.close()

    def _cleanup_ttl(self) -> int:
        """Удаление записей (если архив отключён)."""
        if DEFAULT_TTL_DAYS <= 0:
            return 0
        with self._lock:
            conn = self._get_conn()
            try:
                cutoff = (datetime.now() - timedelta(days=DEFAULT_TTL_DAYS)).isoformat()
                cursor = conn.execute("DELETE FROM entries WHERE ts < ?", (cutoff,))
                conn.commit()
                return cursor.rowcount
            finally:
                conn.close()

    # ─── ОСНОВНЫЕ МЕТОДЫ (add, query, get_dialog_thread) ──────────────────
    def add(self, op: str, paths: Union[str, dict], status: str,
            dialog: str = None, meta: dict = None,
            context: str = None, related: list = None,
            category: str = "auto", tags: List[str] = None) -> str:
        d_id = dialog or dialog_ctx.get()
        with self._lock:
            conn = self._get_conn()
            try:
                entry_id = f"{int(time.time() * 1000) & 0xFFffFFff:08x}"
                if isinstance(paths, str):
                    paths = {"path": paths}
                paths_json = json.dumps(paths)
                meta_dict = meta or {}
                meta_dict["category"] = category
                meta_dict["tags"] = tags or []
                meta_dict["server"] = _server_name.get("")
                meta_json = json.dumps(meta_dict)
                related_json = json.dumps(related or [])
                checksum = hashlib.md5(paths_json.encode()).hexdigest()[:8]
                ts = datetime.now().isoformat()

                conn.execute("""
                    INSERT INTO entries (id, ts, dialog, op, paths_json, context, meta_json, status, related_json, checksum)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (entry_id, ts, d_id, op, paths_json, context, meta_json, status, related_json, checksum))

                if self.max_entries > 0:
                    count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
                    if count > self.max_entries:
                        excess = count - self.max_entries
                        conn.execute(
                            "DELETE FROM entries WHERE id IN (SELECT id FROM entries ORDER BY ts ASC LIMIT ?)",
                            (excess,)
                        )
                        _log(f"MEMORY LIMIT: removed {excess} oldest entries")

                conn.commit()

                # Автоматическая архивация при переполнении диалога
                if ACTIVE_ENTRIES_LIMIT > 0:
                    self._archive_overflow_dialog(d_id)

                return entry_id
            finally:
                conn.close()

    def query(self, dialog: str = None, op: str = None, path: str = None,
              category: str = None, tags: List[str] = None, ext: str = None,
              hours: int = None, limit: int = 50, include_context: bool = True) -> List[dict]:
        with self._lock:
            conn = self._get_conn()
            try:
                query = "SELECT * FROM entries WHERE 1=1"
                params = []
                if dialog: query += " AND dialog = ?"; params.append(dialog)
                if op: query += " AND op = ?"; params.append(op)
                if hours:
                    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
                    query += " AND ts > ?"; params.append(cutoff)
                query += " ORDER BY ts DESC LIMIT ?"
                params.append(limit)
                rows = conn.execute(query, params).fetchall()
                results = []
                for row in rows:
                    entry = dict(row)
                    paths_plain = json.loads(entry.get("paths_json", "{}")) if entry.get("paths_json") else {}
                    context_plain = entry.get("context", "")
                    if path:
                        path_found = any(path.lower() in str(v).lower() for v in paths_plain.values() if isinstance(v, str))
                        if not path_found and context_plain:
                            path_found = path.lower() in context_plain.lower()
                        if not path_found: continue
                    try:
                        meta = json.loads(entry.get("meta_json", "{}"))
                    except:
                        meta = {}
                    if category and meta.get("category") != category:
                        continue
                    if tags and not any(t in meta.get("tags", []) for t in tags):
                        continue
                    results.append({
                        "id": entry["id"], "ts": entry["ts"], "dialog": entry["dialog"],
                        "op": entry["op"], "paths": paths_plain,
                        "context": context_plain if include_context else "",
                        "meta": meta, "status": entry["status"],
                        "related": json.loads(entry.get("related_json", "[]")),
                        "checksum": entry["checksum"]
                    })
                return results
            finally:
                conn.close()

    def get_dialog_thread(self, dialog: str = None, limit: int = 100) -> List[dict]:
        d_id = dialog or dialog_ctx.get()
        with self._lock:
            conn = self._get_conn()
            try:
                comp = conn.execute(
                    "SELECT summary FROM compressed_history WHERE dialog = ? ORDER BY ts DESC LIMIT 1",
                    (d_id,)
                ).fetchone()
                rows = conn.execute(
                    "SELECT * FROM entries WHERE dialog = ? ORDER BY ts DESC LIMIT ?",
                    (d_id, limit)
                ).fetchall()
                results = []
                for row in rows:
                    entry = dict(row)
                    paths_plain = json.loads(entry.get("paths_json", "{}")) if entry.get("paths_json") else {}
                    try:
                        meta = json.loads(entry.get("meta_json", "{}"))
                        related = json.loads(entry.get("related_json", "[]"))
                    except:
                        meta, related = {}, []
                    results.append({
                        "id": entry["id"], "ts": entry["ts"], "dialog": entry["dialog"],
                        "op": entry["op"], "paths": paths_plain,
                        "context": entry.get("context", ""),
                        "meta": meta, "status": entry["status"], "related": related,
                        "checksum": entry["checksum"]
                    })
                result = {"dialog": d_id, "entries": results, "count": len(results)}
                if comp:
                    result["compressed_summary"] = comp[0]
                return result
            finally:
                conn.close()

    def recall_fact(self, query: str, dialog: str = None) -> Dict:
        d_id = dialog or dialog_ctx.get()
        with self._lock:
            conn = self._get_conn()
            try:
                results = self.query(dialog=d_id, path=query, limit=10)
                if results:
                    return {
                        "found": True,
                        "source": "dialog_memory",
                        "confidence": "high",
                        "fact": results[0],
                        "related": len(results)
                    }
                results = self.query(path=query, limit=5)
                if results:
                    return {
                        "found": True,
                        "source": "global_memory",
                        "confidence": "medium",
                        "fact": results[0],
                        "related": len(results)
                    }
                comp = conn.execute(
                    "SELECT summary FROM compressed_history WHERE dialog = ? ORDER BY ts DESC LIMIT 1",
                    (d_id,)
                ).fetchone()
                if comp and query.lower() in comp[0].lower():
                    return {
                        "found": True,
                        "source": "compressed_history",
                        "confidence": "low",
                        "summary": comp[0][:300]
                    }
                return {"found": False, "source": None, "confidence": "none"}
            finally:
                conn.close()

    # ─── НОВЫЕ МЕТОДЫ ДЛЯ РАБОТЫ С АРХИВОМ ────────────────────────────────
    def search_archive(self, dialog: str = None, op: str = None, path: str = None,
                       category: str = None, tags: List[str] = None,
                       hours: int = None, limit: int = 100) -> List[dict]:
        """Поиск в долгосрочном архиве (бессрочное хранение)."""
        with self._lock:
            conn = self._get_conn()
            try:
                query = "SELECT * FROM archived_entries WHERE 1=1"
                params = []
                if dialog: query += " AND dialog = ?"; params.append(dialog)
                if op: query += " AND op = ?"; params.append(op)
                if hours:
                    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
                    query += " AND ts > ?"; params.append(cutoff)
                query += " ORDER BY ts DESC LIMIT ?"
                params.append(limit)
                rows = conn.execute(query, params).fetchall()
                results = []
                for row in rows:
                    entry = dict(row)
                    paths_plain = json.loads(entry.get("paths_json", "{}")) if entry.get("paths_json") else {}
                    context_plain = entry.get("context", "")
                    # Декомпрессия context_blob если есть
                    if entry.get("context_blob") and COMPRESS_ARCHIVE:
                        context_plain = self._decompress_text(entry["context_blob"])
                    if path:
                        path_found = any(path.lower() in str(v).lower() for v in paths_plain.values() if isinstance(v, str))
                        if not path_found and context_plain:
                            path_found = path.lower() in context_plain.lower()
                        if not path_found: continue
                    try:
                        meta = json.loads(entry.get("meta_json", "{}"))
                    except:
                        meta = {}
                    if category and meta.get("category") != category: continue
                    if tags and not any(t in meta.get("tags", []) for t in tags): continue
                    results.append({
                        "id": entry["id"], "ts": entry["ts"], "dialog": entry["dialog"],
                        "op": entry["op"], "paths": paths_plain,
                        "context": context_plain, "meta": meta, "status": entry["status"],
                        "related": json.loads(entry.get("related_json", "[]")),
                        "checksum": entry["checksum"], "archived_at": entry["archived_at"]
                    })
                return results
            finally:
                conn.close()

    def restore_from_archive(self, entry_id: str, target_dialog: str = None) -> bool:
        """Восстановить запись из архива в активную таблицу."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT * FROM archived_entries WHERE id = ?", (entry_id,)).fetchone()
                if not row:
                    return False
                context = row["context"]
                if row["context_blob"] and COMPRESS_ARCHIVE:
                    context = self._decompress_text(row["context_blob"])
                conn.execute("""
                    INSERT INTO entries (id, ts, dialog, op, paths_json, context, meta_json, status, related_json, checksum)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (row["id"], row["ts"], target_dialog or row["dialog"], row["op"],
                      row["paths_json"], context, row["meta_json"], row["status"],
                      row["related_json"], row["checksum"]))
                conn.execute("DELETE FROM archived_entries WHERE id = ?", (entry_id,))
                conn.commit()
                _log(f"RESTORED from archive: {entry_id} -> dialog {target_dialog or row['dialog']}")
                return True
            finally:
                conn.close()

    def restore_dialog_from_archive(self, dialog: str, limit: int = 50) -> int:
        """Восстановить из архива последние limit сообщений для диалога (вставляя в entries)."""
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM archived_entries WHERE dialog = ? ORDER BY ts DESC LIMIT ?",
                    (dialog, limit)
                ).fetchall()
                if not rows:
                    return 0
                for row in rows:
                    context = row["context"]
                    if row["context_blob"] and COMPRESS_ARCHIVE:
                        context = self._decompress_text(row["context_blob"])
                    conn.execute(
                        """INSERT INTO entries 
                           (id, ts, dialog, op, paths_json, context, meta_json, status, related_json, checksum)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (row["id"], row["ts"], row["dialog"], row["op"], row["paths_json"],
                         context, row["meta_json"], row["status"], row["related_json"], row["checksum"])
                    )
                ids = [r["id"] for r in rows]
                placeholders = ','.join('?' * len(ids))
                conn.execute(f"DELETE FROM archived_entries WHERE id IN ({placeholders})", ids)
                conn.commit()
                _log(f"RESTORED DIALOG: {len(rows)} entries from archive for dialog {dialog}")
                return len(rows)
            finally:
                conn.close()

    def purge_archive(self, older_than_days: int = 730) -> Dict:
        """Удалить из архива записи старше указанного числа дней."""
        with self._lock:
            conn = self._get_conn()
            try:
                cutoff = (datetime.now() - timedelta(days=older_than_days)).isoformat()
                cursor = conn.execute("DELETE FROM archived_entries WHERE ts < ?", (cutoff,))
                conn.commit()
                _log(f"PURGE ARCHIVE: removed {cursor.rowcount} entries older than {older_than_days} days")
                return {"status": "ok", "removed": cursor.rowcount, "older_than_days": older_than_days}
            finally:
                conn.close()

    def archive_stats(self) -> Dict:
        """Статистика архива."""
        with self._lock:
            conn = self._get_conn()
            try:
                total = conn.execute("SELECT COUNT(*) FROM archived_entries").fetchone()[0]
                dialogs = conn.execute("SELECT COUNT(DISTINCT dialog) FROM archived_entries").fetchone()[0]
                first = conn.execute("SELECT ts FROM archived_entries ORDER BY ts ASC LIMIT 1").fetchone()
                last = conn.execute("SELECT ts FROM archived_entries ORDER BY ts DESC LIMIT 1").fetchone()
                return {
                    "archive_enabled": ARCHIVE_ENABLED,
                    "archive_ttl_days": ARCHIVE_TTL_DAYS,
                    "total_archived_entries": total,
                    "distinct_dialogs_in_archive": dialogs,
                    "oldest_entry": first["ts"] if first else None,
                    "newest_entry": last["ts"] if last else None
                }
            finally:
                conn.close()

    # ─── СНАПШОТЫ И СТАТИСТИКА (без изменений) ───────────────────────────
    def save_snapshot(self, state: Dict, dialog: str = None, note: str = "") -> str:
        d_id = dialog or dialog_ctx.get()
        with self._lock:
            conn = self._get_conn()
            try:
                snap_id = f"snap_{hashlib.sha256(json.dumps(state).encode()).hexdigest()[:8]}"
                conn.execute(
                    "INSERT INTO mem_snapshots (id, ts, dialog, note, state_json) VALUES (?, ?, ?, ?, ?)",
                    (snap_id, datetime.now().isoformat(), d_id, note, json.dumps(state, default=str))
                )
                conn.commit()
                _log(f"SNAPSHOT saved: {snap_id} | dialog={d_id}")
                return snap_id
            finally:
                conn.close()

    def get_snapshot(self, dialog: str = None, latest: bool = True) -> Optional[Dict]:
        d_id = dialog or dialog_ctx.get()
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM mem_snapshots WHERE dialog = ? ORDER BY ts DESC LIMIT ?",
                    (d_id, 1 if latest else 999)
                ).fetchall()
                if not rows:
                    return None if latest else []
                if latest:
                    r = rows[0]
                    return {
                        "id": r["id"], "ts": r["ts"], "dialog": r["dialog"],
                        "note": r["note"], "state": json.loads(r["state_json"])
                    }
                else:
                    return [{"id": r["id"], "ts": r["ts"], "dialog": r["dialog"],
                             "note": r["note"], "state": json.loads(r["state_json"])} for r in rows]
            finally:
                conn.close()

    def get_stats(self) -> dict:
        with self._lock:
            conn = self._get_conn()
            try:
                total = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
                dialogs = conn.execute("SELECT COUNT(DISTINCT dialog) FROM entries").fetchone()[0]
                snaps = conn.execute("SELECT COUNT(*) FROM mem_snapshots").fetchone()[0]
                comps = conn.execute("SELECT COUNT(*) FROM compressed_history").fetchone()[0]
                first = conn.execute("SELECT ts FROM entries ORDER BY ts ASC LIMIT 1").fetchone()
                last = conn.execute("SELECT ts FROM entries ORDER BY ts DESC LIMIT 1").fetchone()
                db_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
                top_dialogs = conn.execute("""
                    SELECT dialog, COUNT(*) as cnt FROM entries GROUP BY dialog ORDER BY cnt DESC LIMIT 10
                """).fetchall()
                archive_stats = self.archive_stats() if ARCHIVE_ENABLED else {}
                return {
                    "total_entries": total,
                    "active_dialogs": dialogs,
                    "snapshots": snaps,
                    "compressed_histories": comps,
                    "max_entries_limit": self.max_entries if self.max_entries > 0 else "unlimited",
                    "ttl_days": DEFAULT_TTL_DAYS if DEFAULT_TTL_DAYS > 0 else "unlimited",
                    "first_entry": first["ts"] if first else None,
                    "last_entry": last["ts"] if last else None,
                    "db_size_bytes": db_size,
                    "db_size_human": _format_size(db_size),
                    "top_dialogs": [{"dialog": d[0], "entries": d[1]} for d in top_dialogs],
                    "auto_memory_enabled": ENABLE_AUTO_MEMORY,
                    "auto_snapshot_interval_sec": AUTO_SNAPSHOT_INTERVAL,
                    "auto_cleanup_interval_sec": AUTO_CLEANUP_INTERVAL,
                    "auto_compress_threshold": AUTO_COMPRESS_THRESHOLD,
                    "archive": archive_stats,
                    "active_entries_limit": ACTIVE_ENTRIES_LIMIT,
                    "chunk_cache_backend": CHUNK_CACHE_BACKEND
                }
            finally:
                conn.close()

    def clear_all(self, dry_run: bool = False, clear_archive: bool = False) -> Dict:
        if dry_run:
            return self.get_stats()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("DELETE FROM entries")
                conn.execute("DELETE FROM mem_snapshots")
                conn.execute("DELETE FROM compressed_history")
                if clear_archive:
                    conn.execute("DELETE FROM archived_entries")
                conn.commit()
                _log("MEMORY CLEARED: all entries, snapshots, compressed history removed" +
                     (" + archive" if clear_archive else ""))
                return {"status": "cleared", "message": "Memory erased", "archive_cleared": clear_archive}
            finally:
                conn.close()

# ─── Global Instances ─────────────────────────────────────────────────────
conversation_memory = ConversationMemory()
chunk_cache = conversation_memory.chunk_cache

def get_global_memory() -> ConversationMemory:
    return conversation_memory

# ─── Helpers (без изменений) ──────────────────────────────────────────────
def list_directory_sync(path: str, recursive: bool = False) -> Dict[str, Any]:
    p = Path(normalize_path(path))
    try:
        _ensure_allowed(p, "list_directory_sync")
    except PermissionError as e:
        return {"error": str(e)}
    if not p.is_dir():
        return {"error": f"Not a directory: {path}"}
    entries = []
    try:
        for entry in os.scandir(p):
            try:
                stat = entry.stat(follow_symlinks=False)
                entries.append({
                    "name": entry.name, "path": entry.path,
                    "is_dir": entry.is_dir(follow_symlinks=False),
                    "is_file": entry.is_file(follow_symlinks=False),
                    "size": stat.st_size if entry.is_file() else 0,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
                })
            except (OSError, PermissionError):
                continue
    except PermissionError:
        return {"error": "Permission denied"}
    return {"entries": entries, "count": len(entries)}

def query_llm(prompt: str, model: str = None) -> str:
    endpoint = os.environ.get("LLM_ENDPOINT", "http://localhost:1234/v1/chat/completions")
    payload = {
        "model": model or "local-model",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2
    }
    try:
        data = json.dumps(payload).encode('utf-8')
        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(endpoint, data=data, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=120) as response:
            if response.status == 200:
                resp_data = json.loads(response.read().decode('utf-8'))
                return resp_data["choices"][0]["message"]["content"]
    except Exception as e:
        _log(f"LLM Query failed: {e}")
    return ""

def is_placeholder_path(path_str: str) -> Tuple[bool, str]:
    if not path_str or not isinstance(path_str, str):
        return True, "empty or non-string"
    name = Path(path_str).name
    patterns = [
        re.compile(r'^\[File_\d+_.*\]$'), re.compile(r'^\[.*\]$'),
        re.compile(r'^File_\d+_.*$'), re.compile(r'^<.*>$'), re.compile(r'^{.*}$'),
        re.compile(r'^placeholder.*$', re.I), re.compile(r'^example.*$', re.I)
    ]
    for pat in patterns:
        if pat.match(name):
            return True, f"matches {pat.pattern}"
    if name.startswith('File_') and '_' in name:
        parts = name.split('_')
        if len(parts) >= 2 and parts[1].isdigit():
            return True, "looks like File_N_Name"
    return False, ""

def validate_paths_decorator(func):
    from functools import wraps
    @wraps(func)
    def wrapper(*args, **kwargs):
        paths_to_check = []
        for key in ('path', 'source', 'destination', 'src', 'dst'):
            if key in kwargs:
                paths_to_check.append(kwargs[key])
        for p in paths_to_check:
            is_ph, reason = is_placeholder_path(p)
            if is_ph:
                return {"status": "validation_failed", "path": p, "reason": reason}
        return func(*args, **kwargs)
    return wrapper

# ─── Verbose mode (полностью вынесено в mcp_verbose) ──────────────────────
from mcp_verbose import (
    set_verbose, is_verbose, send_progress,
    patch_base_server, clear_verbose_all, verbose_stats,
    list_verbose_dialogs, list_disabled_dialogs, VERBOSE_DEFAULT,
    notify_tool_call, is_batch_mode, force_batch_mode,
    get_rate_limiter, get_batch_detector, get_batch_aggregator
)
patch_base_server()