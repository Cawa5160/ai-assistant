"""
Характеристики компьютера для подбора локальных моделей: память, диск, процессор, видеокарта.
Видеопамять надёжно читается только у NVIDIA (через nvidia-smi из драйвера); у остальных Windows часто занижает
объём, поэтому там мы честно пишем «неизвестно». Ничего не отправляется в интернет.
"""
import os
import platform
import shutil
import subprocess

from logger import log

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def parse_nvidia_smi(output: str) -> list:
    """Строки вида 'NVIDIA GeForce RTX 4050 Laptop GPU, 6141' (МиБ) -> [{'name', 'vram_gb', 'source'}]."""
    gpus = []
    for line in (output or "").splitlines():
        if "," not in line:
            continue
        name, _, mem = line.rpartition(",")
        try:
            gpus.append({"name": name.strip(), "vram_gb": round(int(float(mem.strip())) / 1024, 1),
                         "source": "nvidia-smi"})
        except ValueError:
            continue
    return gpus


def _run(cmd, timeout=6) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              creationflags=NO_WINDOW).stdout or ""
    except Exception:
        return ""


def _gpus() -> list:
    found = parse_nvidia_smi(_run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]))
    if found or os.name != "nt":
        return found
    out = _run(["powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"], timeout=10)
    return [{"name": n.strip(), "vram_gb": None, "source": "windows"} for n in out.splitlines() if n.strip()]


def _cpu_name() -> str:
    if os.name == "nt":
        out = _run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"], timeout=10)
        if out.strip():
            return out.strip().splitlines()[0].strip()
    return platform.processor() or platform.machine()


def detect(path: str = None) -> dict:
    """{'ram_gb', 'disk_free_gb', 'cpu', 'cores', 'gpus': [...], 'vram_gb' (лучшая NVIDIA или None)}"""
    info = {"ram_gb": None, "disk_free_gb": None, "cpu": "", "cores": os.cpu_count() or 0, "gpus": [], "vram_gb": None}
    try:
        import psutil
        info["ram_gb"] = round(psutil.virtual_memory().total / 1024 ** 3, 1)
    except Exception as e:
        log(f"Оборудование: не прочитана память: {e}")
    try:
        info["disk_free_gb"] = round(shutil.disk_usage(path or os.path.expanduser("~")).free / 1024 ** 3, 1)
    except Exception:
        pass
    info["cpu"] = _cpu_name()
    info["gpus"] = _gpus()
    vrams = [g["vram_gb"] for g in info["gpus"] if g.get("vram_gb")]
    info["vram_gb"] = max(vrams) if vrams else None
    return info
