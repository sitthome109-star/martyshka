"""
РАЗРАБОТКА — инструменты агента Студии для работы с кодом (как у настоящего программиста):
карта проекта, поиск по коду, шаблоны файлов, автопроверка кода/тестов, Python, git, HTTP-запросы,
проверка сайтов глазами (с ошибками JS-консоли), фоновые серверы, долгая память.
"""
from __future__ import annotations

import ast
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

from .. import config

MAX_OUT = 14000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".cache", "models",
             "coverage", ".pytest_cache", ".mypy_cache", ".ruff_cache", "obj", "bin"}
TEXT_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".scss", ".json", ".md", ".txt", ".cs", ".cpp",
            ".c", ".h", ".hpp", ".java", ".kt", ".go", ".rs", ".rb", ".php", ".lua", ".sh", ".ps1", ".bat", ".yml",
            ".yaml", ".toml", ".ini", ".cfg", ".xml", ".svg", ".vue", ".svelte", ".sql", ".gd", ".csv"}
NO_WINDOW = 0x08000000


def _expand_glob(g: str) -> list[str]:
    """*.{js,ts} → [*.js, *.ts]"""
    m = re.search(r"\{([^}]+)\}", g or "")
    if not m:
        return [g] if g else []
    return [g[:m.start()] + part + g[m.end():] for part in m.group(1).split(",")]


def _glob_match(rel: str, name: str, pat: str) -> bool:
    """Сопоставление пути с шаблоном, включая корректную обработку **/ в корне."""
    pat = pat.replace("\\", "/")
    if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat):
        return True
    if pat.startswith("**/"):
        sub = pat[3:]
        if fnmatch.fnmatch(rel, sub) or fnmatch.fnmatch(name, sub):
            return True
    if "/**/" in pat:
        collapsed = pat.replace("/**/", "/")
        if fnmatch.fnmatch(rel, collapsed):
            return True
    return False


def _walk(root: Path):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for f in sorted(files):
            yield Path(dirpath) / f


def _decode(b: bytes) -> str:
    return (b or b"").decode("utf-8", "replace")


def _extract_symbols(path: Path) -> list[str]:
    """Короткий список классов/функций/экспортов в файле для карты проекта."""
    ext = path.suffix.lower()
    try:
        if path.stat().st_size > 400_000:
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    syms: list[str] = []
    if ext == ".py":
        try:
            tree = ast.parse(text)
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                               and not n.name.startswith("__")][:8]
                    mstr = f"({', '.join(methods)})" if methods else ""
                    syms.append(f"class {node.name}{mstr} :{node.lineno}")
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_") or len(syms) < 12:
                        syms.append(f"def {node.name}() :{node.lineno}")
        except SyntaxError as e:
            syms.append(f"⚠ SyntaxError :{e.lineno}: {e.msg}")
    elif ext in (".js", ".ts", ".jsx", ".tsx", ".mjs"):
        rx = re.compile(
            r"^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:function\s+([A-Za-z_$]\w*)|"
            r"class\s+([A-Za-z_$]\w*)|(?:const|let|var)\s+([A-Za-z_$]\w*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$]\w*)\s*=>)",
            re.M)
        for m in rx.finditer(text):
            name = m.group(1) or m.group(2) or m.group(3)
            if name:
                ln = text.count("\n", 0, m.start()) + 1
                syms.append(f"{name} :{ln}")
    elif ext in (".cs", ".java", ".kt"):
        rx = re.compile(r"^\s*(?:public|private|internal|protected)?\s*(?:static\s+)?(?:class|struct|interface|enum|record)\s+(\w+)", re.M)
        for m in rx.finditer(text):
            ln = text.count("\n", 0, m.start()) + 1
            syms.append(f"type {m.group(1)} :{ln}")
    elif ext in (".rs", ".go"):
        rx = re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?(?:fn|func|struct|enum|trait|type)\s+(\w+)", re.M)
        for m in rx.finditer(text):
            ln = text.count("\n", 0, m.start()) + 1
            syms.append(f"{m.group(1)} :{ln}")
    elif ext == ".html":
        title = re.search(r"<title>(.*?)</title>", text, re.I | re.S)
        scripts = len(re.findall(r"<script\b", text, re.I))
        if title:
            syms.append(f"title={title.group(1).strip()[:40]}")
        if scripts:
            syms.append(f"scripts={scripts}")
    return syms[:18]


def _project_map(root: Path, max_files: int = 120, with_symbols: bool = True) -> dict:
    if not root.exists():
        return {"text": f"Нет такой папки: {root}", "ok": False}
    if root.is_file():
        syms = _extract_symbols(root)
        return {"text": f"{root}\n" + ("\n".join("  " + s for s in syms) or "(символов не найдено)"), "ok": True}
    stack = []
    if (root / ".git").exists():
        stack.append("git")
    if (root / "package.json").exists():
        stack.append("Node.js")
    if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists() or any(root.glob("*.py")):
        stack.append("Python")
    if (root / ".venv").exists() or (root / "venv").exists():
        stack.append("venv")
    if any(root.glob("*.csproj")) or any(root.glob("*.cs")):
        stack.append("C#")
    if (root / "Cargo.toml").exists():
        stack.append("Rust")
    if (root / "index.html").exists():
        stack.append("HTML")
    if (root / "tests").exists() or any(root.glob("test_*.py")):
        stack.append("tests")

    lines = [f"Проект: {root}"]
    if stack:
        lines.append("Стек: " + ", ".join(stack))
    count = 0
    total = 0
    for f in _walk(root):
        total += 1
        if count >= max_files:
            continue
        count += 1
        rel = str(f.relative_to(root)).replace("\\", "/")
        try:
            sz = f.stat().st_size
            sz_s = f"{sz / 1024:.1f} КБ" if sz >= 1024 else f"{sz} Б"
        except Exception:
            sz_s = "?"
        row = f"  {rel} ({sz_s})"
        if with_symbols and f.suffix.lower() in (".py", ".js", ".ts", ".jsx", ".tsx", ".cs", ".rs", ".go", ".html"):
            syms = _extract_symbols(f)
            if syms:
                row += "  →  " + ", ".join(syms[:10]) + (" …" if len(syms) > 10 else "")
        lines.append(row)
    if total > count:
        lines.append(f"  … и ещё {total - count} файлов")
    return {"text": "\n".join(lines)[:MAX_OUT], "ok": True}


def _check_code(target: Path, run_tests: bool = True) -> dict:
    """Быстрая проверка синтаксиса (Python / JSON / JS / inline JS в HTML) и запуск тестов проекта."""
    if not target.exists():
        return {"text": f"Нет такого пути: {target}", "ok": False}
    files = [target] if target.is_file() else list(_walk(target))
    root = target.parent if target.is_file() else target
    node = shutil.which("node")

    errors: list[str] = []
    checked = {"py": 0, "json": 0, "js": 0, "html": 0}

    for f in files:
        ext = f.suffix.lower()
        rel = str(f.relative_to(root)).replace("\\", "/") if f != root else f.name
        try:
            if f.stat().st_size > 2_000_000:
                continue
        except Exception:
            continue
        if ext == ".py":
            checked["py"] += 1
            try:
                ast.parse(f.read_text(encoding="utf-8", errors="replace"), filename=str(f))
            except SyntaxError as e:
                errors.append(f"Python {rel}:{e.lineno}: {e.msg}")
        elif ext == ".json":
            checked["json"] += 1
            try:
                json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                errors.append(f"JSON {rel}: {e}")
        elif ext in (".js", ".mjs") and node:
            checked["js"] += 1
            r = subprocess.run([node, "--check", str(f)], capture_output=True, timeout=15, creationflags=NO_WINDOW)
            if r.returncode != 0:
                errors.append(f"JS {rel}: {_decode(r.stderr).strip()[:300]}")
        elif ext == ".html" and node:
            checked["html"] += 1
            text = f.read_text(encoding="utf-8", errors="replace")
            for idx, m in enumerate(re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", text, re.I | re.S), 1):
                code = m.group(1).strip()
                if not code:
                    continue
                with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tf:
                    tf.write(code)
                    tmp_js = tf.name
                try:
                    r = subprocess.run([node, "--check", tmp_js], capture_output=True, timeout=15,
                                       creationflags=NO_WINDOW)
                    if r.returncode != 0:
                        errors.append(f"HTML <script #{idx}> в {rel}: {_decode(r.stderr).strip()[:300]}")
                finally:
                    try:
                        os.unlink(tmp_js)
                    except Exception:
                        pass

    report = [f"Проверено файлов: py={checked['py']}, json={checked['json']}, js={checked['js']}, html={checked['html']}."]
    if errors:
        report.append("ОШИБКИ СИНТАКСИСА:\n" + "\n".join("✗ " + e for e in errors[:30]))
    else:
        report.append("✓ Синтаксических ошибок не найдено.")

    tests_ok = True
    if run_tests and not errors and target.is_dir():
        has_py_tests = (root / "tests").exists() or any(root.glob("test_*.py"))
        if has_py_tests:
            py_exe = root / ".venv" / "Scripts" / "python.exe"
            exe = str(py_exe) if py_exe.exists() else sys.executable
            cmd = [exe, "-m", "unittest", "discover", "-s", "tests" if (root / "tests").exists() else ".", "-v"]
            try:
                r = subprocess.run(cmd, cwd=str(root), capture_output=True, timeout=120, creationflags=NO_WINDOW,
                                   env=dict(os.environ, PYTHONIOENCODING="utf-8"))
                out = (_decode(r.stdout) + "\n" + _decode(r.stderr)).strip()
                tests_ok = r.returncode == 0
                report.append(("✓ Python-тесты прошли:\n" if tests_ok else "✗ Python-тесты упали:\n") + out[-5000:])
            except subprocess.TimeoutExpired:
                tests_ok = False
                report.append("✗ Python-тесты не уложились в 120 с.")
        pkg = root / "package.json"
        if pkg.exists() and shutil.which("npm"):
            try:
                pj = json.loads(pkg.read_text(encoding="utf-8"))
                tcmd = (pj.get("scripts") or {}).get("test", "")
                if tcmd and "no test specified" not in tcmd.lower():
                    r = subprocess.run(["npm", "test"], cwd=str(root), capture_output=True, timeout=120,
                                       creationflags=NO_WINDOW)
                    out = (_decode(r.stdout) + "\n" + _decode(r.stderr)).strip()
                    if r.returncode != 0:
                        tests_ok = False
                    report.append(("✓ npm test прошёл:\n" if r.returncode == 0 else "✗ npm test упал:\n") + out[-4000:])
            except Exception:
                pass

    return {"text": "\n\n".join(report)[:MAX_OUT], "ok": (not errors) and tests_ok}


# ---------- фоновые команды ----------
_bg: dict[int, dict] = {}
_bg_next = [1]


def _bg_reader(job: dict) -> None:
    for line in iter(job["proc"].stdout.readline, b""):
        job["out"].append(_decode(line).rstrip())
    job["proc"].wait()


def run(name: str, args: dict, path_fn, workspace_fn) -> dict:
    ws = workspace_fn()

    if name == "project_map":
        root = path_fn(args.get("path") or str(ws))
        max_files = min(max(int(args.get("max_files") or 120), 10), 400)
        return _project_map(root, max_files=max_files, with_symbols=bool(args.get("symbols", True)))

    if name == "check_code":
        target = path_fn(args.get("path") or str(ws))
        return _check_code(target, run_tests=bool(args.get("run_tests", True)))

    if name == "grep_code":
        root = path_fn(args.get("path") or str(ws))
        flags = 0 if args.get("case_sensitive") else re.I
        try:
            rx = re.compile(args["pattern"], flags)
        except re.error as e:
            return {"text": f"Плохое регулярное выражение: {e}", "ok": False}
        globs = _expand_glob(args.get("glob") or "")
        ctx_n = min(max(int(args.get("context") or 0), 0), 5)
        hits, t0, truncated = [], time.time(), False
        files = [root] if root.is_file() else _walk(root)
        for f in files:
            rel = str(f.relative_to(root)).replace("\\", "/") if (not root.is_file() and f != root) else f.name
            if globs and not any(_glob_match(rel, f.name, g) for g in globs):
                continue
            if not globs and f.suffix.lower() not in TEXT_EXT:
                continue
            try:
                if f.stat().st_size > 2_000_000:
                    continue
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
                for i, line in enumerate(lines, 1):
                    if rx.search(line):
                        if ctx_n:
                            lo, hi = max(1, i - ctx_n), min(len(lines), i + ctx_n)
                            block = [f"{f}:{j}{'>' if j == i else ':'} {lines[j - 1][:220]}" for j in range(lo, hi + 1)]
                            hits.append("\n".join(block))
                        else:
                            hits.append(f"{f}:{i}: {line.strip()[:220]}")
                        if len(hits) >= 200:
                            truncated = True
                            break
            except Exception:
                continue
            if len(hits) >= 200 or time.time() - t0 > 15:
                truncated = True
                break
        sep = "\n---\n" if ctx_n else "\n"
        text = sep.join(hits)
        if truncated and hits:
            text += "\n…(показаны первые совпадения, уточни pattern или path/glob)"
        return {"text": text[:MAX_OUT] or "Ничего не нашла.", "ok": bool(hits)}

    if name == "glob_files":
        root = path_fn(args.get("path") or str(ws))
        if not root.exists():
            return {"text": f"Нет такой папки: {root}", "ok": False}
        pats = _expand_glob(args["pattern"])
        found = []
        for f in _walk(root):
            rel = str(f.relative_to(root)).replace("\\", "/")
            if any(_glob_match(rel, f.name, p) for p in pats):
                found.append(str(f))
                if len(found) >= 300:
                    break
        return {"text": "\n".join(found) or "Ничего не нашла.", "ok": bool(found)}

    if name == "run_python":
        cwd = path_fn(args.get("cwd") or str(ws))
        timeout = min(max(int(args.get("timeout_sec") or 120), 5), 900)
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
            tf.write(args["code"])
            script = tf.name
        try:
            r = subprocess.run([sys.executable, script], cwd=str(cwd), capture_output=True, timeout=timeout,
                               creationflags=NO_WINDOW, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        except subprocess.TimeoutExpired:
            return {"text": f"Не закончилось за {timeout} с.", "ok": False}
        finally:
            try:
                os.unlink(script)
            except Exception:
                pass
        out, err = _decode(r.stdout), _decode(r.stderr)
        text = (out[-MAX_OUT:] + ("\nОШИБКИ:\n" + err[-6000:] if err.strip() else "")).strip()
        return {"text": f"Код выхода {r.returncode}\n{text or '(без вывода)'}", "ok": r.returncode == 0}

    if name == "git_command":
        cwd = path_fn(args.get("cwd") or str(ws))
        import shlex
        try:
            parts = shlex.split(args["args"], posix=True)
        except ValueError:
            parts = args["args"].split()
        if parts and parts[0] == "git":
            parts = parts[1:]
        r = subprocess.run(["git", "-c", "core.quotepath=false", *parts], cwd=str(cwd), capture_output=True,
                           timeout=120, creationflags=NO_WINDOW)
        text = (_decode(r.stdout)[-MAX_OUT:] + ("\n" + _decode(r.stderr)[-3000:] if r.stderr else "")).strip()
        return {"text": f"Код выхода {r.returncode}\n{text or '(без вывода)'}", "ok": r.returncode == 0}

    if name == "http_request":
        import httpx
        try:
            headers = json.loads(args.get("headers") or "{}")
        except Exception:
            headers = {}
        body = args.get("body")
        kw: dict = {"headers": headers, "timeout": 30, "follow_redirects": True}
        if body:
            try:
                kw["json"] = json.loads(body)
            except Exception:
                kw["content"] = body.encode("utf-8")
        try:
            r = httpx.request((args.get("method") or "GET").upper(), args["url"], **kw)
        except Exception as e:
            return {"text": f"Запрос не прошёл: {e}", "ok": False}
        hdr = "\n".join(f"{k}: {v}" for k, v in list(r.headers.items())[:12])
        return {"text": f"HTTP {r.status_code}\n{hdr}\n\n{r.text[:MAX_OUT]}", "ok": r.status_code < 400}

    if name == "page_preview":
        target = args["target"].strip()
        if not re.match(r"https?://|file:", target):
            p = path_fn(target)
            if not p.exists():
                return {"text": f"Нет такого файла: {p}", "ok": False}
            target = p.as_uri()
        edge = next((p for p in (Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft/Edge/Application/msedge.exe",
                                 Path(os.environ.get("ProgramFiles", "")) / "Microsoft/Edge/Application/msedge.exe")
                     if p.exists()), None)
        if edge is None:
            return {"text": "Не нашла Microsoft Edge для проверки страницы.", "ok": False}
        w, h = int(args.get("width") or 1280), int(args.get("height") or 800)
        shot = Path(tempfile.gettempdir()) / f"martyshka_preview_{int(time.time() * 1000)}.png"
        prof = Path(tempfile.gettempdir()) / "martyshka_edge_profile"
        cmd = [str(edge), "--headless=new", "--disable-gpu", "--hide-scrollbars",
               "--enable-logging=stderr", "--v=0", f"--user-data-dir={prof}",
               f"--window-size={w},{h}", "--virtual-time-budget=4000", f"--screenshot={shot}", target]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return {"text": "Страница не открылась за минуту.", "ok": False}
        if not shot.exists():
            return {"text": "Снимок страницы не получился.", "ok": False}
        # Собираем сообщения и ошибки из JS-консоли страницы (без шума расширений)
        console_msgs: list[str] = []
        for line in _decode(r.stderr).splitlines():
            if "CONSOLE" in line and "chrome-extension://" not in line:
                m = re.search(r"CONSOLE(?:\(\d+\)|:\d+)\]\s*(.+)", line)
                console_msgs.append((m.group(1) if m else line).strip()[:300])
        console_note = ("\nКонсоль JS:\n" + "\n".join("  " + c for c in console_msgs[:20])) if console_msgs else ""
        from .. import deepseek
        from .tools import _thumb
        try:
            seen = deepseek.look(shot, "Это снимок веб-страницы/игры, которую сделал программист. " + args["question"]
                                 + " Опиши конкретно: что видно, что выглядит сломанным или некрасивым, "
                                   "есть ли сообщения об ошибках, пустые места, наезжающий текст."
                                 + (f"\nТакже в консоли JS появились сообщения:{console_note}" if console_note else ""))
        except Exception as e:
            seen = f"(посмотреть не вышло: {e})"
        img = _thumb(shot)
        return {"text": f"Снимок {w}×{h}: {shot}\nЧто видно: {seen}{console_note}", "ok": True,
                "image": img, "file": str(shot)}

    if name == "start_background":
        cwd = path_fn(args.get("cwd") or str(ws))
        proc = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                                 "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + args["command"]],
                                cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=NO_WINDOW)
        jid = _bg_next[0]
        _bg_next[0] += 1
        job = {"proc": proc, "out": deque(maxlen=400), "cmd": args["command"], "cwd": str(cwd)}
        _bg[jid] = job
        threading.Thread(target=_bg_reader, args=(job,), daemon=True).start()
        time.sleep(2.5)
        tail = "\n".join(list(job["out"])[-40:])
        return {"text": f"Запущено в фоне, номер {jid} (pid {proc.pid}).\n{tail}", "ok": True}

    if name == "background_output":
        job = _bg.get(int(args["id"]))
        if not job:
            return {"text": "Нет такой фоновой команды.", "ok": False}
        alive = job["proc"].poll() is None
        tail = "\n".join(list(job["out"])[-120:])
        return {"text": f"{'Работает' if alive else f'Закончилась, код {job['proc'].returncode}'}\n{tail[-MAX_OUT:]}",
                "ok": True}

    if name == "background_stop":
        job = _bg.get(int(args["id"]))
        if not job:
            return {"text": "Нет такой фоновой команды.", "ok": False}
        if job["proc"].poll() is None:
            subprocess.run(["taskkill", "/PID", str(job["proc"].pid), "/T", "/F"], capture_output=True,
                           creationflags=NO_WINDOW)          # только свой процесс и его дети
        return {"text": "Остановлено.", "ok": True}

    if name == "memory_search":
        from .. import longmem
        return {"text": longmem.search(args["query"]), "ok": True}

    return {"text": f"Нет такого инструмента: {name}", "ok": False}
