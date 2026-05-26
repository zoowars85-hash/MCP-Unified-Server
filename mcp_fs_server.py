#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Unified Filesystem Server v3.4 (Plugin-Ready + Task Manager + Help + Export + Shell)
Добавлена поддержка: асинхронные задачи, !command, справка, экспорт диалога, безопасный шелл,
а также экспорт всех чатов LM Studio.
"""
import sys
import os
import atexit
import importlib
import importlib.util
from pathlib import Path

from mcp_shared import BaseMCPServer, _log, dialog_ctx
from mcp_verbose import set_verbose as set_dialog_verbose, is_verbose as get_dialog_verbose

# --- Новые модули ---
from mcp_task_manager import register_tasks
from mcp_help import register_help_tool
from mcp_export_dialog import register_export_tool
from mcp_shell import register_shell_tool
from mcp_export_lmstudio import register_export_lmstudio_tool   # <--- ДОБАВЛЕНО

# --- Список основных модулей (с добавленным dialog_manager и mcp_rag_engine) ---
SERVER_MODULES = [
    "mcp_fs_operations",
    "mcp_fs_search",
    "mcp_fs_batch",
    "mcp_fs_trash",
    "mcp_fs_sync",
    "mcp_fs_watcher",
    "mcp_fs_archives",
    "mcp_fs_cloud",
    "mcp_fs_organizer",
    "mcp_fs_versioning",
    "mcp_fs_indexer",
    "mcp_fs_discovery",
    "mcp_fs_scripts",
    "mcp_fs_media",
    "mcp_fs_analyzer",
    "mcp_orchestrator",
    "mcp_admin_server",
    "mcp_event_bus",
    "knowledge_base_server",
    "logic_verifier_server",
    "context_manager_server",
    "mcp_logic_optimizer",
    "code_debugger_server",
    "mcp_memory_engine",
    "mcp_office_reader",
    "mcp_web_reader",
    "mcp_db_client",
    "mcp_calendar",
    "mcp_smart_search",
    "dialog_manager",           # управление диалогами
    "mcp_rag_engine",           # <--- ДОБАВЛЕНО: RAG движок
]

PLUGIN_DIR = Path(__file__).parent / "mcp_plugins"
_loaded_modules = []

MODULE_DEPS = {
    "mcp_fs_search": ["watchdog"],
    "mcp_fs_watcher": ["watchdog"],
    "mcp_fs_media": ["PIL", "mutagen"],
    "mcp_fs_indexer": [],
    "mcp_office_reader": ["docx", "openpyxl", "pptx"],
    "mcp_web_reader": ["requests", "bs4", "feedparser"],
    "mcp_db_client": ["duckdb", "pyodbc"],
    "mcp_calendar": ["icalendar"],
    "mcp_email_client": ["keyring"],
    "code_debugger_server": [],
    "knowledge_base_server": [],
    "mcp_smart_search": [],
    "dialog_manager": [],
    "mcp_rag_engine": ["chromadb", "sentence_transformers", "pypdf", "docx", "ebooklib", "bs4"], # <--- ДОБАВЛЕНО
}

def _check_dependencies(deps: list) -> list:
    missing = []
    for dep in deps:
        pkg = dep.split("==")[0].split(">=")[0].split("<")[0].strip()
        if importlib.util.find_spec(pkg) is None:
            missing.append(pkg)
    return missing

def _copy_tools(from_server: BaseMCPServer, to_server: BaseMCPServer) -> int:
    registered = 0
    if not hasattr(from_server, "tools") or not from_server.tools:
        return 0
    for tool in from_server.tools:
        name = tool["name"]
        if name in to_server._handlers:
            continue
        handler = from_server._handlers.get(name)
        if handler:
            to_server.register_tool(name, tool, handler)
            registered += 1
    return registered

def _load_module(mod_name: str, unified: BaseMCPServer) -> bool:
    optional_deps = MODULE_DEPS.get(mod_name, [])
    if optional_deps:
        missing_opt = _check_dependencies(optional_deps)
        if missing_opt:
            _log(f"[INFO] {mod_name}: optional deps missing → {', '.join(missing_opt)}")
    try:
        mod = importlib.import_module(mod_name)
    except ImportError as e:
        _log(f"[SKIP] {mod_name}: missing dependency → {e}")
        return False
    except Exception as e:
        _log(f"[FAIL] {mod_name}: import error → {e}")
        return False
    return _register_module(mod_name, mod, unified)

def _register_module(mod_name: str, mod: object, unified: BaseMCPServer) -> bool:
    meta = getattr(mod, "__mcp_plugin__", {})
    deps = meta.get("dependencies", [])
    missing = _check_dependencies(deps)
    if missing:
        _log(f"[SKIP] {mod_name}: missing deps → {', '.join(missing)}")
        return False
        
    on_load = meta.get("on_load")
    if callable(on_load):
        try:
            on_load()
        except Exception as e:
            _log(f"[WARN] {mod_name}: on_load failed → {e}")
            
    reg_func = getattr(mod, "register_tools", None)
    if callable(reg_func):
        reg_func(unified)
        _loaded_modules.append((meta, mod))
        return True
    elif hasattr(mod, "server") and mod.server is not None:
        cnt = _copy_tools(mod.server, unified)
        _log(f"[OK] {mod_name} legacy ({cnt} tools)")
        _loaded_modules.append((meta, mod))
        return True
    else:
        _log(f"[WARN] {mod_name}: no register_tools or server")
        return False

def _discover_plugins(unified: BaseMCPServer) -> dict:
    if not PLUGIN_DIR.is_dir():
        PLUGIN_DIR.mkdir(exist_ok=True)
        return {"loaded": 0, "skipped": 0}
        
    loaded, skipped = 0, 0
    for p_file in sorted(PLUGIN_DIR.glob("*.py")):
        if p_file.name.startswith("_"):
            continue
        mod_name = p_file.stem
        spec = importlib.util.spec_from_file_location(mod_name, str(p_file))
        if not spec or not spec.loader:
            skipped += 1
            continue
        try:
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
            if _register_module(mod_name, mod, unified):
                loaded += 1
            else:
                skipped += 1
        except Exception as e:
            _log(f"[FAIL] Plugin {mod_name}: {e}")
            skipped += 1
    return {"loaded": loaded, "skipped": skipped}

def _graceful_shutdown():
    _log("Shutting down MCP Server. Running unload hooks...")
    # Остановка фоновых потоков ConversationMemory (если есть метод)
    try:
        from mcp_shared import conversation_memory
        if hasattr(conversation_memory, '_stop_auto_threads'):
            conversation_memory._stop_auto_threads = True
            _log("ConversationMemory auto-threads stop signal sent.")
    except Exception as e:
        _log(f"[WARN] Failed to stop ConversationMemory threads: {e}")
        
    for meta, mod in reversed(_loaded_modules):
        on_unload = meta.get("on_unload")
        if callable(on_unload):
            try:
                on_unload()
            except Exception as e:
                _log(f"[WARN] Unload hook failed: {e}")
    _log("All cleanup completed. Exiting.")

def print_tools_summary(unified: BaseMCPServer):
    _log("=" * 60)
    _log(f"TOOLS REGISTRY: {len(unified._handlers)} tools available")
    _log("=" * 60)
    from collections import defaultdict
    by_module = defaultdict(list)
    for name in sorted(unified._handlers.keys()):
        handler = unified._handlers[name]
        module = getattr(handler, '__module__', 'unknown')
        by_module[module].append(name)
    for module in sorted(by_module.keys()):
        tools = by_module[module]
        _log(f"  [{module}] ({len(tools)} tools): {', '.join(tools)}")

# Устанавливаем таймауты для LM Studio (долгие операции)
os.environ.setdefault("MCP_SEARCH_TIMEOUT", "3600")
os.environ.setdefault("MCP_ANALYSIS_TIMEOUT", "3600")

def main():
    _log("Initializing MCP Unified Filesystem Server v3.4...")
    unified = BaseMCPServer("filesystem-unified", "3.4")
    
    # Register verbose control tools (using mcp_verbose functions)
    unified.register_tool("set_verbose", {
        "description": "Включить/выключить подробные уведомления о прогрессе (прогресс-бар в чате)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "enable": {"type": "boolean", "default": True},
                "dialog_id": {"type": "string"}
            }
        }
    }, lambda **kw: set_dialog_verbose(kw.get("dialog_id") or dialog_ctx.get(), kw.get("enable", True)) or {"status": "ok"})
    
    unified.register_tool("get_verbose", {
        "description": "Проверить, включены ли подробные уведомления для диалога",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dialog_id": {"type": "string"}
            }
        }
    }, lambda **kw: {"verbose": get_dialog_verbose(kw.get("dialog_id") or dialog_ctx.get())})
    
    # --- Регистрация новых инструментов (до загрузки остальных модулей) ---
    register_help_tool(unified)          # !help
    register_export_tool(unified)        # export_dialog
    register_shell_tool(unified)         # run_shell
    register_tasks(unified)              # task_* (асинхронные задачи)
    register_export_lmstudio_tool(unified)   # <--- ДОБАВЛЕНО: экспорт всех чатов LM Studio
    
    core_loaded, core_skipped = 0, 0
    for mod in SERVER_MODULES:
        if _load_module(mod, unified):
            core_loaded += 1
        else:
            core_skipped += 1
            
    plugin_stats = _discover_plugins(unified)
    total_tools = len(unified._handlers)
    
    _log(f"Server ready: {core_loaded} core + {plugin_stats['loaded']} plugins loaded. {total_tools} tools available.")
    if core_skipped or plugin_stats['skipped']:
        _log(f"Skipped: {core_skipped} core, {plugin_stats['skipped']} plugins")
        
    print_tools_summary(unified)
    atexit.register(_graceful_shutdown)
    
    _log("Listening on STDIO (JSON-RPC 2.0)...")
    unified.run()

if __name__ == "__main__":
    main()