#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Smart Search v1.0
Unified search across web, knowledge base, and conversation memory using keywords.
Automatically dispatches to appropriate backends.
"""
import json
from typing import List, Dict, Optional
from mcp_shared import (
    _log, BaseMCPServer, conversation_memory, dialog_ctx
)

# ─── Plugin Metadata ─────────────────────────────────────────────────────────
__mcp_plugin__ = {
    "name": "smart-search",
    "version": "1.0.0",
    "description": "Unified keyword search over web, KB, and memory",
    "dependencies": [],
    "on_load": lambda: _log("[smart-search] Loaded. Unified search ready."),
    "on_unload": lambda: _log("[smart-search] Unloaded.")
}

# ─── Core Function ───────────────────────────────────────────────────────────
def smart_search(query: str, sources: Optional[List[str]] = None,
                 limit: int = 5, dialog_id: Optional[str] = None) -> Dict:
    """
    Search for information using keywords across multiple sources.

    Args:
        query: Search keywords (e.g., "Molo 5 BMS 100A charging")
        sources: List of sources to query: "web", "kb", "memory"
                 Default: ["web", "kb", "memory"]
        limit: Max results per source (default 5)
        dialog_id: Optional dialog ID for memory context

    Returns:
        Dict with results grouped by source.
    """
    d_id = dialog_id or dialog_ctx.get()
    if sources is None:
        sources = ["web", "kb", "memory"]

    results = {}
    errors = []

    # 1. Web search
    if "web" in sources:
        try:
            # Try to import web_search from mcp_web_reader
            from mcp_web_reader import web_search
            web_res = web_search(query, max_results=limit)
            if web_res.get("status") == "success":
                results["web"] = {
                    "status": "success",
                    "count": web_res.get("count", 0),
                    "results": web_res.get("results", [])
                }
            else:
                errors.append(f"Web search: {web_res.get('error', 'Unknown error')}")
        except ImportError:
            errors.append("Web search not available: mcp_web_reader module missing")
        except Exception as e:
            errors.append(f"Web search error: {e}")

    # 2. Knowledge base search (notes)
    if "kb" in sources:
        try:
            from knowledge_base_server import search_notes
            kb_res = search_notes(query, limit=limit)
            if kb_res.get("status") == "success":
                results["knowledge_base"] = {
                    "status": "success",
                    "count": len(kb_res.get("results", [])),
                    "results": kb_res.get("results", [])
                }
            else:
                errors.append(f"KB search: {kb_res.get('message', 'Unknown error')}")
        except ImportError:
            errors.append("Knowledge base not available: knowledge_base_server module missing")
        except Exception as e:
            errors.append(f"KB search error: {e}")

    # 3. Memory search (conversation history)
    if "memory" in sources:
        try:
            from context_manager_server import recall_fact
            mem_res = recall_fact(query, store_if_missing=False, dialog_id=d_id)
            if mem_res.get("found"):
                results["memory"] = {
                    "status": "success",
                    "confidence": mem_res.get("confidence"),
                    "fact": mem_res.get("fact"),
                    "source": mem_res.get("source"),
                    "related_count": mem_res.get("related_count", 0)
                }
            else:
                results["memory"] = {"status": "not_found", "message": "No matching fact in memory"}
        except ImportError:
            errors.append("Memory recall not available: context_manager_server module missing")
        except Exception as e:
            errors.append(f"Memory search error: {e}")

    # Log to conversation memory
    conversation_memory.add(
        op="smart_search",
        paths={"query": query, "sources": sources},
        status="success" if results else "partial",
        dialog=d_id,
        context=f"Smart search for '{query}' over {sources}. Found {len(results)} sources with data."
    )

    return {
        "status": "success" if results else "error",
        "query": query,
        "sources_used": sources,
        "results": results,
        "errors": errors if errors else None,
        "dialog_id": d_id
    }

# ─── Server Setup ────────────────────────────────────────────────────────────
server = BaseMCPServer("smart-search", "1.0")

server.register_tool("smart_search", {
    "description": "Unified search across web, knowledge base, and conversation memory using keywords. No URLs needed.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search keywords, e.g., 'Molo 5 BMS specifications'"},
            "sources": {
                "type": "array",
                "items": {"type": "string", "enum": ["web", "kb", "memory"]},
                "default": ["web", "kb", "memory"],
                "description": "Sources to search: web (internet), kb (knowledge base notes), memory (conversation history)"
            },
            "limit": {"type": "integer", "default": 5, "description": "Max results per source"},
            "dialog_id": {"type": "string", "description": "Dialog ID for memory context"}
        },
        "required": ["query"]
    }
}, lambda **kw: smart_search(
    kw["query"],
    kw.get("sources", ["web", "kb", "memory"]),
    kw.get("limit", 5),
    kw.get("dialog_id")
))

if __name__ == "__main__":
    server.run()