"""
Логирование действий приложения — в консоль и в файл с автоматической
ротацией (чтобы action_log.txt не рос бесконечно).
"""
import logging
import sys
from logging.handlers import RotatingFileHandler

import config

_logger = logging.getLogger("ai_assistant")
_logger.setLevel(logging.INFO)

if not _logger.handlers:
    formatter = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    file_handler = RotatingFileHandler(
        config.LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    _logger.addHandler(file_handler)

    if sys.stderr is not None:  # при запуске без консоли (pythonw / .exe) консоли нет
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        _logger.addHandler(console_handler)


def log(message: str):
    _logger.info(message)
