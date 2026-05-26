#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Web Reader v4.0 – улучшенное извлечение: текст (trafilatura/readability),
таблицы (pandas / html-table-takeout), изображения, больше источников поиска.
(Исправлены имена зависимостей для корректной загрузки плагина)
"""
import os
import re
import time
import json
import csv
import socket
import ipaddress
import threading
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Any, Set, Tuple
import requests
from requests.exceptions import RequestException
from urllib.robotparser import RobotFileParser

# Улучшенные библиотеки для извлечения контента (опционально)
try:
    import trafilatura
    TRAFILATURA_AVAILABLE = True
except ImportError:
    TRAFILATURA_AVAILABLE = False

try:
    from readability import Document
    READABILITY_AVAILABLE = True
except ImportError:
    READABILITY_AVAILABLE = False

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

try:
    from html_table_takeout import parse_html
    HTML_TABLE_TAKEOUT = True
except ImportError:
    HTML_TABLE_TAKEOUT = False

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    import feedparser
except ImportError:
    feedparser = None

try:
    from playwright.sync_api import sync_playwright
    PW_AVAILABLE = True
except ImportError:
    sync_playwright = None
    PW_AVAILABLE = False

from mcp_shared import (
    _log, normalize_path, _ensure_allowed,
    BaseMCPServer, conversation_memory, dialog_ctx
)

# ИСПРАВЛЕНА СЕКЦИЯ ЗАВИСИМОСТЕЙ
__mcp_plugin__ = {
    "name": "web-reader",
    "version": "4.0.0",
    "description": "Расширенный веб-ридер: текст, таблицы, изображения, поиск по нескольким источникам",
    # Указаны корректные имена модулей (readability вместо readability-lxml, html_table_takeout вместо html-table-takeout)
    # Также оставлены только минимально необходимые для базовой работы; остальные опциональны
    "dependencies": ["requests", "bs4", "feedparser", "playwright", "trafilatura", "readability", "pandas", "html_table_takeout"],
    "on_load": lambda: _log("[web-reader] v4.0 loaded. Enhanced extraction active."),
    "on_unload": lambda: _log("[web-reader] Unloaded.")
}

# Конфигурация
USER_AGENT = "MCP-WebReader/4.0 (Educational; +https://mcp.local)"
ROBOTS_TTL = 3600
MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 30
MAX_BODY_SIZE_MB = 10
MAX_CRAWL_PAGES = 50
MAX_CRAWL_DEPTH = 3
ALLOWED_SCHEMES = ("http://", "https://")

_BLOCKED_NETS = [
    ipaddress.IPv4Network("127.0.0.0/8"), ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"), ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("169.254.0.0/16"), ipaddress.IPv6Network("::1/128"),
    ipaddress.IPv6Network("fc00::/7"), ipaddress.IPv6Network("fe80::/10")
]

_robots_cache: Dict[str, Dict[str, Any]] = {}
_cache_lock = threading.Lock()

def _get_domain(url: str) -> Optional[str]:
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.scheme + "://" + parsed.hostname if parsed.hostname else None
    except Exception:
        return None

def _get_robots_parser(base_url: str) -> RobotFileParser:
    with _cache_lock:
        cached = _robots_cache.get(base_url)
        if cached and time.time() < cached["expires"]:
            return cached["parser"]
    parser = RobotFileParser()
    robots_url = urllib.parse.urljoin(base_url, "/robots.txt")
    try:
        resp = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=10, allow_redirects=True)
        parser.parse(resp.text.splitlines() if resp.status_code == 200 else [])
    except Exception:
        parser.parse([])
    with _cache_lock:
        _robots_cache[base_url] = {"parser": parser, "expires": time.time() + ROBOTS_TTL}
    return parser

def _is_allowed_by_robots(url: str) -> bool:
    base = _get_domain(url)
    if not base:
        return False
    return _get_robots_parser(base).can_fetch(USER_AGENT, url)

def _is_safe_host(host: str) -> bool:
    try:
        if host.lower() in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "metadata.google.internal"):
            return False
        for ip in socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM):
            if any(ipaddress.ip_address(ip[4][0]) in net for net in _BLOCKED_NETS):
                return False
        return True
    except Exception:
        return False

def _extract_main_text(html: str, url: str) -> str:
    """Извлекает основной текст страницы, используя лучший доступный метод."""
    if TRAFILATURA_AVAILABLE:
        text = trafilatura.extract(html, include_comments=False, include_tables=False)
        if text:
            return text
    if READABILITY_AVAILABLE:
        try:
            doc = Document(html)
            return doc.summary()
        except:
            pass
    if BeautifulSoup:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "iframe", "noscript", "form", "meta", "link", "head"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
    return re.sub(r"<[^>]+>", "", html)

def _extract_tables(html: str, url: str) -> List[Dict]:
    tables = []
    if PANDAS_AVAILABLE:
        try:
            dfs = pd.read_html(html)
            for i, df in enumerate(dfs):
                tables.append({
                    "index": i,
                    "rows": len(df),
                    "columns": list(df.columns),
                    "data": df.fillna("").to_dict(orient="records")[:100]
                })
            return tables
        except Exception as e:
            _log(f"pandas read_html failed: {e}")
    if HTML_TABLE_TAKEOUT:
        try:
            parsed = parse_html(html)
            for i, table in enumerate(parsed):
                tables.append({"index": i, "data": table})
            return tables
        except Exception as e:
            _log(f"html-table-takeout failed: {e}")
    if BeautifulSoup:
        soup = BeautifulSoup(html, "html.parser")
        for i, table in enumerate(soup.find_all("table")):
            rows = []
            for tr in table.find_all("tr"):
                row = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if row:
                    rows.append(row)
            tables.append({"index": i, "rows": len(rows), "data": rows[:50]})
    return tables

def _extract_images(html: str, base_url: str) -> List[Dict]:
    images = []
    if not BeautifulSoup:
        return images
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        src = img.get("src")
        if not src:
            continue
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = urllib.parse.urljoin(base_url, src)
        images.append({
            "src": src,
            "alt": img.get("alt", ""),
            "title": img.get("title", ""),
            "width": img.get("width", ""),
            "height": img.get("height", "")
        })
    return images[:50]

def fetch_url_enhanced(url: str, extract_tables: bool = True, extract_images: bool = True,
                       timeout: int = DEFAULT_TIMEOUT, max_size_mb: int = MAX_BODY_SIZE_MB) -> Dict:
    d_id = dialog_ctx.get()
    if not url.startswith(ALLOWED_SCHEMES):
        return {"error": "Only HTTP/HTTPS allowed."}
    if not _is_safe_host(urllib.parse.urlparse(url).hostname):
        return {"error": "SSRF prevention: Host blocked."}
    if not _is_allowed_by_robots(url):
        return {"error": "robots.txt denies access."}
    session = None
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
        session.max_redirects = MAX_REDIRECTS
        resp = session.get(url, timeout=timeout, stream=True)
        resp.raise_for_status()
        ct = resp.headers.get("Content-Type", "").lower()
        max_bytes = max_size_mb * 1024 * 1024
        chunks, total = [], 0
        for chunk in resp.iter_content(chunk_size=8192):
            total += len(chunk)
            if total > max_bytes:
                return {"error": "Content exceeds size limit."}
            chunks.append(chunk)
        body = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
        text = _extract_main_text(body, url) if "html" in ct else body
        result = {
            "url": url,
            "status": resp.status_code,
            "content_type": ct,
            "text": text,
            "length_chars": len(text),
            "title": "",
            "images": [],
            "tables": []
        }
        if BeautifulSoup and "html" in ct:
            soup = BeautifulSoup(body, "html.parser")
            title_tag = soup.find("title")
            if title_tag:
                result["title"] = title_tag.get_text(strip=True)
        if extract_tables and "html" in ct:
            result["tables"] = _extract_tables(body, url)
        if extract_images and "html" in ct:
            result["images"] = _extract_images(body, url)
        conversation_memory.add(op="fetch_url_enhanced", paths={"url": url}, status="success", dialog=d_id,
                                context=f"Extracted {len(text)} chars, {len(result['tables'])} tables, {len(result['images'])} images")
        return result
    except RequestException as e:
        return {"error": f"Request failed: {e}"}
    finally:
        if session:
            session.close()

def web_search_enhanced(query: str, max_results: int = 15, sources: List[str] = None,
                        timeout: int = 30) -> Dict:
    d_id = dialog_ctx.get()
    if sources is None:
        sources = ["duckduckgo"]
    all_results = []
    seen_urls = set()
    if "duckduckgo" in sources:
        encoded_query = urllib.parse.quote(query)
        search_url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
        if BeautifulSoup:
            try:
                session = requests.Session()
                session.headers.update({"User-Agent": USER_AGENT})
                resp = session.get(search_url, timeout=timeout)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")
                for result in soup.select(".result"):
                    title_elem = result.select_one(".result__a")
                    snippet_elem = result.select_one(".result__snippet")
                    if not title_elem:
                        continue
                    raw_link = title_elem.get("href", "")
                    real_url = raw_link
                    if raw_link and raw_link.startswith("/"):
                        from urllib.parse import urlparse, parse_qs
                        parsed = urlparse(raw_link)
                        if parsed.path == "/l/":
                            qs = parse_qs(parsed.query)
                            if "uddg" in qs:
                                real_url = urllib.parse.unquote(qs["uddg"][0])
                            else:
                                real_url = "https://duckduckgo.com" + raw_link
                        else:
                            real_url = "https://duckduckgo.com" + raw_link
                    title = title_elem.get_text(strip=True)
                    snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""
                    if real_url not in seen_urls:
                        seen_urls.add(real_url)
                        all_results.append({
                            "title": title,
                            "snippet": snippet[:500],
                            "url": real_url,
                            "source": "duckduckgo"
                        })
                    if len(all_results) >= max_results:
                        break
            except Exception as e:
                _log(f"DuckDuckGo search error: {e}")
    if "brave" in sources:
        api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "")
        if api_key:
            try:
                headers = {"Accept": "application/json", "X-Subscription-Token": api_key}
                params = {"q": query, "count": max_results}
                resp = requests.get("https://api.search.brave.com/res/v1/web/search", headers=headers, params=params, timeout=timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    for web in data.get("web", {}).get("results", []):
                        url = web.get("url")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            all_results.append({
                                "title": web.get("title", ""),
                                "snippet": web.get("description", "")[:500],
                                "url": url,
                                "source": "brave"
                            })
                            if len(all_results) >= max_results:
                                break
            except Exception as e:
                _log(f"Brave search error: {e}")
    final_results = all_results[:max_results]
    conversation_memory.add(op="web_search_enhanced", paths={"query": query}, status="success",
                            dialog=d_id, context=f"Found {len(final_results)} results from {len(sources)} sources")
    return {
        "status": "success",
        "query": query,
        "count": len(final_results),
        "results": final_results,
        "markdown_results": [{"markdown": f"[{r['title']}]({r['url']})", "snippet": r['snippet']} for r in final_results]
    }

def fetch_url(url: str, timeout: int = DEFAULT_TIMEOUT, max_size_mb: int = MAX_BODY_SIZE_MB) -> Dict:
    return fetch_url_enhanced(url, extract_tables=False, extract_images=False,
                              timeout=timeout, max_size_mb=max_size_mb)

def web_search(query: str, max_results: int = 5, timeout: int = 30) -> Dict:
    return web_search_enhanced(query, max_results=max_results, sources=["duckduckgo"], timeout=timeout)

def read_rss(feed_url: str) -> Dict:
    d_id = dialog_ctx.get()
    if not feed_url.startswith(ALLOWED_SCHEMES):
        return {"error": "Invalid scheme."}
    if not _is_safe_host(urllib.parse.urlparse(feed_url).hostname):
        return {"error": "SSRF prevention."}
    if not feedparser:
        return {"error": "feedparser not installed. pip install feedparser"}
    session = None
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        resp = session.get(feed_url, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        entries = [{"title": e.get("title", ""), "link": e.get("link", ""), "published": e.get("published", ""), "summary": e.get("summary", "")[:300]} for e in parsed.entries[:20]]
        conversation_memory.add(op="read_rss", paths={"url": feed_url}, status="success", dialog=d_id, context=f"Parsed {len(entries)} entries")
        return {"url": feed_url, "title": parsed.feed.get("title", ""), "entries": entries}
    except Exception as e:
        return {"error": str(e)}
    finally:
        if session:
            session.close()

def download_file(url: str, destination: str) -> Dict:
    d_id = dialog_ctx.get()
    if not url.startswith(ALLOWED_SCHEMES):
        return {"error": "Invalid scheme."}
    if not _is_safe_host(urllib.parse.urlparse(url).hostname):
        return {"error": "SSRF prevention."}
    dest = Path(normalize_path(destination))
    _ensure_allowed(dest.parent, "download_file")
    session = None
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        resp = session.get(url, stream=True, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        written = 0
        max_bytes = MAX_BODY_SIZE_MB * 1024 * 1024
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    written += len(chunk)
                    if written > max_bytes:
                        dest.unlink(missing_ok=True)
                        return {"error": "Exceeded size limit."}
        conversation_memory.add(op="download_file", paths={"src": url, "dst": str(dest)}, status="success", dialog=d_id, context=f"Downloaded {written:,} bytes")
        return {"status": "success", "url": url, "destination": str(dest), "size_bytes": written}
    except Exception as e:
        if dest.exists():
            dest.unlink(missing_ok=True)
        return {"error": str(e)}
    finally:
        if session:
            session.close()

def crawl_deep_links(start_url: str, max_depth: int = MAX_CRAWL_DEPTH, max_pages: int = MAX_CRAWL_PAGES, same_domain: bool = True) -> Dict:
    d_id = dialog_ctx.get()
    if not start_url.startswith(ALLOWED_SCHEMES):
        return {"error": "Invalid scheme."}
    if not _is_safe_host(urllib.parse.urlparse(start_url).hostname):
        return {"error": "SSRF prevention."}
    if not BeautifulSoup:
        return {"error": "BeautifulSoup not installed. pip install beautifulsoup4"}
    start_domain = _get_domain(start_url)
    visited: Set[str] = set()
    results = []
    queue = [(start_url, 0)]
    while queue and len(visited) < max_pages:
        url, depth = queue.pop(0)
        if url in visited:
            continue
        if not _is_allowed_by_robots(url):
            continue
        visited.add(url)
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=DEFAULT_TIMEOUT, allow_redirects=True)
            if resp.status_code != 200 or "html" not in resp.headers.get("Content-Type", "").lower():
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            title = soup.find("title")
            title = title.text.strip() if title else ""
            text = soup.get_text(separator=" ", strip=True)[:500]
            results.append({"url": url, "title": title, "depth": depth, "preview": text, "status": 200})
            if depth < max_depth:
                for a in soup.find_all("a", href=True):
                    href = urllib.parse.urljoin(url, a["href"])
                    clean = href.split("#")[0]
                    if clean.startswith(ALLOWED_SCHEMES) and clean not in visited:
                        if same_domain and _get_domain(clean) != start_domain:
                            continue
                        queue.append((clean, depth + 1))
        except Exception:
            results.append({"url": url, "title": "Error", "depth": depth, "preview": "Fetch failed", "status": 0})
    conversation_memory.add(op="crawl_deep_links", paths={"url": start_url}, status="success", dialog=d_id, context=f"Crawled {len(results)} pages, depth {max_depth}")
    return {"start_url": start_url, "pages_found": len(results), "max_depth_reached": max_depth, "data": results}

def fetch_dynamic_js(url: str, wait_for_selector: Optional[str] = None, js_eval: Optional[str] = None, timeout: int = 30000) -> Dict:
    d_id = dialog_ctx.get()
    if not PW_AVAILABLE:
        return {"error": "Playwright not installed. pip install playwright && playwright install chromium"}
    if not url.startswith(ALLOWED_SCHEMES):
        return {"error": "Invalid scheme."}
    if not _is_safe_host(urllib.parse.urlparse(url).hostname):
        return {"error": "SSRF prevention."}
    if not _is_allowed_by_robots(url):
        return {"error": "robots.txt denies access."}
    p = None
    browser = None
    try:
        p = sync_playwright().start()
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout)
        if wait_for_selector:
            page.wait_for_selector(wait_for_selector, timeout=timeout)
        if js_eval:
            page.evaluate(js_eval)
            time.sleep(1)
        content = page.content()
        title = page.title()
        text = _extract_main_text(content, url)
        conversation_memory.add(op="fetch_dynamic_js", paths={"url": url}, status="success", dialog=d_id, context="Rendered JS content successfully")
        return {"url": url, "title": title, "text": text, "rendered": True, "length_chars": len(text)}
    except Exception as e:
        return {"error": f"Playwright failed: {e}"}
    finally:
        try:
            if browser:
                browser.close()
            if p:
                p.stop()
        except:
            pass

def export_scraped_data(data: List[Dict], output_path: str, format: str = "json", delimiter: str = ",") -> Dict:
    d_id = dialog_ctx.get()
    if not data:
        return {"error": "No data provided for export."}
    dest = Path(normalize_path(output_path))
    _ensure_allowed(dest.parent, "export_scraped_data")
    _ensure_allowed(dest, "export_scraped_data")
    fmt = format.lower()
    if fmt not in ("json", "csv"):
        return {"error": "Format must be 'json' or 'csv'."}
    try:
        if fmt == "json":
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        else:
            keys = set()
            for d in data:
                keys.update(d.keys())
            headers = sorted(keys)
            with open(dest, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=headers, delimiter=delimiter, extrasaction="ignore")
                writer.writeheader()
                for row in data:
                    writer.writerow(row)
        conversation_memory.add(op="export_scraped_data", paths={"dst": str(dest)}, status="success", dialog=d_id, context=f"Exported {len(data)} records to {fmt.upper()}")
        return {"status": "success", "path": str(dest), "records": len(data), "format": fmt}
    except Exception as e:
        return {"error": str(e)}

def register_tools(server: BaseMCPServer):
    # Базовый fetch_url (совместимость)
    server.register_tool("fetch_url", {
        "description": "Fetch and sanitize text from web page (robots/SSRF protected)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "timeout": {"type": "integer", "default": 30},
                "max_size_mb": {"type": "integer", "default": 10}
            },
            "required": ["url"]
        }
    }, lambda **kw: fetch_url(kw["url"], kw.get("timeout", 30), kw.get("max_size_mb", 10)))

    server.register_tool("fetch_url_enhanced", {
        "description": "Fetch webpage with tables, images, and improved text extraction",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "extract_tables": {"type": "boolean", "default": True},
                "extract_images": {"type": "boolean", "default": True},
                "timeout": {"type": "integer", "default": 30},
                "max_size_mb": {"type": "integer", "default": 10}
            },
            "required": ["url"]
        }
    }, lambda **kw: fetch_url_enhanced(kw["url"], kw.get("extract_tables", True), kw.get("extract_images", True),
                                       kw.get("timeout", 30), kw.get("max_size_mb", 10)))

    # Короткое имя web_search (удобно для модели)
    server.register_tool("web_search", {
        "description": "Search the web using DuckDuckGo. Returns real URLs and snippets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 5},
                "timeout": {"type": "integer", "default": 30}
            },
            "required": ["query"]
        }
    }, lambda **kw: web_search(kw["query"], kw.get("max_results", 5), kw.get("timeout", 30)))

    server.register_tool("web_search_enhanced", {
        "description": "Search web using multiple sources (DuckDuckGo, Brave, etc.)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 15},
                "sources": {"type": "array", "items": {"type": "string", "enum": ["duckduckgo", "brave"]}, "default": ["duckduckgo"]},
                "timeout": {"type": "integer", "default": 30}
            },
            "required": ["query"]
        }
    }, lambda **kw: web_search_enhanced(kw["query"], kw.get("max_results", 15), kw.get("sources", ["duckduckgo"]), kw.get("timeout", 30)))

    server.register_tool("read_rss", {
        "description": "Parse RSS/Atom feed into structured JSON",
        "inputSchema": {
            "type": "object",
            "properties": {"feed_url": {"type": "string"}},
            "required": ["feed_url"]
        }
    }, lambda **kw: read_rss(kw["feed_url"]))

    server.register_tool("download_file", {
        "description": "Safely download file from URL to allowed local path",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "destination": {"type": "string"}
            },
            "required": ["url", "destination"]
        }
    }, lambda **kw: download_file(kw["url"], kw["destination"]))

    server.register_tool("crawl_deep_links", {
        "description": "Crawl links from start URL up to max depth/pages (same-domain lock)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "start_url": {"type": "string"},
                "max_depth": {"type": "integer", "default": 3},
                "max_pages": {"type": "integer", "default": 50},
                "same_domain": {"type": "boolean", "default": True}
            },
            "required": ["start_url"]
        }
    }, lambda **kw: crawl_deep_links(kw["start_url"], kw.get("max_depth", 3), kw.get("max_pages", 50), kw.get("same_domain", True)))

    server.register_tool("fetch_dynamic_js", {
        "description": "Render JS-heavy pages via Playwright (optional form interaction)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "wait_for_selector": {"type": "string"},
                "js_eval": {"type": "string"},
                "timeout": {"type": "integer", "default": 30000}
            },
            "required": ["url"]
        }
    }, lambda **kw: fetch_dynamic_js(kw["url"], kw.get("wait_for_selector"), kw.get("js_eval"), kw.get("timeout", 30000)))

    server.register_tool("export_scraped_data", {
        "description": "Export structured web data to JSON or CSV",
        "inputSchema": {
            "type": "object",
            "properties": {
                "data": {"type": "array", "items": {"type": "object"}},
                "output_path": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "csv"], "default": "json"},
                "delimiter": {"type": "string", "default": ","}
            },
            "required": ["data", "output_path"]
        }
    }, lambda **kw: export_scraped_data(kw["data"], kw["output_path"], kw.get("format", "json"), kw.get("delimiter", ",")))

if __name__ == "__main__":
    server = BaseMCPServer("web-reader", "4.0")
    register_tools(server)
    server.run()
