#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP RAG Engine v1.0 – семантический индекс книг и файлов
"""
import os
import json
import hashlib
from pathlib import Path
from typing import List, Dict, Optional, Any
import threading

# Векторная БД
import chromadb
from chromadb.config import Settings

# Эмбеддеры
from sentence_transformers import SentenceTransformer

# Извлечение текста из разных форматов
import pypdf
import docx
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

# Общие модули MCP
from mcp_shared import (
    _log, BaseMCPServer, dialog_ctx, conversation_memory,
    normalize_path, _ensure_allowed, query_llm
)

# ─── Конфигурация ──────────────────────────────────────────────────────────
RAG_DB_PATH = os.environ.get("MCP_RAG_DB_PATH", "./mcp_rag_db")
CHUNK_SIZE = int(os.environ.get("MCP_RAG_CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(os.environ.get("MCP_RAG_CHUNK_OVERLAP", "100"))
EMBEDDING_MODEL = os.environ.get("MCP_RAG_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
TOP_K = int(os.environ.get("MCP_RAG_TOP_K", "5"))

# Глобальный экземпляр клиента Chroma (с поддержкой потоков)
_chroma_client = None
_embedder = None
_collections = {}  # name -> collection
_collection_lock = threading.Lock()

def _get_client() -> chromadb.ClientAPI:
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=RAG_DB_PATH)
    return _chroma_client

def _get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        _log(f"[RAG] Loading embedding model '{EMBEDDING_MODEL}'...")
        _embedder = SentenceTransformer(EMBEDDING_MODEL)
        _log("[RAG] Embedder ready")
    return _embedder

# ─── Извлечение текста из файлов ──────────────────────────────────────────
def extract_text(file_path: Path) -> str:
    """Извлекает текст из PDF, DOCX, EPUB, TXT, MD."""
    ext = file_path.suffix.lower()
    if ext == '.pdf':
        text = []
        with open(file_path, 'rb') as f:
            reader = pypdf.PdfReader(f)
            for page in reader.pages:
                if page_text := page.extract_text():
                    text.append(page_text)
        return '\n'.join(text)
    elif ext == '.docx':
        doc = docx.Document(file_path)
        return '\n'.join(p.text for p in doc.paragraphs)
    elif ext == '.epub':
        book = epub.read_epub(file_path)
        text = []
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                soup = BeautifulSoup(item.get_content(), 'html.parser')
                text.append(soup.get_text())
        return '\n'.join(text)
    elif ext in ('.txt', '.md'):
        return file_path.read_text(encoding='utf-8', errors='replace')
    else:
        return ""

def chunk_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    """Разбивает текст на перекрывающиеся фрагменты по предложениям/абзацам."""
    if not text:
        return []
    
    # Простая эвристика: разбиваем по абзацам
    paragraphs = text.split('\n')
    chunks = []
    current = []
    current_len = 0
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        
        para_len = len(para)
        if current_len + para_len > chunk_size and current:
            chunks.append('\n'.join(current))
            # keep overlap
            overlap_text = []
            overlap_len = 0
            for p in reversed(current):
                if overlap_len + len(p) <= overlap:
                    overlap_text.insert(0, p)
                    overlap_len += len(p)
                else:
                    break
            current = overlap_text
            current_len = overlap_len
            
        current.append(para)
        current_len += para_len
        
    if current:
        chunks.append('\n'.join(current))
        
    return chunks

# ─── Индексация папки ──────────────────────────────────────────────────────
def rag_index_folder(folder_path: str, collection_name: str = "default",
                     force_reindex: bool = False) -> Dict:
    """Рекурсивно индексирует все поддерживаемые файлы в папке."""
    dialog_id = dialog_ctx.get()
    root = Path(normalize_path(folder_path))
    try:
        _ensure_allowed(root, "rag_index_folder")
    except PermissionError as e:
        return {"status": "error", "message": str(e)}
        
    if not root.is_dir():
        return {"status": "error", "message": "Not a directory"}
        
    client = _get_client()
    embedder = _get_embedder()
    
    with _collection_lock:
        try:
            collection = client.get_collection(collection_name)
            if force_reindex:
                client.delete_collection(collection_name)
                collection = client.create_collection(collection_name)
        except Exception:
            collection = client.create_collection(collection_name)
            
    supported_ext = {'.pdf', '.epub', '.docx', '.txt', '.md'}
    files_to_index = []
    for f in root.rglob("*"):
        if f.is_file() and f.suffix.lower() in supported_ext:
            files_to_index.append(f)
            
    total = len(files_to_index)
    if total == 0:
        return {"status": "error", "message": "No supported files found"}
        
    indexed_chunks = 0
    errors = 0
    chunk_id_counter = 0
    
    for i, file_path in enumerate(files_to_index):
        try:
            raw_text = extract_text(file_path)
            if not raw_text:
                errors += 1
                continue
                
            chunks = chunk_text(raw_text, CHUNK_SIZE, CHUNK_OVERLAP)
            for chunk in chunks:
                # Генерируем уникальный ID
                chunk_id = hashlib.md5(
                    f"{file_path}_{chunk_id_counter}".encode()
                ).hexdigest()[:16]
                embedding = embedder.encode(chunk).tolist()
                metadata = {
                    "source": str(file_path),
                    "filename": file_path.name,
                    "chunk_index": chunk_id_counter,
                    "total_chunks": len(chunks)
                }
                collection.add(
                    ids=[chunk_id],
                    embeddings=[embedding],
                    metadatas=[metadata],
                    documents=[chunk]
                )
                chunk_id_counter += 1
                indexed_chunks += 1
        except Exception as e:
            _log(f"[RAG] Error indexing {file_path}: {e}")
            errors += 1
            
        # Прогресс в диалог (если включён verbose)
        if (i + 1) % 10 == 0:
            _log(f"[RAG] Indexed {i+1}/{total} files, {indexed_chunks} chunks")
            
    conversation_memory.add(
        op="rag_index_folder",
        paths={"folder": str(root), "collection": collection_name},
        status="completed",
        dialog=dialog_id,
        context=f"Indexed {indexed_chunks} chunks from {total - errors} files"
    )
    
    return {
        "status": "success",
        "collection": collection_name,
        "files_scanned": total,
        "files_processed": total - errors,
        "chunks_created": indexed_chunks,
        "errors": errors
    }

# ─── Поиск по RAG ──────────────────────────────────────────────────────────
def rag_search(query: str, collection_name: str = "default",
               top_k: int = TOP_K) -> Dict:
    """Возвращает top_k наиболее релевантных фрагментов."""
    if not query.strip():
        return {"status": "error", "message": "Empty query"}
        
    client = _get_client()
    embedder = _get_embedder()
    
    try:
        collection = client.get_collection(collection_name)
    except Exception:
        return {"status": "error", "message": f"Collection '{collection_name}' not found"}
        
    query_embedding = embedder.encode(query).tolist()
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"]
    )
    
    # Формируем ответ
    chunks = []
    if results["ids"] and results["ids"][0]:
        for i, doc_id in enumerate(results["ids"][0]):
            chunks.append({
                "id": doc_id,
                "text": results["documents"][0][i],
                "source": results["metadatas"][0][i].get("source", "unknown"),
                "filename": results["metadatas"][0][i].get("filename", "unknown"),
                "distance": results["distances"][0][i]
            })
            
    return {
        "status": "success",
        "query": query,
        "collection": collection_name,
        "chunks": chunks,
        "count": len(chunks)
    }

# ─── Генерация ответа через LLM ────────────────────────────────────────────
def rag_ask(question: str, collection_name: str = "default",
            top_k: int = TOP_K, model: Optional[str] = None) -> Dict:
    """Ищет релевантные фрагменты и генерирует ответ через LLM."""
    search_result = rag_search(question, collection_name, top_k)
    if search_result.get("status") != "success":
        return search_result
        
    chunks = search_result["chunks"]
    if not chunks:
        return {
            "status": "no_results",
            "question": question,
            "answer": "Не найдено релевантных фрагментов в индексе."
        }
        
    # Собираем контекст
    context_parts = []
    for i, ch in enumerate(chunks, 1):
        context_parts.append(f"[Фрагмент {i} из {ch['filename']}]\n{ch['text']}")
    context = "\n---\n".join(context_parts)
    
    prompt = f"""Ты — помощник, отвечающий на вопросы, используя только предоставленный контекст.
Если ответа нет в контексте, скажи: «В индексированных книгах нет информации об этом».

ВОПРОС: {question}

КОНТЕКСТ:
{context}

ОТВЕТ:"""
    
    llm_response = query_llm(prompt, model=model)
    if not llm_response:
        llm_response = "Не удалось получить ответ от LLM. Проверьте LLM_ENDPOINT."
        
    dialog_id = dialog_ctx.get()
    conversation_memory.add(
        op="rag_ask",
        paths={"question": question[:100], "collection": collection_name},
        status="answered",
        dialog=dialog_id,
        context=f"Used {len(chunks)} chunks, answer length {len(llm_response)}"
    )
    
    return {
        "status": "success",
        "question": question,
        "answer": llm_response,
        "used_chunks": len(chunks),
        "collection": collection_name,
        "chunks": chunks  # можно убрать, если ответ и так длинный
    }

# ─── Статистика и управление коллекциями ────────────────────────────────────
def rag_list_collections() -> Dict:
    client = _get_client()
    collections = client.list_collections()
    return {"collections": [c.name for c in collections], "count": len(collections)}

def rag_delete_collection(collection_name: str) -> Dict:
    client = _get_client()
    try:
        client.delete_collection(collection_name)
        return {"status": "deleted", "collection": collection_name}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def rag_stats(collection_name: str = "default") -> Dict:
    client = _get_client()
    try:
        coll = client.get_collection(collection_name)
        count = coll.count()
        # получить пример метаданных
        sample = coll.get(limit=1, include=["metadatas"])
        total_files = set()
        if sample["metadatas"]:
            total_files = {m["source"] for m in sample["metadatas"]}
        return {
            "collection": collection_name,
            "chunks": count,
            "unique_files": len(total_files),
            "embedding_model": EMBEDDING_MODEL,
            "chunk_size": CHUNK_SIZE,
            "db_path": RAG_DB_PATH
        }
    except Exception:
        return {"error": f"Collection '{collection_name}' not found"}

# ─── Регистрация инструментов ───────────────────────────────────────────────
def register_tools(server: BaseMCPServer):
    server.register_tool("rag_index_folder", {
        "description": "Индексировать папку с книгами/файлами в векторную БД (RAG)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "folder_path": {"type": "string"},
                "collection_name": {"type": "string", "default": "default"},
                "force_reindex": {"type": "boolean", "default": False}
            },
            "required": ["folder_path"]
        }
    }, lambda **kw: rag_index_folder(kw["folder_path"], kw.get("collection_name", "default"), kw.get("force_reindex", False)))
    
    server.register_tool("rag_search", {
        "description": "Поиск релевантных фрагментов в индексе по ключевым словам/смыслу",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "collection_name": {"type": "string", "default": "default"},
                "top_k": {"type": "integer", "default": TOP_K}
            },
            "required": ["query"]
        }
    }, lambda **kw: rag_search(kw["query"], kw.get("collection_name", "default"), kw.get("top_k", TOP_K)))
    
    server.register_tool("rag_ask", {
        "description": "Задать вопрос, ответ будет сгенерирован LLM на основе найденных фрагментов",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "collection_name": {"type": "string", "default": "default"},
                "top_k": {"type": "integer", "default": TOP_K},
                "model": {"type": "string"}
            },
            "required": ["question"]
        }
    }, lambda **kw: rag_ask(kw["question"], kw.get("collection_name", "default"), kw.get("top_k", TOP_K), kw.get("model")))
    
    server.register_tool("rag_stats", {
        "description": "Статистика индекса (количество чанков, файлов, модель)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection_name": {"type": "string", "default": "default"}
            }
        }
    }, lambda **kw: rag_stats(kw.get("collection_name", "default")))
    
    server.register_tool("rag_list_collections", {
        "description": "Список всех коллекций в векторной БД",
        "inputSchema": {"type": "object", "properties": {}}
    }, lambda **kw: rag_list_collections())
    
    server.register_tool("rag_delete_collection", {
        "description": "Удалить коллекцию (освободить место)",
        "inputSchema": {
            "type": "object",
            "properties": {"collection_name": {"type": "string"}},
            "required": ["collection_name"]
        }
    }, lambda **kw: rag_delete_collection(kw["collection_name"]))

# ─── Плагин MCP ─────────────────────────────────────────────────────────────
__mcp_plugin__ = {
    "name": "rag-engine",
    "version": "1.0",
    "description": "Векторный поиск и RAG для книг и документов",
    "dependencies": ["sentence_transformers", "chromadb", "pypdf", "docx", "ebooklib", "bs4"],
    "on_load": lambda: _log("[RAG] Engine loaded. Use rag_index_folder to index your books."),
    "on_unload": lambda: _log("[RAG] Engine unloaded.")
}

if __name__ == "__main__":
    server = BaseMCPServer("rag-engine", "1.0")
    register_tools(server)
    server.run()