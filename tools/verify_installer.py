#!/usr/bin/env python3
"""
Проверка целостности установщика: раскодирует каждый встроенный
base64-блок (как это сделает certutil на Windows) и сравнивает
побайтово с файлом проекта. Список файлов берётся из build_installer.py,
так что рассинхрона между сборкой и проверкой быть не может.
"""
import base64
import os
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS_DIR)
import build_installer as bi  # noqa: E402

with open(bi.OUT_PATH, "r", encoding="utf-8") as f:
    bat_lines = f.read().splitlines()

ok_all = True
idx = 0
for rel in bi.FILES_TO_EMBED:
    marker = f"echo Распаковываю: {rel.replace('/', chr(92))}"
    try:
        start = bat_lines.index(marker, idx)
    except ValueError:
        print(f"ОШИБКА: нет блока для {rel}")
        ok_all = False
        continue
    i = start + 2
    b64 = []
    while bat_lines[i] != ")":
        assert bat_lines[i].startswith("echo "), bat_lines[i]
        b64.append(bat_lines[i][5:])
        i += 1
    decoded = base64.b64decode("".join(b64))
    with open(os.path.join(bi.SRC_DIR, rel), "rb") as f:
        original = f.read()
    if decoded == original:
        print(f"OK   {rel} ({len(original)} байт)")
    else:
        print(f"FAIL {rel}: {len(decoded)} != {len(original)} байт")
        ok_all = False
    idx = i + 1

text = "\n".join(bat_lines)
assert text.count("(") == text.count(")"), "скобки в .bat не сбалансированы"
raw = open(bi.OUT_PATH, "rb").read()
assert raw.count(b"\n") == raw.count(b"\r\n"), "есть строки без CR"
print()
print("ВСЁ В ПОРЯДКЕ: файлы совпадают побайтово, скобки сбалансированы, CRLF" if ok_all else "ЕСТЬ РАСХОЖДЕНИЯ")
sys.exit(0 if ok_all else 1)
