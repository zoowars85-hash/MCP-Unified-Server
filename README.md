Архитектура проекта / Project Architecture
markdown
## Архитектура проекта / Project Architecture

### Русский

**MCP Unified Server** – это единая точка входа для всех инструментов. Всё общение с LLM-клиентом происходит через **JSON‑RPC 2.0** по `STDIN`/`STDOUT`.
[LLM Client] → STDIN → mcp_fs_server.py → JSON-RPC → [Tool]
(LM Studio, (главный сервер) │
Claude Desktop) ├── Файловые операции
├── Поиск и RAG
├── Веб и API
├── Базы данных
├── Почта и календарь
├── Shell команды
└── ... (все инструменты)

text

### 🧩 Ключевые компоненты

| Файл | Назначение |
|------|------------|
| `mcp_fs_server.py` | **Главный сервер** – загружает все модули, регистрирует инструменты, запускает JSON‑RPC |
| `mcp_shared.py` | **Общее ядро** – память диалогов, безопасность путей, нормализация, логирование, контекст |
| `mcp_verbose.py` | **Прогресс‑уведомления** – batch‑режим, rate limiting, агрегация прогресса |
| `mcp_task_manager.py` | **Асинхронные задачи** – пауза, возобновление, сохранение прогресса |
| `mcp_orchestrator.py` | **Оркестратор** – естественный язык → план действий, синтаксис `!command` |
| `mcp_rag_engine.py` | **RAG движок** – векторный поиск по книгам и документам |
| `mcp_web_reader.py` | **Веб и поиск** – безопасное извлечение HTML, RSS, DuckDuckGo |
| `mcp_fs_*.py` | **Файловые модули** – операции, поиск, синхронизация, корзина, версионирование и т.д. |
| `*.py` | Остальные инструменты: почта, календарь, базы данных, shell, API‑клиент и др. |
| `setup.bat` | **Установщик для Windows** – создаёт venv, ставит зависимости, запускает сервер |
| `.venv/` | Виртуальное окружение Python (создаётся автоматически) |
| `*.db` | Базы данных SQLite (память диалогов, задачи, знания, индексы) |

### 🔌 Как это работает

1. **LLM‑клиент** (LM Studio, Claude Desktop) запускает `mcp_fs_server.py` как дочерний процесс.
2. Клиент отправляет JSON‑RPC запросы (например, `{"method": "tools/call", "params": {"name": "search_files", "arguments": {...}}}`).
3. Сервер обрабатывает запрос, вызывает нужный инструмент, возвращает результат.
4. **Память диалогов** (`conversation_memory`) автоматически сохраняет каждое действие в SQLite.
5. **Прогресс** отправляется через `stderr` в формате `[PROGRESS][dialog_id] Сообщение`.

### 🖥️ Команда запуска для LM Studio / Claude Desktop

```json
{
  "mcpServers": {
    "mcp_unified": {
      "command": "C:\\Tools\\mcp-unified-server\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Tools\\mcp-unified-server\\mcp_fs_server.py"],
      "env": {
        "MCP_ALLOWED_PATHS": "C:\\;D:\\Data",
        "LLM_ENDPOINT": "http://localhost:1234/v1/chat/completions",
        "MCP_AUTO_MEMORY": "true"
      }
    }
  }
}
🔧 Основные переменные окружения
Переменная	Назначение
MCP_ALLOWED_PATHS	Разрешённые директории (через ;)
LLM_ENDPOINT	URL локальной LLM (например, LM Studio)
MCP_MEMORY_PATH	Путь к БД памяти диалогов
MCP_AUTO_MEMORY	Автоматические снимки и очистка (true/false)
MCP_VERBOSE_DEFAULT	Включить прогресс по умолчанию
MCP_RAG_DB_PATH	Папка для векторной базы ChromaDB
MCP_SHELL_TIMEOUT	Таймаут для shell‑команд (сек)
Полный список – в комментариях кода и CONTRIBUTING.md.

🐧 Примечание для Linux / macOS
Сервер написан на кроссплатформенном Python, но не тестировался на Linux/macOS.
Ожидаются отличия в:

Путях (используйте os.path.join или Path)

Shell‑командах (вместо powershell – bash)

Установке зависимостей (нужен аналог setup.bat)

Помощь сообщества с адаптацией приветствуется!

English
MCP Unified Server is a single entry point for all tools. Communication with the LLM client happens via JSON‑RPC 2.0 over STDIN/STDOUT.

text
[LLM Client] → STDIN → mcp_fs_server.py → JSON-RPC → [Tool]
(LM Studio,        (main server)           │
 Claude Desktop)                           ├── File operations
                                            ├── Search & RAG
                                            ├── Web & API
                                            ├── Databases
                                            ├── Email & Calendar
                                            ├── Shell commands
                                            └── ... (all tools)
🧩 Key Components
File	Purpose
mcp_fs_server.py	Main server – loads modules, registers tools, runs JSON‑RPC
mcp_shared.py	Core – dialog memory, path security, normalization, logging, context
mcp_verbose.py	Progress notifications – batch mode, rate limiting, aggregation
mcp_task_manager.py	Async tasks – pause, resume, progress persistence
mcp_orchestrator.py	Orchestrator – natural language → action plan, !command syntax
mcp_rag_engine.py	RAG engine – vector search across books and documents
mcp_web_reader.py	Web & search – safe HTML fetch, RSS, DuckDuckGo
mcp_fs_*.py	Filesystem modules – operations, search, sync, trash, versioning, etc.
*.py	Other tools: email, calendar, databases, shell, API client, etc.
setup.bat	Windows installer – creates venv, installs deps, runs server
.venv/	Python virtual environment (created automatically)
*.db	SQLite databases (dialog memory, tasks, KB, indices)
🔌 How it works
LLM client (LM Studio, Claude Desktop) spawns mcp_fs_server.py as a child process.

Client sends JSON‑RPC requests (e.g., {"method": "tools/call", "params": {"name": "search_files", "arguments": {...}}}).

Server processes the request, calls the appropriate tool, returns the result.

Conversation memory (conversation_memory) automatically saves every action to SQLite.

Progress is sent via stderr in the format [PROGRESS][dialog_id] Message.

🖥️ Launch command for LM Studio / Claude Desktop
json
{
  "mcpServers": {
    "mcp_unified": {
      "command": "C:\\Tools\\mcp-unified-server\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Tools\\mcp-unified-server\\mcp_fs_server.py"],
      "env": {
        "MCP_ALLOWED_PATHS": "C:\\;D:\\Data",
        "LLM_ENDPOINT": "http://localhost:1234/v1/chat/completions",
        "MCP_AUTO_MEMORY": "true"
      }
    }
  }
}
🔧 Essential environment variables
Variable	Purpose
MCP_ALLOWED_PATHS	Allowed directories (semicolon‑separated)
LLM_ENDPOINT	Local LLM URL (e.g., LM Studio)
MCP_MEMORY_PATH	Path to dialog memory database
MCP_AUTO_MEMORY	Enable automatic snapshots & cleanup (true/false)
MCP_VERBOSE_DEFAULT	Enable progress notifications by default
MCP_RAG_DB_PATH	Folder for ChromaDB vector database
MCP_SHELL_TIMEOUT	Timeout for shell commands (seconds)
Full list is in code comments and CONTRIBUTING.md.

🐧 Linux / macOS note
The server is written in cross‑platform Python but has not been tested on Linux/macOS.
Expect differences in:

Paths (use os.path.join or Path)

Shell commands (instead of powershell – bash)

Dependency installation (needs an alternative to setup.bat)
