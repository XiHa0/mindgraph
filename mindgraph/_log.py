"""mindgraph 自带的轻量日志器（标准库实现，无外部依赖）。

通过环境变量 KG_LOG_LEVEL 控制级别（默认 INFO）。
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("mindgraph")

if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(_handler)
    logger.setLevel(os.environ.get("KG_LOG_LEVEL", "INFO").upper())
    logger.propagate = False
