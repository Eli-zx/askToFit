"""统一日志：控制台 + 轮转文件双输出。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import Config

_CONFIGURED = False
# 每条日志都带源码文件与行号，ERROR/异常可精确定位到具体代码位置
_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(filename)s:%(lineno)d | "
    "%(name)s | %(message)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str | None = None) -> None:
    """初始化根日志器。重复调用安全。"""
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_dir = Path(Config.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / Config.log_file

    root = logging.getLogger()
    root.setLevel(getattr(logging, (level or Config.log_level).upper(), logging.INFO))
    root.handlers.clear()

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    # Windows 控制台默认 GBK，统一改为 UTF-8 避免中文乱码
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.setLevel(root.level)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=Config.log_max_bytes,
        backupCount=Config.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(root.level)
    root.addHandler(file_handler)

    # 降低第三方库噪音
    for noisy in ("httpx", "httpcore", "openai", "urllib3", "pymilvus", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
