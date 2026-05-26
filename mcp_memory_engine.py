#!C:\Tools\.venv\Scripts\python.exe
"""
MCP Memory Engine v4.5 – Добавлена поддержка долгосрочного архива
Совместим с mcp_shared v5.2 (таблицы entries/mem_snapshots/archived_entries)
"""
import os
import sys
import json
import hashlib
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Union
from mcp_shared import (
    _log, BaseMCPServer, conversation_memory, dialog_ctx, MEMORY_DB_PATH
)

class MemoryEngine:
    def __init__(self, db_path: str = MEMORY_DB_PATH):
        self.db_path = db_path
        self._ensure_tables()

    def _ensure_tables(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL;")
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
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _auto_categorize(path: str) -> Dict[str, Union[str, List[str]]]:
        pl = path.lower()
        name = os.path.basename(pl)
        ext = os.path.splitext(name)[1]
        if any(k in pl for k in ("tools", "utils", "soft", "setup", "install")):
            cat = "tools"
        elif any(k in pl for k in ("tv", "video", "movie", "movies")):
            cat = "media_tv"
        elif any(k in pl for k in ("music", "audio", "sound", "mp3", "lossless")):
            cat = "media_audio"
        elif any(k in pl for k in ("game", "games", "gaming")):
            cat = "games"
        elif any(k in pl for k in ("doc", "office", "docs", "work", "project")):
            cat = "docs"
        elif any(k in pl for k in ("backup", "archive", "old", "trash", "temp")):
            cat = "archive"
        else:
            cat = "other"
        tags = []
        ext_map = {
            ('.exe', '.msi', '.bat', '.ps1', '.cmd', '.vbs'): "#executable",
            ('.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv'): "#video",
            ('.mp3', '.flac', '.wav', '.aac', '.ogg', '.m4a'): "#audio",
            ('.iso', '.img', '.vhd', '.vhdx'): "#disk_image",
            ('.zip', '.rar', '.7z', '.tar', '.gz', '.bz2'): "#archive",
            ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff'): "#image",
            ('.doc', '.docx', '.pdf', '.txt', '.rtf', '.odt', '.ppt', '.pptx'): "#document",
        }
        for exts, tag in ext_map.items():
            if ext in exts:
                tags.append(tag)
                break
        if any(k in name for k in ("defender", "security", "crypt", "antivir", "firewall")):
            tags.append("#security")
        return {"category": cat, "tags": list(set(tags)), "ext": ext if ext else "no_ext"}

    def add(self, op: str, paths: Union[str, Dict], status: str,
            dialog: str = None, meta: Dict = None,
            context: str = None, related: List[str] = None) -> str:
        d_id = dialog or dialog_ctx.get()
        target = ""
        if isinstance(paths, dict):
            target = paths.get("to") or paths.get("path") or ""
        elif isinstance(paths, str):
            target = paths
        auto = self._auto_categorize(target)
        return conversation_memory.add(
            op=op, paths=paths, status=status, dialog=d_id, meta=meta,
            context=context, related=related,
            category=auto["category"], tags=auto["tags"]
        )

    def query(self, dialog: str = None, op: str = None, path: str = None,
              category: str = None, tags: List[str] = None, ext: str = None,
              hours: int = None, limit: int = 20,
              include_related: bool = False, include_context: bool = True) -> List[Dict]:
        d_id = dialog or dialog_ctx.get()
        return conversation_memory.query(
            dialog=d_id, op=op, path=path, category=category, tags=tags,
            ext=ext, hours=hours, limit=limit, include_context=include_context
        )

    def get_thread(self, dialog: str = None, limit: int = 100) -> Dict:
        d_id = dialog or dialog_ctx.get()
        return conversation_memory.get_dialog_thread(dialog=d_id, limit=limit)

    def save_snapshot(self, state: Dict, dialog: str = None, note: str = "") -> str:
        d_id = dialog or dialog_ctx.get()
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            snap_id = f"snap_{hashlib.sha256(json.dumps(state).encode()).hexdigest()[:8]}"
            conn.execute(
                "INSERT INTO mem_snapshots (id, ts, dialog, note, state_json) VALUES (?, ?, ?, ?, ?)",
                (snap_id, datetime.now().isoformat(), d_id, note, json.dumps(state, default=str))
            )
            conn.commit()
            return snap_id
        finally:
            conn.close()

    def get_snapshot(self, dialog: str = None, latest: bool = True) -> Optional[Dict]:
        d_id = dialog or dialog_ctx.get()
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(
                "SELECT * FROM mem_snapshots WHERE dialog = ? ORDER BY ts DESC LIMIT ?",
                (d_id, 1 if latest else 999)
            )
            rows = cur.fetchall()
            if not rows:
                return None if latest else []
            if latest:
                r = rows[0]
                return {
                    "id": r["id"], "ts": r["ts"], "dialog": r["dialog"],
                    "note": r["note"], "state": json.loads(r["state_json"])
                }
            else:
                return [{
                    "id": r["id"], "ts": r["ts"], "dialog": r["dialog"],
                    "note": r["note"], "state": json.loads(r["state_json"])
                } for r in rows]
        finally:
            conn.close()

    def get_stats(self) -> Dict:
        stats = conversation_memory.get_stats()
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            snap_count = conn.execute("SELECT COUNT(*) FROM mem_snapshots").fetchone()[0]
            stats["snapshots"] = snap_count
            dialog_count = conn.execute("SELECT COUNT(DISTINCT dialog) FROM entries").fetchone()[0]
            stats["distinct_dialogs"] = dialog_count
            return stats
        finally:
            conn.close()

    def clear_all(self, dry_run: bool = False) -> Dict:
        if dry_run:
            return self.get_stats()
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.execute("DELETE FROM entries")
            conn.execute("DELETE FROM mem_snapshots")
            conn.commit()
            return {"removed": "all", "remaining": 0, "message": "Memory & Snapshots cleared"}
        finally:
            conn.close()

    def list_dialogs(self, limit: int = 50) -> Dict:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute("""
                SELECT dialog, COUNT(*) as entries,
                       MIN(ts) as first_seen, MAX(ts) as last_seen
                FROM entries
                GROUP BY dialog
                ORDER BY last_seen DESC
                LIMIT ?
            """, (limit,))
            dialogs = []
            for row in cur:
                dialogs.append({
                    "dialog_id": row["dialog"],
                    "entries": row["entries"],
                    "first_seen": row["first_seen"],
                    "last_seen": row["last_seen"]
                })
            return {"status": "success", "dialogs": dialogs, "count": len(dialogs)}
        finally:
            conn.close()

    def search_archive(self, dialog: str = None, op: str = None, path: str = None,
                       category: str = None, tags: List[str] = None,
                       hours: int = None, limit: int = 100) -> Dict:
        results = conversation_memory.search_archive(
            dialog=dialog, op=op, path=path,
            category=category, tags=tags,
            hours=hours, limit=limit
        )
        return {"status": "success", "count": len(results), "results": results}

    def restore_from_archive(self, entry_id: str, target_dialog: str = None) -> Dict:
        if not entry_id or not entry_id.strip():
            return {"status": "error", "message": "entry_id is required and cannot be empty"}
        success = conversation_memory.restore_from_archive(entry_id.strip(), target_dialog)
        return {"status": "restored" if success else "not_found", "entry_id": entry_id}

    def restore_dialog_from_archive(self, dialog_id: str, limit: int = 50) -> Dict:
        """Восстановить последние limit сообщений диалога из архива."""
        restored = conversation_memory.restore_dialog_from_archive(dialog_id, limit)
        return {"status": "restored", "dialog_id": dialog_id, "restored_count": restored}

    def purge_archive(self, older_than_days: int = 730) -> Dict:
        """Очистить архив от записей старше указанного числа дней."""
        return conversation_memory.purge_archive(older_than_days)

    def optimize_database(self) -> Dict:
        """Запустить VACUUM и очистить кеш чанков."""
        with conversation_memory._lock:
            conn = conversation_memory._get_conn()
            try:
                conn.execute("VACUUM")
                conn.commit()
                # также можно оптимизировать FTS, если есть
            finally:
                conn.close()
            if hasattr(conversation_memory.chunk_cache, 'cleanup'):
                conversation_memory.chunk_cache.cleanup()
        return {"status": "ok", "message": "Database vacuumed and chunk cache cleaned"}

    def archive_stats(self) -> Dict:
        return conversation_memory.archive_stats()


# ─── ИСПРАВЛЕННАЯ ФУНКЦИЯ ────────────────────────────────────────────────
def log_conversation(role: str, content: str, dialog_id: Optional[str] = None) -> str:
    """Сохранить сообщение диалога (user/assistant) в память.
       Возвращает простую строку, чтобы избежать экранирования в JSON."""
    d_id = dialog_id or dialog_ctx.get()
    if not content or not content.strip():
        return "Error: Empty content"
    conversation_memory.add(
        op="conversation",
        paths={"role": role},
        status="logged",
        dialog=d_id,
        context=content.strip(),
        category="chat",
        tags=[role]
    )
    return "OK"


# ─── ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР И СЕРВЕР ────────────────────────────────────────
_engine = MemoryEngine(MEMORY_DB_PATH)
server = BaseMCPServer("memory-engine", "4.5")

# Регистрация инструментов
server.register_tool("mem_add", {
    "description": "Add entry to persistent memory",
    "inputSchema": {"type": "object", "properties": {
        "op": {"type": "string"}, "paths": {"type": ["string", "object"]},
        "status": {"type": "string"}, "dialog": {"type": "string"},
        "meta": {"type": "object"}, "context": {"type": "string"},
        "related": {"type": "array", "items": {"type": "string"}}
    }, "required": ["op", "paths", "status"]}
}, lambda **kw: _engine.add(**kw))

server.register_tool("mem_query", {
    "description": "Query persistent memory",
    "inputSchema": {"type": "object", "properties": {
        "dialog": {"type": "string"}, "op": {"type": "string"},
        "path": {"type": "string"}, "category": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "ext": {"type": "string"}, "hours": {"type": "integer"},
        "limit": {"type": "integer", "default": 20}
    }}
}, lambda **kw: _engine.query(**kw))

server.register_tool("mem_thread", {
    "description": "Get conversation thread",
    "inputSchema": {"type": "object", "properties": {
        "dialog": {"type": "string"}, "limit": {"type": "integer", "default": 100}
    }}
}, lambda **kw: _engine.get_thread(**kw))

server.register_tool("mem_snapshot", {
    "description": "Save state snapshot",
    "inputSchema": {"type": "object", "properties": {
        "state": {"type": "object"}, "dialog": {"type": "string"}, "note": {"type": "string"}
    }, "required": ["state"]}
}, lambda **kw: _engine.save_snapshot(**kw))

server.register_tool("mem_get_snapshot", {
    "description": "Retrieve latest snapshot",
    "inputSchema": {"type": "object", "properties": {"dialog": {"type": "string"}}}
}, lambda **kw: _engine.get_snapshot(**kw))

server.register_tool("mem_stats", {
    "description": "Engine statistics",
    "inputSchema": {"type": "object"}
}, lambda **kw: _engine.get_stats())

server.register_tool("mem_clear", {
    "description": "Clear all memory",
    "inputSchema": {"type": "object", "properties": {"dry_run": {"type": "boolean", "default": False}}}
}, lambda **kw: _engine.clear_all(kw.get('dry_run', False)))

server.register_tool("mem_list_dialogs", {
    "description": "List all dialog IDs in memory with entry counts and timestamps",
    "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 50}}}
}, lambda **kw: _engine.list_dialogs(kw.get('limit', 50)))

server.register_tool("mem_search_archive", {
    "description": "Поиск в долгосрочном архиве",
    "inputSchema": {
        "type": "object",
        "properties": {
            "dialog": {"type": "string"},
            "op": {"type": "string"},
            "path": {"type": "string"},
            "category": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "hours": {"type": "integer"},
            "limit": {"type": "integer", "default": 100}
        }
    }
}, lambda **kw: _engine.search_archive(**kw))

server.register_tool("mem_restore_from_archive", {
    "description": "Восстановить одну запись из архива",
    "inputSchema": {
        "type": "object",
        "properties": {
            "entry_id": {"type": "string"},
            "target_dialog": {"type": "string"}
        },
        "required": ["entry_id"]
    }
}, lambda **kw: _engine.restore_from_archive(kw["entry_id"], kw.get("target_dialog")))

server.register_tool("mem_restore_dialog", {
    "description": "Восстановить последние N сообщений диалога из архива в активную память",
    "inputSchema": {
        "type": "object",
        "properties": {
            "dialog_id": {"type": "string"},
            "limit": {"type": "integer", "default": 50}
        },
        "required": ["dialog_id"]
    }
}, lambda **kw: _engine.restore_dialog_from_archive(kw["dialog_id"], kw.get("limit", 50)))

server.register_tool("mem_purge_archive", {
    "description": "Удалить из архива записи старше N дней (по умолчанию 730)",
    "inputSchema": {
        "type": "object",
        "properties": {
            "older_than_days": {"type": "integer", "default": 730}
        }
    }
}, lambda **kw: _engine.purge_archive(kw.get("older_than_days", 730)))

server.register_tool("mem_optimize", {
    "description": "Запустить VACUUM и очистку кеша чанков",
    "inputSchema": {"type": "object", "properties": {}}
}, lambda **kw: _engine.optimize_database())

server.register_tool("mem_archive_stats", {
    "description": "Статистика архива",
    "inputSchema": {"type": "object", "properties": {}}
}, lambda **kw: _engine.archive_stats())

# ─── ИСПРАВЛЕННЫЙ ИНСТРУМЕНТ ДЛЯ ЛОГИРОВАНИЯ ДИАЛОГА ─────────────────────
server.register_tool("log_conversation", {
    "description": "Сохранить сообщение диалога (user/assistant) в память. Вызывай перед каждым своим ответом и, если возможно, перед вопросом пользователя.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "role": {"type": "string", "enum": ["user", "assistant"]},
            "content": {"type": "string"},
            "dialog_id": {"type": "string"}
        },
        "required": ["role", "content"]
    }
}, log_conversation)

if __name__ == "__main__":
    server.run()