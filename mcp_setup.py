#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Setup Helper v6.4 – Исправлена работа с pip в виртуальном окружении
Поддерживает online (скачивание) и offline (установку из папки python_deps)
"""
import os
import sys
import ast
import subprocess
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PY_EXE = str(VENV / "Scripts" / "python.exe") if sys.platform == "win32" else str(VENV / "bin" / "python3")
PIP_CMD = [PY_EXE, "-m", "pip", "--no-input"]
DEPS_DIR = ROOT / "python_deps"

BASE_DEPS = {
    "watchdog", "psutil", "requests", "xxhash", "cryptography", "keyring",
    "beautifulsoup4", "feedparser", "icalendar", "openpyxl", "python-docx",
    "python-pptx", "pytesseract", "Pillow", "mutagen", "duckdb", "pyodbc",
    "patool", "py7zr", "rarfile", "playwright", "pandas",
    # RAG dependencies (новые)
    "sentence-transformers", "chromadb", "pypdf", "pdfplumber", "ebooklib"
}

def find_plugin_deps():
    deps = set()
    for search_dir in [ROOT, ROOT / "mcp_plugins"]:
        if not search_dir.is_dir():
            continue
        for py_file in search_dir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name) and target.id == "__mcp_plugin__":
                                if isinstance(node.value, ast.Dict):
                                    keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
                                    if "dependencies" in keys:
                                        idx = keys.index("dependencies")
                                        val = node.value.values[idx]
                                        if isinstance(val, ast.List):
                                            for elem in val.elts:
                                                raw = getattr(elem, 'value', getattr(elem, 's', ''))
                                                if isinstance(raw, str):
                                                    pkg = raw.split("^")[0].split("~")[0].split("=")[0].strip()
                                                    deps.add(pkg)
            except Exception as e:
                print(f"Предупреждение: {py_file.name}: {e}")
    return deps

def get_full_deps():
    return sorted(BASE_DEPS | find_plugin_deps())

def run(cmd, check=False):
    print(f">>> {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    if check and proc.returncode != 0:
        sys.exit(proc.returncode)
    return proc

def ensure_venv():
    if not VENV.exists():
        print("📦 Создаю виртуальное окружение...")
        run([sys.executable, "-m", "venv", str(VENV)], check=True)
    if not Path(PY_EXE).exists():
        print(f"❌ Ошибка: {PY_EXE} не найден")
        sys.exit(1)

def check_pip():
    """Проверяет, доступен ли pip в виртуальном окружении."""
    result = subprocess.run([PY_EXE, "-m", "pip", "--version"], capture_output=True)
    if result.returncode != 0:
        print("❌ Pip не найден в виртуальном окружении.")
        print("   Запустите опцию 1 (Recreate virtual environment) в setup.bat")
        sys.exit(1)
    return True

def check_import(package: str) -> bool:
    """Проверяет импорт пакета, преобразуя имя pip в имя модуля."""
    import_name = package.split('[')[0].replace('-', '_')
    mapping = {
        "beautifulsoup4": "bs4",
        "Pillow": "PIL",
        "python_docx": "docx",
        "python_pptx": "pptx",
        "patool": None,       # не Python-модуль
    }
    if import_name in mapping:
        if mapping[import_name] is None:
            return True   # patool пропускаем
        import_name = mapping[import_name]
    try:
        subprocess.run([PY_EXE, "-c", f"import {import_name}"],
                       capture_output=True, check=True)
        return True
    except:
        return False

def check_and_install_missing():
    ensure_venv()
    check_pip()
    deps = get_full_deps()
    missing = [dep for dep in deps if not check_import(dep)]
    if not missing:
        print("✅ Все зависимости уже установлены.")
        return
    print(f"⚠️ Отсутствуют {len(missing)} пакетов: {', '.join(missing)}")
    print("🔧 Устанавливаю недостающие пакеты...")
    if DEPS_DIR.exists() and any(DEPS_DIR.glob("*.whl")):
        cmd = PIP_CMD + ["install", "--no-index", "--find-links", str(DEPS_DIR), "--no-build-isolation"] + missing
    else:
        cmd = PIP_CMD + ["install", "--prefer-binary"] + missing
    result = run(cmd)
    if result.returncode != 0:
        print("❌ Ошибка при установке.")
        sys.exit(1)
    else:
        print("✅ Недостающие зависимости успешно установлены.")

def online_mode():
    ensure_venv()
    check_pip()
    deps = get_full_deps()
    print(f"🌐 Скачиваю {len(deps)} пакетов...")
    # Обновляем pip, setuptools, wheel
    run(PIP_CMD + ["install", "--upgrade", "pip", "setuptools", "wheel"], check=True)
    DEPS_DIR.mkdir(exist_ok=True)
    cmd = PIP_CMD + ["download", "-d", str(DEPS_DIR), "--prefer-binary"] + deps
    result = run(cmd)
    if result.returncode != 0:
        print("❌ Ошибка при скачивании")
        sys.exit(1)
    # Также скачиваем сами pip, setuptools, wheel для offline
    run(PIP_CMD + ["download", "-d", str(DEPS_DIR), "--prefer-binary", "pip", "setuptools", "wheel"])
    print(f"✅ Пакеты скачаны в {DEPS_DIR}")
    print("Запустите mcp_setup.py --offline для установки.")

def offline_mode():
    if not DEPS_DIR.exists() or not any(DEPS_DIR.glob("*.whl")):
        print("❌ Папка python_deps пуста. Сначала --online")
        sys.exit(1)
    ensure_venv()
    check_pip()
    deps = get_full_deps()
    print(f"📦 Устанавливаю {len(deps)} пакетов из {DEPS_DIR}...")
    # Обновляем pip, setuptools, wheel из локальной папки
    upgrade_cmd = PIP_CMD + ["install", "--no-index", "--find-links", str(DEPS_DIR),
                             "--upgrade", "pip", "setuptools", "wheel"]
    run(upgrade_cmd, check=True)
    # Устанавливаем основные зависимости
    install_cmd = PIP_CMD + ["install", "--no-index", "--find-links", str(DEPS_DIR),
                             "--no-build-isolation"] + deps
    run(install_cmd, check=True)
    # Устанавливаем браузеры Playwright (если playwright был установлен)
    print("🎭 Устанавливаю браузеры Playwright...")
    playwright_install = subprocess.run([PY_EXE, "-m", "playwright", "install", "chromium"],
                                        capture_output=True, text=True)
    if playwright_install.returncode != 0:
        print("⚠️ Не удалось установить браузеры Playwright автоматически.")
        print("   Вы можете запустить вручную: .venv\\Scripts\\python -m playwright install chromium")
    else:
        print("✅ Браузеры Playwright установлены.")
    print("✅ Зависимости установлены.")

def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--online", action="store_true")
    group.add_argument("--offline", action="store_true")
    group.add_argument("--check", action="store_true")
    if len(sys.argv) == 1:
        parser.print_help()
        input("\nНажмите Enter для выхода...")
        sys.exit(0)
    args = parser.parse_args()
    if args.online:
        online_mode()
    elif args.offline:
        offline_mode()
    elif args.check:
        check_and_install_missing()

if __name__ == "__main__":
    main()