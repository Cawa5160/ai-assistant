"""Безопасные файловые операции списком: план с предпросмотром «было → стало», применение и отмена.

Без Qt. Windows-специфика (Корзина) — только внутри функций. Правила:
- работаем только внутри разрешённых папок (Рабочий стол, Документы, Загрузки, Изображения, Видео, Музыка);
  сами эти папки, корень диска, C:\\Windows, Program Files и %APPDATA%\\AI_Assistant не трогаем;
- пути сравниваются через realpath (symlink наружу = запрет);
- удаление ТОЛЬКО в Корзину (никакого rmtree/os.remove); нет Корзины — файл остаётся на месте;
- каждое применение пишется в журнал отмены (JSON, атомарно).
"""
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

KINDS = ("rename", "move", "delete")
_JOURNAL_KEEP = 20

TYPE_FOLDERS = {
    "Изображения": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".tif", ".tiff", ".svg", ".ico"},
    "Видео": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".flv", ".m4v"},
    "Музыка": {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"},
    "Документы": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".xls", ".xlsx", ".csv", ".ppt", ".pptx", ".md"},
    "Архивы": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Программы": {".exe", ".msi", ".bat", ".cmd", ".apk", ".iso"},
}
OTHER_FOLDER = "Прочее"


class TrashUnavailable(Exception):
    """Не удалось отправить в Корзину — файл не тронут."""


# ----------------------------------------------------------------------------- модель

@dataclass
class Op:
    kind: str                      # rename | move | delete
    src: str
    dst: Optional[str] = None      # для delete — None
    reason: str = ""               # почему заблокирована (конфликт, запрет...)
    note: str = ""                 # справочно (не блокирует), напр. «папка целиком»

    @property
    def blocked(self) -> bool:
        return bool(self.reason)


@dataclass
class Plan:
    ops: List[Op] = field(default_factory=list)
    title: str = ""
    warnings: List[str] = field(default_factory=list)   # общие предупреждения (не привязаны к одной операции)

    @property
    def runnable(self) -> List[Op]:
        return [o for o in self.ops if not o.blocked]

    @property
    def blocked(self) -> List[Op]:
        return [o for o in self.ops if o.blocked]

    @property
    def all_warnings(self) -> List[str]:
        return list(self.warnings) + [f"{os.path.basename(o.src)}: {o.reason}" for o in self.blocked]

    def __bool__(self):
        return bool(self.runnable)


@dataclass
class ApplyResult:
    done: int = 0
    failed: List[tuple] = field(default_factory=list)    # (Op, текст ошибки)
    skipped: int = 0                                      # заблокированные в плане
    log_path: str = ""


@dataclass
class UndoResult:
    restored: int = 0
    errors: List[str] = field(default_factory=list)


# ----------------------------------------------------------------------------- безопасность путей

def default_allowed_roots() -> List[str]:
    home = os.path.expanduser("~")
    names = ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")
    roots = [os.path.join(home, n) for n in names]
    for od in ("OneDrive",):                              # перенаправленные в OneDrive
        roots += [os.path.join(home, od, n) for n in names]
    return roots


def _real(p: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(p))))


def _inside(path: str, root: str, allow_equal: bool = False) -> bool:
    try:
        if os.path.commonpath([path, root]) != root:
            return False
    except ValueError:
        return False
    return allow_equal or path != root


def _forbidden_reason(real: str) -> str:
    if os.path.dirname(real) == real:
        return "корень диска"
    prefixes = []
    for var in ("WINDIR", "SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData"):
        v = os.environ.get(var)
        if v:
            prefixes.append(v)
    prefixes += ["C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)"]
    for pf in prefixes:
        if _inside(real, _real(pf), allow_equal=True) and os.path.splitdrive(real)[0] == os.path.splitdrive(_real(pf))[0]:
            return "системная папка"
    parts = [x for x in re.split(r"[\\/]+", real.lower()) if x]
    for i in range(len(parts) - 2):
        if parts[i:i + 3] == ["appdata", "roaming", "ai_assistant"]:
            return "данные самого ассистента"
    try:
        import app_paths
        dd = getattr(app_paths, "DATA_DIR", "")
        if dd and _inside(real, _real(dd), allow_equal=True):
            return "данные самого ассистента"
    except Exception:
        pass
    return ""


def check_path(path: str, allowed_roots=None, container: bool = False) -> str:
    """Пустая строка — можно; иначе причина запрета.
    container=True: путь может совпадать с разрешённой папкой (для папки-назначения/сортировки)."""
    roots = [_real(r) for r in (allowed_roots if allowed_roots is not None else default_allowed_roots())]
    real = _real(path)
    bad = _forbidden_reason(real)
    if bad:
        return bad
    if not any(_inside(real, r) for r in roots):
        if container and any(real == r for r in roots):
            return ""
        return "вне разрешённых папок" if not any(real == r for r in roots) else "это сама разрешённая папка"
    return ""


def _check_new(path: str, allowed_roots) -> str:
    """Проверка пути, которого ещё нет: realpath родителя + имя."""
    parent, name = os.path.split(os.path.abspath(path))
    return check_path(os.path.join(os.path.realpath(parent), name), allowed_roots)


# ----------------------------------------------------------------------------- планирование

def _unique(path: str, taken: set) -> str:
    base, ext = os.path.splitext(path)
    i = 1
    cand = path
    while os.path.lexists(cand) or os.path.normcase(cand) in taken:
        i += 1
        cand = f"{base} ({i}){ext}"
    return cand


def _finalize(ops: List[Op], allowed_roots, on_conflict: str = "skip") -> None:
    """Проверки: разрешённые папки, существование, конфликты имён."""
    srcs = {os.path.normcase(os.path.abspath(o.src)) for o in ops}
    taken = {}
    for o in ops:
        if o.blocked:
            continue
        if not os.path.lexists(o.src):
            o.reason = "файл не найден"
            continue
        r = check_path(o.src, allowed_roots)
        if r:
            o.reason = f"нельзя трогать: {r}"
            continue
        if o.kind == "delete":
            if os.path.isdir(o.src) and not os.path.islink(o.src):
                n = _count_files(o.src)
                o.note = f"папка целиком ({n}{'+' if n >= 1000 else ''} файлов), уйдёт в Корзину"
            continue
        r = _check_new(o.dst, allowed_roots)
        if r:
            o.reason = f"нельзя записывать: {r}"
            continue
        if o.kind == "move" and os.path.isdir(o.src) and _inside(_real(o.dst), _real(o.src), allow_equal=True):
            o.reason = "нельзя переместить папку саму в себя"
            continue
        key = os.path.normcase(os.path.abspath(o.dst))
        clash = None
        if key in srcs:
            clash = "имя занято другим файлом из этого списка"
        elif os.path.lexists(o.dst):
            clash = "файл с таким именем уже есть"
        elif key in taken:
            clash = "два файла получают одно имя"
        if clash:
            if on_conflict == "suffix" and not key in srcs:
                o.dst = _unique(o.dst, set(taken))
                key = os.path.normcase(os.path.abspath(o.dst))
            else:
                o.reason = f"конфликт имён: {clash}"
                continue
        taken[key] = o


def _count_files(folder: str, limit: int = 1000) -> int:
    n = 0
    for _r, _d, files in os.walk(folder):
        n += len(files)
        if n >= limit:
            return limit
    return n


def _as_list(paths) -> List[str]:
    if isinstance(paths, (str, os.PathLike)):
        paths = [paths]
    return [os.path.abspath(os.fspath(p)) for p in paths]


def plan_rename(paths, pattern: Optional[str] = None, replace: str = "", numbering: Optional[str] = None,
                start: int = 1, regex: bool = False, allowed_roots=None, on_conflict: str = "skip") -> Plan:
    """Переименование списком (расширение сохраняется).
    pattern/replace — замена в имени (regex=True — регулярное выражение);
    numbering — шаблон нового имени, напр. «Отпуск {n:03d}» ({name} — старое имя без расширения)."""
    plan = Plan(title="Переименование")
    rx = None
    if pattern and regex:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            plan.warnings.append(f"неверное регулярное выражение: {e}")
            return plan
    if not pattern and not numbering:
        plan.warnings.append("не задано, как переименовывать")
        return plan
    n = start
    for src in sorted(_as_list(paths), key=lambda p: os.path.basename(p).lower()):
        folder, name = os.path.split(src)
        stem, ext = (name, "") if os.path.isdir(src) else os.path.splitext(name)
        new = stem
        if pattern:
            new = rx.sub(replace, new) if rx else new.replace(pattern, replace)
        if numbering:
            try:
                new = numbering.format(n=n, name=new)
            except (KeyError, IndexError, ValueError) as e:
                plan.warnings.append(f"неверный шаблон нумерации: {e}")
                return plan
            n += 1
        new = new.strip()
        op = Op("rename", src, os.path.join(folder, new + ext))
        if not new or re.search(r'[\\/:*?"<>|]', new) or new.endswith("."):
            op.reason = "недопустимое имя файла"
        if os.path.normcase(op.dst) == os.path.normcase(src):
            continue                                       # имя не изменилось
        plan.ops.append(op)
    _finalize(plan.ops, allowed_roots, on_conflict)
    return plan


def plan_move(paths, dest_dir: str, allowed_roots=None, on_conflict: str = "skip") -> Plan:
    plan = Plan(title="Перемещение")
    dest_dir = os.path.abspath(os.path.expanduser(dest_dir))
    r = check_path(dest_dir, allowed_roots, container=True)
    if r:
        plan.warnings.append(f"папка назначения недоступна: {r}")
        return plan
    if os.path.lexists(dest_dir) and not os.path.isdir(dest_dir):
        plan.warnings.append("папка назначения — это файл")
        return plan
    for src in _as_list(paths):
        dst = os.path.join(dest_dir, os.path.basename(src))
        if os.path.normcase(dst) == os.path.normcase(src):
            continue
        plan.ops.append(Op("move", src, dst))
    _finalize(plan.ops, allowed_roots, on_conflict)
    return plan


def plan_delete(paths, allowed_roots=None) -> Plan:
    """Удаление = отправка в Корзину."""
    plan = Plan(title="Удаление в Корзину")
    plan.ops = [Op("delete", s) for s in _as_list(paths)]
    _finalize(plan.ops, allowed_roots)
    return plan


def category_for(ext: str) -> str:
    ext = ext.lower()
    for name, exts in TYPE_FOLDERS.items():
        if ext in exts:
            return name
    return OTHER_FOLDER


def plan_organize(folder: str, by: str = "type", allowed_roots=None, on_conflict: str = "suffix") -> Plan:
    """Разложить файлы папки (только верхний уровень) по подпапкам: by='type' или 'date' (ГГГГ-ММ)."""
    plan = Plan(title="Разложить по папкам")
    if by not in ("type", "date"):
        plan.warnings.append("by должно быть 'type' или 'date'")
        return plan
    folder = os.path.abspath(os.path.expanduser(folder))
    r = check_path(folder, allowed_roots, container=True)
    if r:
        plan.warnings.append(f"папка недоступна: {r}")
        return plan
    if not os.path.isdir(folder):
        plan.warnings.append("это не папка")
        return plan
    for name in sorted(os.listdir(folder), key=str.lower):
        src = os.path.join(folder, name)
        if name.startswith((".", "~$")) or name.lower() in ("desktop.ini", "thumbs.db"):
            continue
        if not os.path.isfile(src) or os.path.islink(src):
            continue
        if by == "type":
            sub = category_for(os.path.splitext(name)[1])
        else:
            sub = time.strftime("%Y-%m", time.localtime(os.path.getmtime(src)))
        plan.ops.append(Op("move", src, os.path.join(folder, sub, name)))
    _finalize(plan.ops, allowed_roots, on_conflict)
    if not plan.ops:
        plan.warnings.append("нечего раскладывать")
    return plan


# ----------------------------------------------------------------------------- Корзина

def _trash_to_folder(path: str, trash_dir: str) -> str:
    """Тестовая/Linux-корзина: перемещение в папку (+ .trashinfo, если это стандартная freedesktop-корзина)."""
    os.makedirs(trash_dir, exist_ok=True)
    name = os.path.basename(path.rstrip("\\/"))
    dst = os.path.join(trash_dir, name)
    i = 1
    while os.path.lexists(dst):
        i += 1
        dst = os.path.join(trash_dir, f"{name}.{i}")
    os.rename(path, dst)                                   # тот же диск; иначе OSError -> файл не тронут
    info_dir = os.path.join(os.path.dirname(trash_dir.rstrip("/")), "info")
    if os.path.basename(trash_dir.rstrip("/")) == "files" and os.path.isdir(info_dir):
        try:
            with open(os.path.join(info_dir, os.path.basename(dst) + ".trashinfo"), "w", encoding="utf-8") as f:
                f.write("[Trash Info]\nPath=%s\nDeletionDate=%s\n" % (path, time.strftime("%Y-%m-%dT%H:%M:%S")))
        except OSError:
            pass
    return dst


def _win_shell_delete(path: str) -> None:
    """SHFileOperationW + FOF_ALLOWUNDO (в Корзину). ctypes только внутри функции."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _pack_ = 1 if ctypes.sizeof(ctypes.c_void_p) == 4 else 0
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = os.path.abspath(path) + "\0\0"
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted or os.path.lexists(path):
        raise TrashUnavailable(f"Корзина не приняла файл (код {rc})")


def send_to_trash(path: str, trash_dir: Optional[str] = None) -> str:
    """Отправляет в Корзину. Возвращает путь внутри «корзины-папки» или '' (системная Корзина Windows).
    При любой неудаче бросает TrashUnavailable, файл остаётся на месте."""
    try:
        if trash_dir:
            return _trash_to_folder(path, trash_dir)
        if os.name == "nt":
            try:
                from send2trash import send2trash
            except ImportError:
                _win_shell_delete(path)
                return ""
            send2trash(path)
            if os.path.lexists(path):
                raise TrashUnavailable("Корзина не приняла файл")
            return ""
        home_trash = os.path.join(os.path.expanduser("~"), ".local", "share", "Trash", "files")
        try:
            from send2trash import send2trash
            send2trash(path)
            if os.path.lexists(path):
                raise TrashUnavailable("Корзина не приняла файл")
            return ""
        except ImportError:
            return _trash_to_folder(path, home_trash)
    except TrashUnavailable:
        raise
    except Exception as e:
        raise TrashUnavailable(str(e)) from e


def _win_restore_from_recycle(original: str) -> bool:
    """Best effort: находит в $Recycle.Bin запись ($I) с исходным путём и возвращает файл ($R) на место."""
    drive = os.path.splitdrive(os.path.abspath(original))[0]
    base = os.path.join(drive + os.sep, "$Recycle.Bin")
    if not os.path.isdir(base):
        return False
    want = os.path.normcase(os.path.abspath(original))
    best = None
    for sid in os.listdir(base):
        d = os.path.join(base, sid)
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            if not n.startswith("$I"):
                continue
            try:
                with open(os.path.join(d, n), "rb") as f:
                    data = f.read()
                ver = int.from_bytes(data[:8], "little")
                if ver == 2:
                    ln = int.from_bytes(data[24:28], "little")
                    orig = data[28:28 + ln * 2].decode("utf-16-le").rstrip("\0")
                else:
                    orig = data[24:].decode("utf-16-le").split("\0")[0]
                ts = int.from_bytes(data[16:24], "little")
            except (OSError, ValueError):
                continue
            if os.path.normcase(os.path.abspath(orig)) == want:
                r = os.path.join(d, "$R" + n[2:])
                if os.path.lexists(r) and (best is None or ts > best[0]):
                    best = (ts, r, os.path.join(d, n))
    if not best:
        return False
    os.makedirs(os.path.dirname(original), exist_ok=True)
    os.rename(best[1], original)
    try:
        os.remove(best[2])                                 # только служебная запись $I
    except OSError:
        pass
    return True


# ----------------------------------------------------------------------------- журнал

def default_undo_log() -> str:
    try:
        import app_paths
        base = app_paths.DATA_DIR
    except Exception:
        base = os.path.join(os.path.expanduser("~"), ".ai_assistant")
    return os.path.join(base, "file_ops_undo.json")


def _read_journal(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("batches"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "batches": []}


def _write_journal(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ----------------------------------------------------------------------------- применение / отмена

def apply(plan: Plan, undo_log_path: Optional[str] = None, allowed_roots=None, trash_dir: Optional[str] = None,
          trash_fn: Optional[Callable[[str], str]] = None) -> ApplyResult:
    """Выполняет незаблокированные операции плана. Каждая проверяется заново прямо перед выполнением.
    trash_fn(path) -> путь в корзине или '' (по умолчанию send_to_trash); trash_dir — корзина-папка (тесты)."""
    log_path = undo_log_path or default_undo_log()
    res = ApplyResult(skipped=len(plan.blocked), log_path=log_path)
    entries, created = [], []
    for op in plan.runnable:
        try:
            if op.kind not in KINDS:
                raise ValueError("неизвестная операция")
            r = check_path(op.src, allowed_roots)
            if r:
                raise PermissionError(f"нельзя трогать: {r}")
            if not os.path.lexists(op.src):
                raise FileNotFoundError("файл не найден")
            if op.kind == "delete":
                fn = trash_fn or (lambda p: send_to_trash(p, trash_dir))
                where = fn(op.src)
                entries.append({"kind": "delete", "src": op.src, "dst": None, "trash": where or ""})
            else:
                r = _check_new(op.dst, allowed_roots)
                if r:
                    raise PermissionError(f"нельзя записывать: {r}")
                if os.path.lexists(op.dst):
                    raise FileExistsError("файл с таким именем уже есть")
                _makedirs_tracked(os.path.dirname(op.dst), created)
                os.rename(op.src, op.dst)
                entries.append({"kind": op.kind, "src": op.src, "dst": op.dst, "trash": ""})
            res.done += 1
        except Exception as e:
            res.failed.append((op, str(e) or e.__class__.__name__))
    if entries or created:
        j = _read_journal(log_path)
        j["batches"].append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "title": plan.title,
                             "entries": entries, "created_dirs": created})
        j["batches"] = j["batches"][-_JOURNAL_KEEP:]
        _write_journal(log_path, j)
    return res


def _makedirs_tracked(folder: str, created: list) -> None:
    missing = []
    d = folder
    while d and not os.path.lexists(d):
        missing.append(d)
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    for d in reversed(missing):
        os.mkdir(d)
        created.append(d)


def can_undo(undo_log_path: Optional[str] = None) -> bool:
    return bool(_read_journal(undo_log_path or default_undo_log())["batches"])


def undo(undo_log_path: Optional[str] = None, trash_dir_unused=None) -> UndoResult:
    """Откатывает последнее применение: возвращает имена/места, удалённое — из Корзины."""
    log_path = undo_log_path or default_undo_log()
    j = _read_journal(log_path)
    res = UndoResult()
    if not j["batches"]:
        res.errors.append("нечего отменять")
        return res
    batch = j["batches"].pop()
    for e in reversed(batch.get("entries", [])):
        try:
            src, dst = e["src"], e.get("dst")
            if os.path.lexists(src):
                raise FileExistsError(f"на старом месте уже есть «{os.path.basename(src)}»")
            if e["kind"] == "delete":
                trash = e.get("trash")
                if trash:
                    if not os.path.lexists(trash):
                        raise FileNotFoundError("в корзине файла уже нет")
                    os.makedirs(os.path.dirname(src), exist_ok=True)
                    os.rename(trash, src)
                    _drop_trashinfo(trash)
                elif os.name != "nt" or not _win_restore_from_recycle(src):
                    raise FileNotFoundError("верни из Корзины вручную")
            else:
                if not os.path.lexists(dst):
                    raise FileNotFoundError(f"«{os.path.basename(dst)}» уже нет на месте")
                os.makedirs(os.path.dirname(src), exist_ok=True)
                os.rename(dst, src)
            res.restored += 1
        except Exception as ex:
            res.errors.append(f"{os.path.basename(e.get('src', ''))}: {ex}")
    for d in reversed(batch.get("created_dirs", [])):
        try:
            os.rmdir(d)                                    # только пустые; иначе оставляем
        except OSError:
            pass
    if res.errors:                                         # то, что не удалось, оставляем в журнале для повтора
        left = [e for e in batch.get("entries", []) if not _entry_undone(e)]
        if left:
            batch["entries"] = left
            batch["created_dirs"] = []
            j["batches"].append(batch)
    _write_journal(log_path, j)
    return res


def _entry_undone(e) -> bool:
    return os.path.lexists(e["src"])


def _drop_trashinfo(trash_path: str) -> None:
    info = os.path.join(os.path.dirname(os.path.dirname(trash_path)), "info", os.path.basename(trash_path) + ".trashinfo")
    try:
        if os.path.isfile(info):
            os.remove(info)
    except OSError:
        pass
