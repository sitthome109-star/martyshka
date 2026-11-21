"""
РАЗРАБОТКА — инструменты агента Студии для работы с кодом (как у настоящего программиста):
поиск по коду, шаблоны файлов, Python, git, HTTP-запросы, проверка сайтов глазами, фоновые серверы, долгая память.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

from .. import config

MAX_OUT = 14000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".cache", "models"}
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


def _walk(root: Path):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            yield Path(dirpath) / f


def _decode(b: bytes) -> str:
    return (b or b"").decode("utf-8", "replace")


# ---------- фоновые команды ----------
_bg: dict[int, dict] = {}
_bg_next = [1]


def _bg_reader(job: dict) -> None:
    for line in iter(job["proc"].stdout.readline, b""):
        job["out"].append(_decode(line).rstrip())
    job["proc"].wait()


def run(name: str, args: dict, path_fn, workspace_fn) -> dict:
    ws = workspace_fn()

    if name == "grep_code":
        root = path_fn(args.get("path") or str(ws))
        try:
            rx = re.compile(args["pattern"], re.I)
        except re.error as e:
            return {"text": f"Плохое регулярное выражение: {e}", "ok": False}
        globs = _expand_glob(args.get("glob") or "")
        hits, t0 = [], time.time()
        files = [root] if root.is_file() else _walk(root)
        for f in files:
            if globs and not any(fnmatch.fnmatch(f.name, g) or fnmatch.fnmatch(str(f), g) for g in globs):
                continue
            if not globs and f.suffix.lower() not in TEXT_EXT:
                continue
            try:
                if f.stat().st_size > 2_000_000:
                    continue
                for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{f}:{i}: {line.strip()[:220]}")
                        if len(hits) >= 200:
                            break
            except Exception:
                continue
            if len(hits) >= 200 or time.time() - t0 > 15:
                break
        return {"text": "\n".join(hits) or "Ничего не нашла.", "ok": bool(hits)}

    if name == "glob_files":
        root = path_fn(args.get("path") or str(ws))
        pats = _expand_glob(args["pattern"])
        found = []
        for f in _walk(root):
            rel = str(f.relative_to(root)).replace("\\", "/")
            if any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(f.name, p) for p in pats):
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
        cmd = [str(edge), "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={prof}",
               f"--window-size={w},{h}", "--virtual-time-budget=4000", f"--screenshot={shot}", target]
        try:
            subprocess.run(cmd, capture_output=True, timeout=60, creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return {"text": "Страница не открылась за минуту.", "ok": False}
        if not shot.exists():
            return {"text": "Снимок страницы не получился.", "ok": False}
        from .. import deepseek
        from .tools import _thumb
        try:
            seen = deepseek.look(shot, "Это снимок веб-страницы/игры, которую сделал программист. " + args["question"]
                                 + " Опиши конкретно: что видно, что выглядит сломанным или некрасивым, "
                                   "есть ли сообщения об ошибках, пустые места, наезжающий текст.")
        except Exception as e:
            seen = f"(посмотреть не вышло: {e})"
        img = _thumb(shot)
        return {"text": f"Снимок {w}×{h}: {shot}\nЧто видно: {seen}", "ok": True, "image": img, "file": str(shot)}

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
