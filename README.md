# MCP Unified Server

**Мощный all-in-one MCP-сервер для локальных LLM**

90+ инструментов для работы с файлами, RAG, планированием и автоматизацией задач.

---

### ✨ Основные возможности

- **Файловая система**: поиск, копирование, перемещение, архивация, корзина, версии файлов
- **RAG** на базе ChromaDB (индексация PDF, DOCX, EPUB и др.)
- **Оркестратор** — выполняй сложные задачи на естественном языке
- **Планировщик задач** и фоновое выполнение
- **Память диалогов** (SQLite)
- Поддержка **!command** и JSON-RPC 2.0

Идеально работает с **LM Studio**, Claude Desktop и другими MCP-клиентами.

---

### 🚀 Быстрый старт

1. Скачай репозиторий
2. Запусти `setup.bat` (Windows) или `setup.sh`
3. Запусти сервер:
   
bash
   python mcp_fs_server.py
  
4. Подключи в LM Studio как MCP-сервер

Готово! Теперь можешь писать:
- `!search_files path=C:\ pattern=*.pdf`
- Или просто описать задачу обычными словами

---

### English

# MCP Unified Server

**Powerful all-in-one MCP server for local LLMs**

90+ tools for file management, RAG, task orchestration and automation.

---

### ✨ Key Features

- **File System**: search, move, copy, archive, trash, versioning
- **RAG Engine** with ChromaDB (PDF, DOCX, EPUB, etc.)
- **Natural Language Orchestrator** — execute complex tasks
- **Task Scheduler** + background execution
- **Conversation Memory** (SQLite)
- Supports **!command** and standard JSON-RPC 2.0

Works great with **LM Studio**, Claude Desktop and other MCP clients.

---

### 🚀 Quick Start

1. Clone or download the repository
2. Run `setup.bat` (Windows) or `setup.sh`
3. Start the server:
   
bash
   python mcp_fs_server.py
  
4. Connect it in LM Studio as MCP server

Now you can use:
- `!search_files path=C:\ pattern=*.pdf`
- Or just describe what you want in plain English

---

**Лицензия**: MIT  
**Язык**: Python 3.10+

⭐️ Если проект понравился — поставь звезду!
---

### Как испEditь:
1. Перейди в свой репозиторий
2. Нажми на README.md → Commit changes Удали старое содержимое и вставь этот текст
4. Нажми **Commit changes**
