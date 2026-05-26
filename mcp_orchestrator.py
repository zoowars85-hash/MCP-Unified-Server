#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Orchestrator v3.2 (Context-Isolated + !command syntax + caching)
Natural language entry point with dynamic tool resolution, bounded fallback,
explicit command syntax (!tool key=value ...), and optional plan caching.
"""
import os
import re
import time
import json
import hashlib
import importlib
from typing import List, Dict, Any, Optional, Tuple
from mcp_shared import (
    _log, BaseMCPServer, conversation_memory, dialog_ctx
)

# ─── Configuration ──────────────────────────────────────────────────────────
CACHE_ENABLED = os.environ.get("MCP_ORCHESTRATOR_CACHE", "true").lower() == "true"
CACHE_TTL_SEC = int(os.environ.get("MCP_ORCHESTRATOR_CACHE_TTL", "3600"))  # 1 hour
MAX_CACHE_ENTRIES = int(os.environ.get("MCP_ORCHESTRATOR_CACHE_SIZE", "100"))

# ─── Command parser for !syntax ─────────────────────────────────────────────
# Pattern: !command_name arg1="value with spaces" arg2=simple value arg3=123
# Supports: key=value (without quotes), key="quoted value", key='single quoted'
COMMAND_PATTERN = re.compile(
    r'^[!@]([a-zA-Z_][a-zA-Z0-9_]*)\s+(.*)$',
    re.IGNORECASE
)
ARG_PATTERN = re.compile(
    r'([a-zA-Z_][a-zA-Z0-9_-]*)=("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|(?:[^\s]+))',
    re.UNICODE
)

def parse_command(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Parse !command or @command.
    Returns (tool_name, args_dict) or None if not a command.
    Example: !search_files path="D:\\Downloads" pattern="*.pdf" max_files=50
    """
    text = text.strip()
    if not text or text[0] not in ('!', '@'):
        return None
    m = COMMAND_PATTERN.match(text)
    if not m:
        return None
    tool_name = m.group(1)
    args_str = m.group(2).strip()
    if not args_str:
        return tool_name, {}
    
    args = {}
    for match in ARG_PATTERN.finditer(args_str):
        key = match.group(1)
        value = match.group(2)
        # Remove quotes if present
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1].replace('\\"', '"')
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("\\'", "'")
        # Try to convert numeric values
        if value.isdigit():
            value = int(value)
        elif value.replace('.', '', 1).isdigit() and value.count('.') == 1:
            value = float(value)
        elif value.lower() in ('true', 'false'):
            value = value.lower() == 'true'
        args[key] = value
    return tool_name, args

# ─── Plan cache ─────────────────────────────────────────────────────────────
class PlanCache:
    def __init__(self):
        self._cache = {}  # key -> (plan, timestamp)
    
    def _make_key(self, query: str, dialog_id: str, context_hash: str = "") -> str:
        combined = f"{dialog_id}:{context_hash}:{query}"
        return hashlib.md5(combined.encode('utf-8')).hexdigest()
    
    def get(self, query: str, dialog_id: str, context_hash: str = "") -> Optional[Dict]:
        if not CACHE_ENABLED:
            return None
        key = self._make_key(query, dialog_id, context_hash)
        entry = self._cache.get(key)
        if entry:
            plan, ts = entry
            if time.time() - ts < CACHE_TTL_SEC:
                return plan
            else:
                del self._cache[key]
        return None
    
    def set(self, query: str, dialog_id: str, plan: Dict, context_hash: str = ""):
        if not CACHE_ENABLED:
            return
        key = self._make_key(query, dialog_id, context_hash)
        self._cache[key] = (plan, time.time())
        # Trim cache if too large
        if len(self._cache) > MAX_CACHE_ENTRIES:
            # Remove oldest entries
            sorted_items = sorted(self._cache.items(), key=lambda x: x[1][1])
            for k, _ in sorted_items[:MAX_CACHE_ENTRIES // 2]:
                del self._cache[k]
    
    def clear(self, dialog_id: str = None):
        if dialog_id:
            # Remove all entries for a specific dialog (not implemented efficiently)
            to_delete = [k for k, v in self._cache.items() if k.startswith(hashlib.md5(f"{dialog_id}:".encode()).hexdigest()[:8])]
            for k in to_delete:
                del self._cache[k]
        else:
            self._cache.clear()

_plan_cache = PlanCache()

# ─── Dynamic Tool Registry ────────────────────────────────────────────────
def _resolve_tool(module_name: str, func_name: str):
    """Lazy-resolve tool function to support graceful degradation."""
    try:
        mod = importlib.import_module(module_name)
        return getattr(mod, func_name, None)
    except Exception as e:
        _log(f"[Orchestrator] Failed to load {module_name}.{func_name}: {e}")
        return None

class TaskPlanner:
    """Rule-based planner with bounded fallback and context-aware execution."""
    def __init__(self):
        # Dynamic resolution replaces hardcoded imports
        self.tools = {
            "search_files": _resolve_tool("mcp_fs_search", "search_files"),
            "find_duplicates": _resolve_tool("mcp_fs_search", "find_duplicates"),
            "batch_move": _resolve_tool("mcp_fs_batch", "batch_move"),
            "analyze_directory": _resolve_tool("mcp_fs_search", "analyze_directory"),
            "check_consistency": _resolve_tool("logic_verifier_server", "check_consistency"),
            "smart_search": _resolve_tool("mcp_smart_search", "smart_search"),
            # Add more as needed
        }
        # Blacklist for dangerous tools that cannot be called via !command
        self.command_blacklist = {"delete_all", "format_disk", "shutdown_system"}  # placeholder

    def is_allowed_command(self, tool_name: str) -> bool:
        return tool_name not in self.command_blacklist

    def parse_intent(self, query: str) -> Dict:
        """Analyze query to determine intent and required steps."""
        query_lower = query.lower()
        plan = {"intent": "unknown", "steps": [], "params": {}, "confidence": 0.0}

        # ─── Search intent detection ─────────────────────────────────
        search_keywords = ["найди", "поиск", "ищи", "какая информация", "найти", "search", "find"]
        if any(kw in query_lower for kw in search_keywords):
            plan["intent"] = "search"
            plan["params"]["query"] = query
            plan["steps"].append({
                "tool": "smart_search",
                "args": {"query": query, "sources": ["web", "kb", "memory"], "limit": 5}
            })
            plan["confidence"] = 0.9
        elif "duplicate" in query_lower or "dupes" in query_lower:
            plan["intent"] = "find_duplicates"
            plan["params"]["path"] = self._extract_path(query) or "."
            plan["steps"].append({"tool": "find_duplicates", "args": {"path": plan["params"]["path"]}})
            plan["confidence"] = 0.9
        elif "clean" in query_lower or "delete" in query_lower or "remove" in query_lower:
            plan["intent"] = "cleanup"
            plan["params"]["path"] = self._extract_path(query) or "."
            plan["steps"].append({"tool": "analyze_directory", "args": {"path": plan["params"]["path"]}})
            plan["confidence"] = 0.8
        elif "move" in query_lower or "organize" in query_lower or "sort" in query_lower:
            plan["intent"] = "organize"
            plan["params"]["source"] = self._extract_path(query) or "."
            plan["steps"].append({"tool": "search_files", "args": {"path": plan["params"]["source"], "pattern": "*"}})
            plan["confidence"] = 0.85
        else:
            plan["intent"] = "general_search"
            plan["params"]["query"] = query
            plan["steps"].append({"tool": "search_files", "args": {"path": ".", "pattern": f"*{query}*"}})
            plan["confidence"] = 0.6
        return plan

    def refine_plan(self, original_query: str, error_context: str) -> Dict:
        """Fallback planning: Adjust plan based on previous failure."""
        _log(f"[Orchestrator] Refining plan due to: {error_context}")
        if "validation_failed" in error_context.lower():
            return {
                "intent": "investigate",
                "steps": [{"tool": "search_files", "args": {"path": ".", "pattern": "*"}}],
                "params": {},
                "confidence": 0.5
            }
        return self.parse_intent(original_query)

    def _extract_path(self, text: str) -> Optional[str]:
        """Extract Windows paths from text."""
        match = re.search(r'[A-Z]:\\(?:[^\\/:*?"<>\r\n]+\\)*[^\\/:*?"<>\r\n]*', text)
        return match.group(0) if match else None

    def execute_plan(self, plan: Dict, depth: int = 0) -> Dict:
        """Execute planned steps with bounded fallback recursion."""
        results = []
        context = {}
        start_time = time.time()

        for i, step in enumerate(plan["steps"]):
            tool_name = step["tool"]
            args = step["args"]
            func = self.tools.get(tool_name)

            if not func:
                _log(f"[Orchestrator] Tool '{tool_name}' unavailable")
                results.append({"step": i + 1, "tool": tool_name, "error": "Tool not loaded"})
                continue

            try:
                res = func(**args)
                results.append({"step": i + 1, "tool": tool_name, "status": "success", "summary": str(res)[:200]})
                context[tool_name] = res
            except Exception as e:
                error_msg = str(e)
                _log(f"[Orchestrator] Error in step {i+1}: {error_msg}")

                if depth < 1 and plan["confidence"] > 0.4:
                    _log("[Orchestrator] Triggering bounded fallback (depth 1)")
                    new_plan = self.refine_plan(context.get("query", ""), error_msg)
                    fallback_res = self.execute_plan(new_plan, depth=depth + 1)
                    results.append({"step": i + 1, "tool": tool_name, "fallback": fallback_res})
                    break
                else:
                    results.append({"step": i + 1, "tool": tool_name, "error": error_msg})
                    break

        elapsed = time.time() - start_time
        return {
            "status": "completed",
            "plan": plan,
            "results": results,
            "context_summary": {k: type(v).__name__ for k, v in context.items()},
            "elapsed_sec": round(elapsed, 2)
        }

def _get_context_hash(dialog_id: str) -> str:
    """Generate a simple context hash based on recent memory entries."""
    try:
        recent = conversation_memory.query(dialog=dialog_id, limit=5, hours=1)
        if recent:
            combined = " ".join(str(r.get("context", "")) for r in recent)
            return hashlib.md5(combined.encode('utf-8')).hexdigest()[:8]
    except Exception:
        pass
    return ""

def run_task(natural_language_query: str) -> Dict:
    """Main entry point: supports !command and caches plans."""
    dialog_id = dialog_ctx.get()
    planner = TaskPlanner()
    
    # ─── Step 1: Check for explicit command syntax ────────────────────────
    cmd_result = parse_command(natural_language_query)
    if cmd_result:
        tool_name, args = cmd_result
        if not planner.is_allowed_command(tool_name):
            return {
                "status": "error",
                "message": f"Tool '{tool_name}' is not allowed to be called via !command (blacklisted)",
                "dialog_id": dialog_id,
                "query": natural_language_query
            }
        # Resolve tool function
        # We need to find the tool in the unified server's handlers
        # Since we don't have direct access to unified._handlers here, we use a workaround:
        # The tool must be registered in planner.tools or we try to import dynamically.
        # For simplicity, we rely on planner.tools (should be extended).
        # Better approach: pass unified instance to run_task, but that requires refactoring.
        # We'll implement a dynamic resolver that searches known modules.
        func = planner.tools.get(tool_name)
        if not func:
            # Attempt to import from common modules
            for module_name in ["mcp_fs_search", "mcp_fs_batch", "mcp_fs_operations", "knowledge_base_server",
                                "logic_verifier_server", "mcp_smart_search", "mcp_calendar", "mcp_db_client"]:
                try:
                    mod = importlib.import_module(module_name)
                    func = getattr(mod, tool_name, None)
                    if func:
                        break
                except ImportError:
                    continue
        if not func:
            return {
                "status": "error",
                "message": f"Tool '{tool_name}' not found. Use 'help' to list available tools.",
                "dialog_id": dialog_id
            }
        try:
            result = func(**args)
            # Log to memory
            conversation_memory.add(
                op=f"command_{tool_name}",
                paths={"args": args},
                status="success",
                dialog=dialog_id,
                context=f"Direct command: {tool_name} with {len(args)} args"
            )
            return {
                "status": "command_executed",
                "tool": tool_name,
                "result": result,
                "dialog_id": dialog_id
            }
        except Exception as e:
            conversation_memory.add(
                op=f"command_{tool_name}",
                paths={"error": str(e)},
                status="error",
                dialog=dialog_id,
                context=f"Command failed: {e}"
            )
            return {
                "status": "error",
                "tool": tool_name,
                "error": str(e),
                "dialog_id": dialog_id
            }
    
    # ─── Step 2: Natural language processing with caching ─────────────────
    context_hash = _get_context_hash(dialog_id)
    cached_plan = _plan_cache.get(natural_language_query, dialog_id, context_hash)
    if cached_plan:
        _log(f"[Orchestrator] Using cached plan for: {natural_language_query[:50]}")
        plan = cached_plan
    else:
        plan = planner.parse_intent(natural_language_query)
        _plan_cache.set(natural_language_query, dialog_id, plan, context_hash)
    
    plan["params"]["original_query"] = natural_language_query
    
    # Context check (existing)
    ctx_check = _check_context_history(plan["intent"], plan["params"])
    if ctx_check.get("requires_confirmation"):
        _log(f"[Orchestrator] Context warning: {ctx_check['warning']}")
    
    execution = planner.execute_plan(plan, depth=0)
    
    conversation_memory.add(
        op=f"orchestrate_{plan['intent']}",
        paths={"query": natural_language_query},
        status=execution["status"],
        dialog=dialog_id,
        context=f"Executed plan for '{natural_language_query}'. Intent: {plan['intent']}"
    )
    
    return {
        "query": natural_language_query,
        "dialog_id": dialog_id,
        "intent": plan["intent"],
        "execution": execution,
        "context_warning": ctx_check if ctx_check.get("requires_confirmation") else None
    }

def _check_context_history(intent: str, params: Dict) -> Dict:
    """Check memory for similar recent operations to avoid redundancy."""
    dialog_id = dialog_ctx.get()
    history = conversation_memory.query(
        op=f"orchestrate_{intent}",
        hours=1,
        limit=1,
        dialog=dialog_id
    )
    if history:
        last_op = history[0]
        if str(params.get("path")) in str(last_op.get("paths", {})):
            return {
                "warning": "Similar operation performed recently",
                "last_execution": last_op["ts"],
                "requires_confirmation": True
            }
    return {"requires_confirmation": False}

def clear_plan_cache(dialog_id: str = None) -> Dict:
    """Clear the orchestration plan cache."""
    _plan_cache.clear(dialog_id)
    return {"status": "cleared", "dialog_id": dialog_id or "all"}

def orchestrator_stats() -> Dict:
    """Return cache statistics."""
    return {
        "cache_enabled": CACHE_ENABLED,
        "cache_entries": len(_plan_cache._cache),
        "cache_ttl_sec": CACHE_TTL_SEC,
        "max_cache_entries": MAX_CACHE_ENTRIES
    }

# ─── Server Setup ────────────────────────────────────────────────────────────
server = BaseMCPServer("orchestrator", "3.2")
server.register_tool("run_task", {
    "description": "Execute a complex task described in natural language. Supports !command syntax for direct tool calls.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "natural_language_query": {
                "type": "string",
                "description": "e.g., 'Find duplicates in D:\\Downloads' or '!search_files path=D:\\Downloads pattern=*.pdf'"
            }
        },
        "required": ["natural_language_query"]
    }
}, lambda **kw: run_task(kw["natural_language_query"]))

server.register_tool("clear_plan_cache", {
    "description": "Clear the orchestration plan cache (optional per dialog)",
    "inputSchema": {
        "type": "object",
        "properties": {"dialog_id": {"type": "string"}}
    }
}, lambda **kw: clear_plan_cache(kw.get("dialog_id")))

server.register_tool("orchestrator_stats", {
    "description": "Get cache and orchestrator statistics",
    "inputSchema": {"type": "object", "properties": {}}
}, lambda **kw: orchestrator_stats())

if __name__ == "__main__":
    server.run()