MCP Unified Server – Complete Toolset for LLM Agents
Author: Claus
Repository: [[link](https://github.com/zoowars85-hash/MCP-Unified-Server.git)]
Tested only with LM Studio – developed and tested exclusively with LM Studio. Should work with any MCP client, but community help is needed for testing on other platforms (Claude Desktop, Continue, Zed, etc.).

⚠️ Important: LLMs can make mistakes. Always review and confirm destructive operations. Use dry_run=True and do not trust the AI blindly.

🧠 What is this?
MCP Unified Server is a single entry point for the Model Context Protocol (MCP), providing LLM agents with over 90 tools for filesystem operations, web search, databases, email, calendars, RAG, shell commands, and much more. Written in Python, it communicates with the client via JSON‑RPC 2.0 over STDIN/STDOUT.

text
[LLM Client]  →  STDIN  →  mcp_fs_server.py  →  JSON‑RPC  →  [Tools]
 (LM Studio,                    (main server)                │
  Claude Desktop)                                              ├── Filesystem ops
                                                               ├── Search & RAG
                                                               ├── Web & API
                                                               ├── Databases
                                                               ├── Email & Calendar
                                                               ├── Shell commands
                                                               └── ... (90+)
✨ Key Features
Category	Capabilities
Filesystem	copy/move/delete (with trash), search by name/content (regex), duplicates, directory analysis, batch operations, smart LLM‑driven sorting, bidirectional sync, versioning, ZIP/TAR archives, cloud sync (rclone), real‑time monitoring (watchdog), content indexing (FTS5), media metadata (EXIF/ID3/ffprobe), OCR, thumbnails, safe script execution
Memory & Context	Automatic logging of every tool call, history compression (TF‑IDF), fact recall, snapshots, permanent archive with restore
RAG	Index PDF, DOCX, EPUB, TXT, MD via ChromaDB + sentence‑transformers, answer questions over your documents
Web	Text extraction (trafilatura/readability), tables, images, web search (DuckDuckGo/Brave), RSS feeds
Databases	Read‑only queries to SQLite, DuckDB, CSV, Parquet, MS Access (DML/DDL blocked)
Office	Read DOCX, XLSX, PPTX, convert to PDF (LibreOffice)
Email & Calendar	IMAP/POP3/SMTP (keyring password storage), ICS, Outlook tasks
Shell	PowerShell / CMD with dangerous command filtering and confirmation
LLM helpers	Logic consistency checking, arithmetic validation, isolated Python execution, natural language orchestrator (!command syntax), async tasks (pause/resume), scheduler (cron/intervals), centralised logging, admin dashboard (health, metrics, Telegram/Slack alerts), auto‑restart watchdog
Security	Allow‑listed paths, blocked system folders, ZipSlip/SSRF/SQL injection protection, hallucination detection (placeholder paths)
🖥️ Requirements & Installation
The project is designed to be installed in C:\Tools (on Windows). All example paths use this directory.

Dependencies
Python 3.10+ (automatically creates .venv)

Core Python packages: watchdog, psutil, requests, beautifulsoup4, feedparser, icalendar, openpyxl, python-docx, python-pptx, pytesseract, Pillow, mutagen, duckdb, pyodbc, patool, py7zr, rarfile, playwright, pandas, sentence-transformers, chromadb, pypdf, pdfplumber, ebooklib, keyring, xxhash, cryptography.

External tools (optional but needed for some features):

ffmpeg – video, thumbnails

pandoc + wkhtmltopdf – dialog export to PDF

rclone – cloud sync

Tesseract – OCR

LibreOffice – Office → PDF conversion

Playwright browsers (after playwright install chromium)

Quick start (Windows)
cmd
cd C:\Tools
git clone https://github.com/yourname/mcp-unified-server.git
cd mcp-unified-server
setup.bat
# Menu: 1 (create venv) → 2 (download dependencies) → 8 (start server)
After installation, configure environment variables (.env file or system):

ini
MCP_ALLOWED_PATHS=C:\Tools;C:\
LLM_ENDPOINT=http://localhost:1234/v1/chat/completions   # for LM Studio
MCP_AUTO_MEMORY=true
MCP_VERBOSE_DEFAULT=true
MCP_MEMORY_PATH=C:\Tools\mcp_memory.db
MCP_TASK_DB=C:\Tools\mcp_tasks.db
MCP_RAG_DB_PATH=C:\Tools\mcp_rag_db
MCP_TRASH_PATH=C:\Tools\.mcp_trash
🔌 Integration with MCP clients
LM Studio
Open Settings → MCP Servers → Edit config file

Paste:

json
{
  "mcpServers": {
    "mcp_unified": {
      "command": "C:\\Tools\\mcp-unified-server\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Tools\\mcp-unified-server\\mcp_fs_server.py"],
      "env": {
        "MCP_ALLOWED_PATHS": "C:\\Tools;C:\\",
        "LLM_ENDPOINT": "http://localhost:1234/v1/chat/completions",
        "MCP_AUTO_MEMORY": "true"
      }
    }
  }
}
Claude Desktop
Config file: %APPDATA%\Claude\claude_desktop_config.json (similar, adjust Python path to your installation)

Other clients
Any MCP client that can spawn child processes via STDIO.
Testing help needed with Continue, Zed, Cursor, etc.

🧪 Usage examples (via LLM)
!search_files path=C:\Tools pattern=*.py recursive=true – search files

find duplicates in D:\Downloads – intelligent duplicate search

sync C:\Projects with D:\Backup in mirror mode – calls sync_directories

index folder C:\Books for RAG – create vector database

send email to example@domain.com with subject "Report" – via SMTP

schedule empty_trash every Sunday – cron scheduler

🐧 Linux / macOS (experimental)
The server is written in cross‑platform Python but has not been tested on Linux/macOS. Expected differences in:

paths (use os.path.join or Path),

shell commands (instead of powershell – bash),

dependency installation (needs an alternative to setup.bat).

To run manually:

bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt   # or install required packages manually
.venv/bin/python mcp_fs_server.py
Community help for adapting to Linux/macOS and creating an installer is very welcome!

🤝 How to help the project
🐛 Testing on different MCP clients (Claude Desktop, Continue, Zed) and OS (Linux, macOS)

🔒 Security audit – especially dangerous operations (shell, deletion, network)

📚 Documentation – translations, examples, improve README

🧪 Writing tests (unit, integration)

🌍 Localisation of interface and messages

🛠️ Porting setup.bat to bash / PowerShell Core for Unix‑like systems

💡 New ideas and tools – create your own modules and share them

📄 License
The project is distributed under the MIT License.
Author: Claus (claus@mcp.local)
Contact: Issues / Pull Requests on GitHub.


MCP Unified Server – полный набор инструментов для LLM-агентов
Автор: Claus
Репозиторий: (https://github.com/zoowars85-hash/MCP-Unified-Server.git)
Тестировалось только с LM Studio – разрабатывалось и отлаживалось исключительно в этой среде. Должно работать с любым MCP-клиентом, но нужна помощь сообщества в тестировании на других платформах (Claude Desktop, Continue, Zed и т.д.).

⚠️ Важно: LLM могут ошибаться. Всегда проверяйте и подтверждайте деструктивные операции. Используйте dry_run=True и не доверяйте ИИ слепо.

🧠 Что это?
MCP Unified Server – единая точка входа для Model Context Protocol (MCP), предоставляющая LLM-агентам более 90 инструментов для работы с файловой системой, вебом, базами данных, электронной почтой, календарями, RAG, shell-командами и многим другим. Сервер написан на Python, общается с клиентом по JSON‑RPC 2.0 через STDIN/STDOUT.

text
[LLM Client]  →  STDIN  →  mcp_fs_server.py  →  JSON‑RPC  →  [Инструменты]
 (LM Studio,                    (главный сервер)          │
  Claude Desktop)                                            ├── Файловые операции
                                                             ├── Поиск и RAG
                                                             ├── Веб и API
                                                             ├── Базы данных
                                                             ├── Почта и календарь
                                                             ├── Shell команды
                                                             └── ... (90+)
✨ Основные возможности
Категория	Возможности
Файловая система	copy/move/delete (с корзиной), поиск по маске и содержимому (регэксп), дубликаты, анализ директорий, пакетные операции, умное перемещение через LLM, двунаправленная синхронизация, версионирование, архивация ZIP/TAR, облачная синхронизация (rclone), мониторинг изменений (watchdog), индексация содержимого (FTS5), медиа-метаданные (EXIF/ID3/ffprobe), OCR, миниатюры, безопасное выполнение скриптов
Память и контекст	Автоматическое сохранение каждого вызова, сжатие истории (TF‑IDF), поиск фактов, снэпшоты, бессрочный архив с восстановлением
RAG	Индексация PDF, DOCX, EPUB, TXT, MD через ChromaDB + sentence-transformers, ответы на вопросы по документам
Веб	Извлечение текста (trafilatura/readability), таблицы, изображения, поиск через DuckDuckGo / Brave, чтение RSS
Базы данных	Read‑only запросы к SQLite, DuckDB, CSV, Parquet, MS Access (с защитой от DML/DDL)
Офис	Чтение DOCX, XLSX, PPTX, конвертация в PDF (LibreOffice)
Почта и календарь	IMAP/POP3/SMTP (хранение паролей в keyring), ICS, задачи Outlook
Shell	PowerShell / CMD с фильтрацией опасных команд и обязательным подтверждением
Инструменты для LLM	Проверка логики (противоречия, арифметика), изолированное выполнение Python‑кода, оркестратор на естественном языке (с поддержкой !command), асинхронные задачи (пауза/возобновление), планировщик (cron/интервалы), централизованное логирование, администрирование (health, метрики, алерты Telegram/Slack), watchdog авто‑восстановления
Безопасность	Белый список разрешённых путей, блокировка системных папок, защита от ZipSlip, SSRF, SQL‑инъекций, детектор галлюцинаций (пути‑заполнители)
🖥️ Требования и установка
Проект предназначен для установки в C:\Tools (на Windows). Все примеры путей используют эту директорию.

Зависимости
Python 3.10+ (автоматически создаётся .venv)

Основные Python‑пакеты: watchdog, psutil, requests, beautifulsoup4, feedparser, icalendar, openpyxl, python-docx, python-pptx, pytesseract, Pillow, mutagen, duckdb, pyodbc, patool, py7zr, rarfile, playwright, pandas, sentence-transformers, chromadb, pypdf, pdfplumber, ebooklib, keyring, xxhash, cryptography.

Внешние утилиты (опционально, но нужны для некоторых функций):

ffmpeg – видео, миниатюры

pandoc + wkhtmltopdf – экспорт диалогов в PDF

rclone – облачная синхронизация

Tesseract – OCR

LibreOffice – конвертация Office → PDF

Браузеры Playwright (устанавливаются после playwright install chromium)

Быстрый старт (Windows)
cmd
cd C:\Tools
git clone https://github.com/yourname/mcp-unified-server.git
cd mcp-unified-server
setup.bat
# В меню: 1 (создать venv) → 2 (скачать зависимости) → 8 (запустить)
После установки настройте переменные окружения (файл .env или системные):

ini
MCP_ALLOWED_PATHS=C:\Tools;C:\
LLM_ENDPOINT=http://localhost:1234/v1/chat/completions   # для LM Studio
MCP_AUTO_MEMORY=true
MCP_VERBOSE_DEFAULT=true
MCP_MEMORY_PATH=C:\Tools\mcp_memory.db
MCP_TASK_DB=C:\Tools\mcp_tasks.db
MCP_RAG_DB_PATH=C:\Tools\mcp_rag_db
MCP_TRASH_PATH=C:\Tools\.mcp_trash
🔌 Интеграция с MCP‑клиентами
LM Studio
Откройте Настройки → MCP Servers → Edit config file

Вставьте:

json
{
  "mcpServers": {
    "mcp_unified": {
      "command": "C:\\Tools\\mcp-unified-server\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Tools\\mcp-unified-server\\mcp_fs_server.py"],
      "env": {
        "MCP_ALLOWED_PATHS": "C:\\Tools;C:\\",
        "LLM_ENDPOINT": "http://localhost:1234/v1/chat/completions",
        "MCP_AUTO_MEMORY": "true"
      }
    }
  }
}
Claude Desktop
Файл %APPDATA%\Claude\claude_desktop_config.json (аналогично, путь к Python в вашей установке)

Другие клиенты
Любой MCP‑клиент, поддерживающий запуск дочерних процессов через STDIO.
Нужна помощь в тестировании с Continue, Zed, Cursor и др.

🧪 Примеры использования (через LLM)
!search_files path=C:\Tools pattern=*.py recursive=true – поиск файлов

найди дубликаты в D:\Downloads – интеллектуальный поиск дубликатов

синхронизируй C:\Projects с D:\Backup в режиме mirror – вызов sync_directories

проиндексируй папку C:\Books для RAG – создание векторной базы

отправь письмо на example@domain.com с темой "Отчёт" – через SMTP

запланируй empty_trash каждую неделю в воскресенье – планировщик

🐧 Linux / macOS (экспериментально)
Сервер написан на кроссплатформенном Python, но не тестировался на Linux/macOS. Ожидаются различия в:

путях (используйте os.path.join или Path),

shell‑командах (вместо powershell – bash),

установке зависимостей (нужен аналог setup.bat).

Для запуска (вручную):

bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt   # или установите нужные пакеты вручную
.venv/bin/python mcp_fs_server.py
Помощь сообщества в адаптации для Linux/macOS и создании установщика очень приветствуется!

🤝 Как помочь проекту
🐛 Тестирование на разных MCP‑клиентах (Claude Desktop, Continue, Zed) и ОС (Linux, macOS)

🔒 Аудит безопасности – особенно опасных операций (shell, удаление, сеть)

📚 Документация – переводы, примеры, улучшение README

🧪 Написание тестов (unit, интеграционных)

🌍 Локализация интерфейса и сообщений

🛠️ Портирование setup.bat на bash/PowerShell Core для Unix‑подобных систем

💡 Новые идеи и инструменты – создавайте свои модули и делитесь

📄 Лицензия
Проект распространяется под MIT License.
Автор: Claus (claus@mcp.local)
Контакты: Issues / Pull Requests на GitHub.
