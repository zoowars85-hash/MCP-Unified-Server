#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Database Client v3.1
Secure read-only querying for SQLite, DuckDB, CSV, Parquet, MS Access.
Features SQL injection protection via regex blocklist, forced LIMIT, 
and automatic connection string parsing.
"""
import os
import re
import sqlite3
import csv
from pathlib import Path
from typing import Dict, List, Any, Optional
from mcp_shared import (
    _log, normalize_path, _ensure_allowed,
    BaseMCPServer, conversation_memory, dialog_ctx
)

# ─── Security & Parsing Helpers ─────────────────────────────────────────────
DANGEROUS_SQL_PATTERN = re.compile(
    r'\b(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|TRUNCATE|REPLACE|MERGE|GRANT|REVOKE|EXEC|EXECUTE|XP_)\b',
    re.IGNORECASE | re.MULTILINE
)
MULTI_STMT_PATTERN = re.compile(r';\s*\w+', re.IGNORECASE)

def _parse_connection_string(conn_str: str) -> tuple[str, str]:
    """Parse connection string into (db_type, db_path). Supports URI scheme or direct path."""
    if "://" in conn_str:
        scheme, path = conn_str.split("://", 1)
        return scheme.lower().strip(), path.strip()
    
    # Fallback: infer from file extension
    p = Path(conn_str)
    ext = p.suffix.lower()
    mapping = {
        ".db": "sqlite", ".sqlite": "sqlite", ".sqlite3": "sqlite",
        ".duckdb": "duckdb", ".ddb": "duckdb",
        ".csv": "csv", ".tsv": "csv",
        ".parquet": "parquet", ".pq": "parquet",
        ".accdb": "access", ".mdb": "access"
    }
    return mapping.get(ext, "sqlite"), conn_str

def _validate_sql(query: str) -> Optional[str]:
    """Return error message if query is unsafe, else None."""
    if MULTI_STMT_PATTERN.search(query):
        return "Multiple statements detected. Only single SELECT queries are allowed."
    if DANGEROUS_SQL_PATTERN.search(query):
        return "Query blocked: contains potentially destructive SQL commands (DDL/DML). Only READ/SELECT allowed."
    return None

# ─── Core Tools ─────────────────────────────────────────────────────────────
def query_db(connection_string: str, sql_query: str, limit: int = 1000) -> Dict:
    d_id = dialog_ctx.get()
    db_type, db_path = _parse_connection_string(connection_string)
    p = Path(normalize_path(db_path))
    _ensure_allowed(p, "query_db")
    
    if not p.exists():
        return {"error": f"Database/file not found: {db_path}"}
    
    limit = min(abs(limit), 10000)  # Cap at 10k
    security_error = _validate_sql(sql_query)
    if security_error:
        return {"error": security_error}
    
    # Safely append LIMIT
    clean_sql = sql_query.rstrip(";").strip()
    if not clean_sql.lower().endswith(f"limit {limit}"):
        clean_sql += f" LIMIT {limit}"
        
    results = []
    cols = []
    try:
        if db_type == "sqlite":
            conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(clean_sql)
            cols = [desc[0] for desc in cursor.description] if cursor.description else []
            results = [dict(zip(cols, row)) for row in cursor.fetchall()]
            conn.close()
            
        elif db_type == "duckdb":
            try:
                import duckdb
            except ImportError:
                return {"error": "duckdb not installed. Install with: pip install duckdb"}
            conn = duckdb.connect(str(p), read_only=True)
            res = conn.execute(clean_sql)
            cols = [desc[0] for desc in res.description] if res.description else []
            results = [dict(zip(cols, row)) for row in res.fetchall()]
            conn.close()
            
        elif db_type in ("csv", "parquet"):
            try:
                import duckdb
            except ImportError:
                return {"error": "duckdb not installed. Required for CSV/Parquet querying."}
            conn = duckdb.connect()
            view_query = f"CREATE VIEW temp_data AS SELECT * FROM read_csv_auto('{p}');" if db_type == "csv" else f"CREATE VIEW temp_data AS SELECT * FROM read_parquet('{p}');"
            conn.execute(view_query)
            query_sql = clean_sql.replace("SELECT ", "SELECT * FROM temp_data ", 1) if "FROM" not in clean_sql.upper() else clean_sql
            res = conn.execute(query_sql)
            cols = [desc[0] for desc in res.description] if res.description else []
            results = [dict(zip(cols, row)) for row in res.fetchall()]
            conn.close()
            
        elif db_type == "access":
            try:
                import pyodbc
            except ImportError:
                return {"error": "pyodbc not installed. Install with: pip install pyodbc"}
            odbc_conn = f"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={p};READONLY=True;"
            conn = pyodbc.connect(odbc_conn)
            cursor = conn.cursor()
            cursor.execute(clean_sql)
            cols = [desc[0] for desc in cursor.description] if cursor.description else []
            results = [dict(zip(cols, row)) for row in cursor.fetchall()]
            conn.close()
        else:
            return {"error": f"Unsupported database type: {db_type}"}
            
    except Exception as e:
        return {"error": str(e)}
        
    conversation_memory.add(
        op="query_db", paths={"db": str(p)}, status="success", dialog=d_id,
        context=f"Executed read query, returned {len(results)} rows from {db_type}"
    )
    return {"rows_returned": len(results), "limit_applied": limit, "columns": cols, "data": results}

def list_db_tables(db_path: str, db_type: str = "sqlite") -> Dict:
    p = Path(normalize_path(db_path))
    _ensure_allowed(p, "list_db_tables")
    
    if not p.exists():
        return {"error": f"Database/file not found: {db_path}"}
        
    tables = []
    try:
        if db_type == "sqlite":
            conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
            conn.close()
        elif db_type == "duckdb":
            import duckdb
            conn = duckdb.connect(str(p), read_only=True)
            tables = [row[0] for row in conn.execute("SHOW TABLES;").fetchall()]
            conn.close()
        elif db_type in ("csv", "parquet"):
            tables = [p.name]
        elif db_type == "access":
            import pyodbc
            odbc_conn = f"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={p};READONLY=True;"
            conn = pyodbc.connect(odbc_conn)
            cursor = conn.cursor()
            tables = [row.table_name for row in cursor.tables(tableType='TABLE')]
            conn.close()
        else:
            return {"error": f"Unsupported db_type: {db_type}"}
    except Exception as e:
        return {"error": str(e)}
        
    return {"path": str(p), "db_type": db_type, "tables": tables, "count": len(tables)}

def import_csv_to_sqlite(csv_path: str, table_name: str, db_path: str) -> Dict:
    csv_p = Path(normalize_path(csv_path))
    db_p = Path(normalize_path(db_path))
    _ensure_allowed(csv_p, "import_csv_to_sqlite")
    _ensure_allowed(db_p.parent, "import_csv_to_sqlite")
    
    if not csv_p.is_file():
        return {"error": f"CSV file not found: {csv_path}"}
        
    d_id = dialog_ctx.get()
    try:
        # Try pandas first for performance
        try:
            import pandas as pd
            df = pd.read_csv(csv_p)
            conn = sqlite3.connect(str(db_p))
            df.to_sql(table_name, conn, if_exists="replace", index=False)
            rows = len(df)
            conn.close()
        except ImportError:
            # Fallback to stdlib csv
            rows = 0
            with open(csv_p, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                headers = reader.fieldnames
                if not headers:
                    return {"error": "CSV file is empty or has no headers"}
                    
            conn = sqlite3.connect(str(db_p))
            cols = ", ".join(f'"{h}"' for h in headers)
            placeholders = ", ".join(["?"] * len(headers))
            create_sql = f"CREATE TABLE IF NOT EXISTS {table_name} ({cols} TEXT)"
            conn.execute(create_sql)
            
            with open(csv_p, "r", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                next(reader) # skip header
                for row in reader:
                    conn.execute(f"INSERT INTO {table_name} VALUES ({placeholders})", row)
                    rows += 1
            conn.commit()
            conn.close()
            
        conversation_memory.add(
            op="import_csv_to_sqlite", paths={"src": str(csv_p), "dst": str(db_p)},
            status="success", dialog=d_id, context=f"Imported {rows} rows into table '{table_name}'"
        )
        return {"status": "success", "table_name": table_name, "rows_imported": rows, "db_path": str(db_p)}
    except Exception as e:
        return {"error": str(e)}

# ─── Server Setup ────────────────────────────────────────────────────────────
server = BaseMCPServer("database-client", "3.1")

server.register_tool("query_db", {
    "description": "Execute safe read-only SQL queries against SQLite, DuckDB, CSV, or Parquet files",
    "inputSchema": {
        "type": "object",
        "properties": {
            "connection_string": {"type": "string", "description": "Path or URI (e.g., sqlite://path.db, csv://data.csv)"},
            "sql_query": {"type": "string", "description": "SELECT query only. DDL/DML blocked."},
            "limit": {"type": "integer", "default": 1000}
        },
        "required": ["connection_string", "sql_query"]
    }
}, lambda **kw: query_db(kw["connection_string"], kw["sql_query"], kw.get("limit", 1000)))

server.register_tool("list_db_tables", {
    "description": "List all tables in a database or data file",
    "inputSchema": {
        "type": "object",
        "properties": {
            "db_path": {"type": "string"},
            "db_type": {"type": "string", "enum": ["sqlite", "duckdb", "csv", "parquet", "access"], "default": "sqlite"}
        },
        "required": ["db_path"]
    }
}, lambda **kw: list_db_tables(kw["db_path"], kw.get("db_type", "sqlite")))

server.register_tool("import_csv_to_sqlite", {
    "description": "Import a CSV file into a SQLite database table",
    "inputSchema": {
        "type": "object",
        "properties": {
            "csv_path": {"type": "string"},
            "table_name": {"type": "string"},
            "db_path": {"type": "string"}
        },
        "required": ["csv_path", "table_name", "db_path"]
    }
}, lambda **kw: import_csv_to_sqlite(kw["csv_path"], kw["table_name"], kw["db_path"]))

if __name__ == "__main__":
    server.run()