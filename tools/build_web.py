"""Сборка веб-версии, которая работает без интернета (PWA).

    python tools/build_web.py                     → dist/
    python tools/build_web.py --base-url my-repo  → для https://<логин>.github.io/my-repo/

Что делает поверх `flet publish`:
  1. --no-cdn: Python (Pyodide) и движок отрисовки (CanvasKit) лежат рядом с игрой.
  2. Кладёт flet внутрь архива игры, а msgpack — к Pyodide. Иначе при каждом
     запуске Pyodide скачивает flet из PyPI, и без сети игра не стартует.
  3. Скачивает шрифты эмодзи и символов — иначе вместо эмодзи будут квадратики.
  4. Убирает то, что игре не нужно (другие рендеры, отладочные символы…).
  5. Ставит свой service worker: при первом запуске он кэширует всё,
     дальше игра открывается без сети. Новая сборка подтягивается сама.

Нужен интернет только во время сборки.
"""

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import flet
from importlib.metadata import version as pkg_version

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "web"
PYODIDE_CDN = "https://cdn.jsdelivr.net/pyodide/v{version}/full/{file}"
FONTS_CDN = "https://fonts.gstatic.com/s/{path}"
FONT_FAMILIES = ("notocoloremoji", "notosanssymbols", "notosanssymbols2", "notosansmath", "notosans")
# У CJK-шрифтов берём только «базовые» кусочки (без номера): латиница, кириллица, греческий.
# Flutter иногда отдаёт кириллицу китайскому шрифту — без этих ~150 КБ она без сети не нарисуется.
CJK_FAMILIES = ("notosanssc", "notosanstc", "notosanshk", "notosansjp", "notosanskr")

# Flet всегда рисует через CanvasKit (canvaskit/ и canvaskit/chromium/), остальное не грузится.
UNUSED = [
    "flutter.js.map",
    "main.dart.wasm",
    "main.dart.mjs",
    "canvaskit/experimental_webparagraph",
    "canvaskit/skwasm.js",
    "canvaskit/skwasm.wasm",
    "canvaskit/skwasm_heavy.js",
    "canvaskit/skwasm_heavy.wasm",
    "canvaskit/wimp.js",
    "canvaskit/wimp.wasm",
    "pyodide/python",
    "pyodide/python.bat",
    "pyodide/python.exe",
    "pyodide/python_cli_entry.mjs",
    "pyodide/micropip-0.11.1-py3-none-any.whl",
    "pyodide/packaging-26.1-py3-none-any.whl",
]
UNUSED_GLOBS = ["**/*.symbols", "**/*.d.ts"]


sys.stdout.reconfigure(encoding="utf-8")


def log(msg: str) -> None:
    print(f"• {msg}", flush=True)


def download(url: str, dest: Path, sha256: str | None = None) -> None:
    """Скачать с кэшем в .cache/web, чтобы повторные сборки не ходили в сеть."""
    cached = CACHE / hashlib.sha1(url.encode()).hexdigest()
    if not cached.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=60) as r:
            cached.write_bytes(r.read())
    data = cached.read_bytes()
    if sha256 and hashlib.sha256(data).hexdigest() != sha256:
        cached.unlink()
        raise RuntimeError(f"Контрольная сумма не совпала: {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def run_flet_publish(out: Path, base_url: str) -> None:
    for d in (ROOT / "src").rglob("__pycache__"):
        shutil.rmtree(d)
    shutil.rmtree(out, ignore_errors=True)
    flet_exe = shutil.which("flet", path=str(Path(sys.executable).parent)) or "flet"
    cmd = [
        flet_exe, "publish", str(ROOT / "src" / "main.py"),
        "--no-cdn",
        "--distpath", str(out),
        "--base-url", base_url,
        "--app-name", "Бездонная шахта",
        "--app-short-name", "Шахта",
        "--app-description", "Idle-кликер: копай вниз бесконечно",
        "--pwa-background-color", "#0b0d10",
        "--pwa-theme-color", "#12151a",
    ]
    log("flet publish --no-cdn")
    subprocess.run(cmd, check=True, cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def bundle_python_packages(out: Path) -> None:
    """flet — внутрь архива игры (папка __pypackages__ попадает в sys.path), msgpack — к Pyodide."""
    archive = out / "app.tar.gz"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        with tarfile.open(archive) as tar:
            tar.extractall(tmp, filter="data")
        (tmp / "requirements.txt").unlink(missing_ok=True)  # не ставить flet из PyPI

        skip = shutil.ignore_patterns("__pycache__", "testing", "fastapi", "pytest_plugin.py")
        shutil.copytree(Path(flet.__file__).parent, tmp / "__pypackages__" / "flet", ignore=skip)
        log(f"flet {pkg_version('flet')} → __pypackages__")

        archive.unlink()
        with tarfile.open(archive, "w:gz") as tar:
            for item in sorted(tmp.iterdir()):
                tar.add(item, arcname=item.name)

    lock = json.loads((out / "pyodide" / "pyodide-lock.json").read_text(encoding="utf-8"))
    version = json.loads((out / "pyodide" / "package.json").read_text(encoding="utf-8"))["version"]
    pkg = lock["packages"]["msgpack"]
    download(PYODIDE_CDN.format(version=version, file=pkg["file_name"]),
             out / "pyodide" / pkg["file_name"], pkg["sha256"])
    log(f"msgpack {pkg['version']} → pyodide/")


def bundle_fonts(out: Path) -> None:
    """Flutter качает шрифты эмодзи по кусочкам; кладём нужные семейства в assets/fonts/."""
    main_js = (out / "main.dart.js").read_text(encoding="utf-8", errors="ignore")
    paths = sorted(set(re.findall(r'"([a-z0-9]+/v\d+/[A-Za-z0-9_-]+(?:\.\d+)?\.(?:woff2|ttf|otf))"', main_js)))
    paths = [
        p for p in paths
        if p.split("/")[0] in FONT_FAMILIES
        or (p.split("/")[0] in CJK_FAMILIES and not re.search(r"\.\d+\.woff2$", p))
    ]
    for p in paths:
        download(FONTS_CDN.format(path=p), out / "assets" / "fonts" / p)
    log(f"шрифты: {len(paths)} файлов")


EMOJI_DATA = "https://unicode.org/Public/UCD/latest/ucd/emoji/emoji-data.txt"
# Символы с текстовым начертанием, которые проверены в Edge без сети и рисуются эмодзи-шрифтом.
TEXT_STYLE_ALLOWED = {0x26CF}  # ⛏ — символ игры
INVISIBLE = {0xFE0F, 0x200D}   # вариационный селектор и склейка эмодзи


def emoji_presentation() -> set[int]:
    """Код-пойнты, которые по умолчанию рисуются как эмодзи (Unicode Emoji_Presentation=Yes)."""
    path = CACHE / "emoji-data.txt"
    download(EMOJI_DATA, path)
    result: set[int] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"([0-9A-F]+)(?:\.\.([0-9A-F]+))?\s*;\s*Emoji_Presentation\b", line)
        if m:
            result.update(range(int(m[1], 16), int(m[2] or m[1], 16) + 1))
    return result


def ui_strings() -> list[tuple[str, str]]:
    """(файл, строка) для всех строковых литералов в src, кроме документации."""
    found = []
    for py in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        docstrings = {
            id(n.body[0].value)
            for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                found.append((py.name, node.value))
    return found


def check_font_coverage(out: Path) -> None:
    """Каждый символ интерфейса должен быть в Roboto или быть «настоящим» эмодзи.

    Иначе Flutter сам выбирает запасной шрифт — обычно китайский, он покрывает больше всех, —
    и без сети рисует квадратик. Символы с текстовым начертанием по умолчанию (⬇, ⚔, ⏱…)
    Flutter тоже может отдать китайскому шрифту, даже если они есть в эмодзи-шрифте.
    """
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        log("проверка шрифтов пропущена — нужен pip install fonttools brotli")
        return
    fonts = out / "assets" / "fonts"
    roboto = set(TTFont(fonts / "roboto.woff2").getBestCmap())
    emoji_font: set[int] = set()
    for f in (fonts / "notocoloremoji").rglob("*.woff2"):
        emoji_font |= set(TTFont(f).getBestCmap())
    emoji_style = emoji_presentation() | TEXT_STYLE_ALLOWED

    bad: dict[str, str] = {}
    # Шрифт, которого нет в сборке (например, "monospace"), Flutter заменит сам — и для кириллицы
    # выберет китайский. Поэтому font_family в коде игры не используем вовсе.
    for py in (ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(node, ast.keyword) and node.arg in ("font_family", "font_family_fallback"):
                bad.setdefault(f"font_family (строка {node.value.lineno})", f"{py.name}: такого шрифта нет в сборке")
    for file, text in ui_strings():
        for ch in text:
            cp = ord(ch)
            if cp <= 0x7F or cp in roboto or cp in INVISIBLE:
                continue
            if cp not in emoji_font:
                bad.setdefault(ch, f"{file}: нет ни в одном шрифте")
            elif cp not in emoji_style:
                bad.setdefault(ch, f"{file}: текстовое начертание — возьми другой эмодзи")
    if bad:
        listing = "\n".join(
            f"  {c} U+{ord(c):04X} — {why}" if len(c) == 1 else f"  {c} — {why}" for c, why in bad.items()
        )
        raise SystemExit(f"Символы, которые без сети могут стать квадратиками:\n{listing}")
    log("шрифты покрывают все символы игры")


def remove_unused(out: Path) -> None:
    for rel in UNUSED:
        p = out / rel
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink(missing_ok=True)
    for pattern in UNUSED_GLOBS:
        for p in out.glob(pattern):
            p.unlink()


SERVICE_WORKER = """\
// Сгенерировано tools/build_web.py. Кэширует всю игру, чтобы она работала без сети.
const CACHE = "mine-%(version)s";
const FILES = %(files)s;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(FILES)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k.startsWith("mine-") && k !== CACHE).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET" || new URL(req.url).origin !== self.location.origin) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const hit = await cache.match(req, { ignoreSearch: true });
    if (hit) return hit;
    try {
      return await fetch(req);
    } catch (err) {
      if (req.mode === "navigate") {
        const page = await cache.match("./");
        if (page) return page;
      }
      throw err;
    }
  })());
});
"""


def register_service_worker(out: Path) -> None:
    """Flutter больше не регистрирует service worker сам (только обновляет существующий),
    поэтому регистрируем его из index.html — по тому же адресу, что и загрузчик Flutter,
    чтобы он не перерегистрировал его своей заглушкой."""
    bootstrap = (out / "flutter_bootstrap.js").read_text(encoding="utf-8")
    sw_version = re.search(r'serviceWorkerVersion:\s*"(\d+)"', bootstrap).group(1)
    script = (
        "<script>\n"
        '  if ("serviceWorker" in navigator) {\n'
        f'    navigator.serviceWorker.register("flutter_service_worker.js?v={sw_version}");\n'
        "  }\n"
        "</script>\n"
    )
    index = out / "index.html"
    html = index.read_text(encoding="utf-8")
    # Баг flet 1.0.1 c --no-cdn: pyodideUrl="pyodide/pyodide.mjs" — для import() в воркере
    # это «голый» спецификатор, и Python не стартует. Делаем адрес абсолютным.
    html = re.sub(
        r'flet\.pyodideUrl="([^"/][^"]*)";',
        r'flet.pyodideUrl=new URL("\1", document.baseURI).href;',
        html,
    )
    index.write_text(html.replace("</body>", script + "</body>", 1), encoding="utf-8")


def write_service_worker(out: Path) -> tuple[int, int]:
    files = sorted(
        p.relative_to(out).as_posix()
        for p in out.rglob("*")
        if p.is_file() and p.name != "flutter_service_worker.js"
    )
    digest = hashlib.sha256()
    total = 0
    for f in files:
        data = (out / f).read_bytes()
        total += len(data)
        digest.update(f.encode() + b"\0" + hashlib.sha256(data).digest())
    urls = ["./"] + files
    (out / "flutter_service_worker.js").write_text(
        SERVICE_WORKER % {"version": digest.hexdigest()[:12], "files": json.dumps(urls, indent=1)},
        encoding="utf-8",
    )
    return len(urls), total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="/", help="подпапка на хостинге, например имя репозитория")
    ap.add_argument("--out", default="dist", help="куда собрать (по умолчанию dist/)")
    args = ap.parse_args()
    out = (ROOT / args.out).resolve()

    run_flet_publish(out, args.base_url)
    bundle_python_packages(out)
    bundle_fonts(out)
    check_font_coverage(out)
    remove_unused(out)
    register_service_worker(out)
    count, size = write_service_worker(out)
    log(f"service worker: {count} файлов, {size / 2**20:.1f} МБ в кэше")
    log(f"готово: {out}")


if __name__ == "__main__":
    main()
