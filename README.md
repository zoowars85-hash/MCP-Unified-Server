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

# Пользование, запуск, интеграция и зависимости / Usage, Launch, Integration & Dependencies

> **Примечание:** Проект предполагает установку в папку `C:\Tools`. Все примеры путей используют эту директорию.

---

## Русский

### 📦 Зависимости

#### Python-пакеты (устанавливаются автоматически через `setup.bat` из `C:\Tools`)

| Пакет | Назначение |
|-------|-------------|
| `watchdog` | Слежение за файловой системой |
| `psutil` | Мониторинг системы (CPU, память, диски) |
| `requests` | HTTP-запросы (веб, API) |
| `beautifulsoup4` | Парсинг HTML |
| `feedparser` | Чтение RSS/Atom лент |
| `icalendar` | Работа с календарями `.ics` |
| `openpyxl` | Чтение Excel `.xlsx` |
| `python-docx` | Чтение Word `.docx` |
| `python-pptx` | Чтение PowerPoint `.pptx` |
| `pytesseract` | OCR (текст из изображений) |
| `Pillow` | Работа с изображениями |
| `mutagen` | Метаданные аудиофайлов |
| `duckdb` | Анализ данных и работа с Parquet/CSV |
| `pyodbc` | Подключение к MS Access |
| `patool`, `py7zr`, `rarfile` | Работа с архивами |
| `playwright` | Рендеринг JavaScript-страниц |
| `pandas` | Работа с табличными данными |
| `sentence-transformers` | RAG эмбеддинги |
| `chromadb` | Векторная база данных (RAG) |
| `pypdf`, `pdfplumber` | Извлечение текста из PDF |
| `ebooklib` | Чтение EPUB |
| `keyring` | Безопасное хранение паролей |
| `xxhash` | Быстрое хеширование файлов |
| `cryptography` | Криптография (опционально) |

#### Внешние утилиты (требуются для некоторых функций)

| Утилита | Необходима для | Как установить (Windows) |
|---------|----------------|--------------------------|
| **ffmpeg** | Метаданные видео, миниатюры | `winget install Gyan.FFmpeg` |
| **pandoc** | Экспорт диалогов в PDF | `winget install JGM.Pandoc` |
| **wkhtmltopdf** | Движок PDF через pandoc | `winget install wkhtmltopdf.wkhtmltopdf` |
| **rclone** | Облачная синхронизация | `winget install Rclone.Rclone` |
| **Tesseract** | OCR (текст из картинок) | `winget install UB-Mannheim.TesseractOCR` |
| **LibreOffice** | Конвертация Office → PDF | `winget install TheDocumentFoundation.LibreOffice` |
| **Playwright браузеры** | Рендеринг JS | После установки playwright: `playwright install chromium` |

> **Примечание:** `setup.bat` из `C:\Tools` автоматически устанавливает **только Python-пакеты** и пытается установить pandoc + wkhtmltopdf. Остальные утилиты нужно установить вручную или через winget.

### 🚀 Запуск и интеграция

#### 1. Установка

```bash
# Перейдите в папку проекта
cd C:\Tools

# Клонируйте репозиторий (если ещё не сделали)
git clone https://github.com/yourname/mcp-unified-server.git
cd C:\Tools\mcp-unified-server

# Запустите установщик
setup.bat

# Меню установщика:
# 1 – Создать виртуальное окружение (venv)
# 2 – Скачать зависимости (интернет)
# 3 – Установить зависимости из python_deps (офлайн)
# 8 – Запустить сервер
2. Настройка переменных окружения
Создайте файл .env в C:\Tools\mcp-unified-server или установите переменные в системе:

ini
# Пути (все файлы в C:\Tools)
MCP_ALLOWED_PATHS=C:\Tools;C:\
MCP_MEMORY_PATH=C:\Tools\mcp_memory.db
MCP_TASK_DB=C:\Tools\mcp_tasks.db
MCP_SCHEDULER_DB=C:\Tools\mcp_scheduler.db
MCP_RAG_DB_PATH=C:\Tools\mcp_rag_db
MCP_TRASH_PATH=C:\Tools\.mcp_trash

# Локальная LLM (например, LM Studio на порту 1234)
LLM_ENDPOINT=http://localhost:1234/v1/chat/completions

# Автоматизация
MCP_AUTO_MEMORY=true
MCP_VERBOSE_DEFAULT=true
MCP_SHELL_TIMEOUT=30

# Безопасность (опционально)
MCP_ALLOWED_UNC_PATHS=\\server\share
MCP_TRASH_MAX_AGE=30
3. Интеграция с LM Studio
Откройте LM Studio → Настройки → MCP Servers

Нажмите "Edit config file"

Вставьте конфигурацию (все пути ведут в C:\Tools):

json
{
  "mcpServers": {
    "mcp_unified": {
      "command": "C:\\Tools\\mcp-unified-server\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Tools\\mcp-unified-server\\mcp_fs_server.py"],
      "env": {
        "PYTHONIOENCODING": "utf-8",
        "MCP_ALLOWED_PATHS": "C:\\Tools;C:\\",
        "MCP_MEMORY_PATH": "C:\\Tools\\mcp_memory.db",
        "LLM_ENDPOINT": "http://localhost:1234/v1/chat/completions",
        "MCP_AUTO_MEMORY": "true"
      }
    }
  }
}
Перезапустите LM Studio. В чате появятся инструменты.

4. Интеграция с Claude Desktop
Файл конфигурации: %APPDATA%\Claude\claude_desktop_config.json

json
{
  "mcpServers": {
    "mcp_unified": {
      "command": "C:\\Tools\\mcp-unified-server\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Tools\\mcp-unified-server\\mcp_fs_server.py"],
      "env": {
        "MCP_ALLOWED_PATHS": "C:\\Tools;C:\\",
        "MCP_MEMORY_PATH": "C:\\Tools\\mcp_memory.db",
        "LLM_ENDPOINT": "http://localhost:1234/v1/chat/completions"
      }
    }
  }
}
5. Ручной запуск (без MCP-клиента, для отладки)
bash
cd C:\Tools\mcp-unified-server
.venv\Scripts\python mcp_fs_server.py
Сервер будет слушать STDIN и выводить ответы в STDOUT. Для теста можно отправить JSON-RPC запрос:

json
{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}
🧪 Проверка работоспособности
После интеграции попросите LLM выполнить простую команду:

text
!help
или

text
найди файлы *.py в папке C:\Tools
Сервер должен ответить результатом.

🗂️ Структура файлов в C:\Tools после установки
text
C:\Tools\
├── mcp-unified-server\          # Корень проекта
│   ├── .venv\                   # Виртуальное окружение Python
│   ├── mcp_fs_server.py         # Главный сервер
│   ├── mcp_shared.py            # Общее ядро
│   ├── mcp_*.py                 # Все модули инструментов
│   ├── setup.bat                # Установщик
│   ├── python_deps\             # Offline-пакеты (опционально)
│   └── mcp_rag_db\              # RAG векторная база (создаётся при первом индексе)
├── mcp_memory.db                # База памяти диалогов
├── mcp_tasks.db                 # База асинхронных задач
├── mcp_scheduler.db             # База планировщика
└── .mcp_trash\                  # Корзина (скрытая папка)
🐧 Запуск на Linux / macOS (экспериментально)
bash
# Создание виртуального окружения
python3 -m venv .venv
source .venv/bin/activate

# Установка зависимостей (ручная)
pip install -r requirements.txt   # или pip install watchdog psutil requests ...

# Запуск
.venv/bin/python mcp_fs_server.py
Внимание: setup.bat не работает на Linux/macOS. Скрипт автоматической установки нуждается в портировании – помощь сообщества приветствуется!

English
Note: The project is intended to be installed in C:\Tools. All example paths use this directory.

📦 Dependencies
Python packages (automatically installed via setup.bat from C:\Tools)
Package	Purpose
watchdog	File system monitoring
psutil	System monitoring (CPU, memory, disks)
requests	HTTP requests (web, API)
beautifulsoup4	HTML parsing
feedparser	RSS/Atom feed reading
icalendar	.ics calendar handling
openpyxl	Excel .xlsx reading
python-docx	Word .docx reading
python-pptx	PowerPoint .pptx reading
pytesseract	OCR (text from images)
Pillow	Image processing
mutagen	Audio file metadata
duckdb	Data analysis, Parquet/CSV
pyodbc	MS Access connection
patool, py7zr, rarfile	Archive handling
playwright	JavaScript page rendering
pandas	Tabular data processing
sentence-transformers	RAG embeddings
chromadb	Vector database (RAG)
pypdf, pdfplumber	PDF text extraction
ebooklib	EPUB reading
keyring	Secure password storage
xxhash	Fast file hashing
cryptography	Cryptography (optional)
External tools (required for some features)
Tool	Required for	Install on Windows
ffmpeg	Video metadata, thumbnails	winget install Gyan.FFmpeg
pandoc	Dialog export to PDF	winget install JGM.Pandoc
wkhtmltopdf	PDF engine via pandoc	winget install wkhtmltopdf.wkhtmltopdf
rclone	Cloud sync	winget install Rclone.Rclone
Tesseract	OCR (text from images)	winget install UB-Mannheim.TesseractOCR
LibreOffice	Office → PDF conversion	winget install TheDocumentFoundation.LibreOffice
Playwright browsers	JS rendering	After playwright install: playwright install chromium
Note: setup.bat from C:\Tools automatically installs Python packages only and tries to install pandoc + wkhtmltopdf. Other tools need manual installation or via winget.

🚀 Launch and Integration
1. Installation
bash
# Navigate to project folder
cd C:\Tools

# Clone the repository (if not already done)
git clone https://github.com/yourname/mcp-unified-server.git
cd C:\Tools\mcp-unified-server

# Run the installer
setup.bat

# Installer menu:
# 1 – Create virtual environment (venv)
# 2 – Download dependencies (internet required)
# 3 – Install dependencies from python_deps (offline)
# 8 – Start the server
2. Environment variables configuration
Create a .env file in C:\Tools\mcp-unified-server or set system variables:

ini
# Paths (all files in C:\Tools)
MCP_ALLOWED_PATHS=C:\Tools;C:\
MCP_MEMORY_PATH=C:\Tools\mcp_memory.db
MCP_TASK_DB=C:\Tools\mcp_tasks.db
MCP_SCHEDULER_DB=C:\Tools\mcp_scheduler.db
MCP_RAG_DB_PATH=C:\Tools\mcp_rag_db
MCP_TRASH_PATH=C:\Tools\.mcp_trash

# Local LLM (e.g., LM Studio on port 1234)
LLM_ENDPOINT=http://localhost:1234/v1/chat/completions

# Automation
MCP_AUTO_MEMORY=true
MCP_VERBOSE_DEFAULT=true
MCP_SHELL_TIMEOUT=30

# Security (optional)
MCP_ALLOWED_UNC_PATHS=\\server\share
MCP_TRASH_MAX_AGE=30
3. Integration with LM Studio
Open LM Studio → Settings → MCP Servers

Click "Edit config file"

Paste the configuration (all paths point to C:\Tools):

json
{
  "mcpServers": {
    "mcp_unified": {
      "command": "C:\\Tools\\mcp-unified-server\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Tools\\mcp-unified-server\\mcp_fs_server.py"],
      "env": {
        "PYTHONIOENCODING": "utf-8",
        "MCP_ALLOWED_PATHS": "C:\\Tools;C:\\",
        "MCP_MEMORY_PATH": "C:\\Tools\\mcp_memory.db",
        "LLM_ENDPOINT": "http://localhost:1234/v1/chat/completions",
        "MCP_AUTO_MEMORY": "true"
      }
    }
  }
}
Restart LM Studio. Tools will appear in the chat.

4. Integration with Claude Desktop
Config file: %APPDATA%\Claude\claude_desktop_config.json

json
{
  "mcpServers": {
    "mcp_unified": {
      "command": "C:\\Tools\\mcp-unified-server\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Tools\\mcp-unified-server\\mcp_fs_server.py"],
      "env": {
        "MCP_ALLOWED_PATHS": "C:\\Tools;C:\\",
        "MCP_MEMORY_PATH": "C:\\Tools\\mcp_memory.db",
        "LLM_ENDPOINT": "http://localhost:1234/v1/chat/completions"
      }
    }
  }
}
5. Manual launch (without MCP client, for debugging)
bash
cd C:\Tools\mcp-unified-server
.venv\Scripts\python mcp_fs_server.py
The server listens on STDIN and outputs responses to STDOUT. For testing, send a JSON-RPC request:

json
{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}
🧪 Testing
After integration, ask the LLM to run a simple command:

text
!help
or

text
search for *.py files in C:\Tools
The server should respond with results.

🗂️ File structure in C:\Tools after installation
text
C:\Tools\
├── mcp-unified-server\          # Project root
│   ├── .venv\                   # Python virtual environment
│   ├── mcp_fs_server.py         # Main server
│   ├── mcp_shared.py            # Core shared library
│   ├── mcp_*.py                 # All tool modules
│   ├── setup.bat                # Installer
│   ├── python_deps\             # Offline packages (optional)
│   └── mcp_rag_db\              # RAG vector database (created on first index)
├── mcp_memory.db                # Dialog memory database
├── mcp_tasks.db                 # Async tasks database
├── mcp_scheduler.db             # Scheduler database
└── .mcp_trash\                  # Trash folder (hidden)
🐧 Running on Linux / macOS (experimental)
bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Manual dependency installation
pip install -r requirements.txt   # or pip install watchdog psutil requests ...

# Launch
.venv/bin/python mcp_fs_server.py
Note: setup.bat does not work on Linux/macOS. An automatic installation script needs porting – community help is very welcome!
