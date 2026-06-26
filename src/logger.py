"""
日志系统 — 文件与控制台双输出日志

职责：
  - 控制台输出（彩色，适合终端查看）
  - 文件输出（按天滚动，保留 7 天）
  - 统一的日志格式与级别控制

用法:
    from src.logger import get_logger
    
    logger = get_logger("spider_engine")
    logger.info("开始检索")
    logger.warning("触发频率限制")
    logger.error("页面加载失败")
"""

import os
import sys
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime


# 日志级别映射（可通过环境变量覆盖）
_LOG_LEVEL = os.environ.get("BOT_LOG_LEVEL", "INFO").upper()
_LOG_DIR = os.environ.get("BOT_LOG_DIR", "./logs")
_LOG_FORMAT = "%(asctime)s [%(levelname)-5s] [%(name)s] %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 保持已创建的 logger 缓存，避免重复注册
_loggers: dict[str, logging.Logger] = {}


def get_logger(name: str) -> logging.Logger:
    """
    获取（或创建）指定名称的 logger

    每个模块使用自己的 logger:
        logger = get_logger(__name__)
    """
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)
    logger.setLevel(_LOG_LEVEL)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    # ── 控制台 Handler ──
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(_LOG_LEVEL)
    console_handler.setFormatter(_ConsoleFormatter(
        fmt=_LOG_FORMAT,
        datefmt=_LOG_DATE_FORMAT,
    ))
    logger.addHandler(console_handler)

    # ── 文件 Handler（按天滚动） ──
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        log_file = os.path.join(_LOG_DIR, "jobs2.log")

        file_handler = TimedRotatingFileHandler(
            filename=log_file,
            when="midnight",      # 每天午夜滚动
            interval=1,
            backupCount=7,        # 保留 7 天
            encoding="utf-8",
            delay=False,
        )
        file_handler.setLevel(_LOG_LEVEL)
        file_handler.setFormatter(logging.Formatter(
            fmt=_LOG_FORMAT,
            datefmt=_LOG_DATE_FORMAT,
        ))
        logger.addHandler(file_handler)
    except (IOError, OSError) as e:
        # 文件日志无法创建时，仅使用控制台输出
        logger.warning(f"无法创建日志文件: {e}")

    # 禁止日志向父 logger 传递（避免重复输出）
    logger.propagate = False

    _loggers[name] = logger
    return logger


class _ConsoleFormatter(logging.Formatter):
    """
    控制台彩色格式化器

    颜色映射:
        DEBUG   → 灰色
        INFO    → 默认
        WARNING → 黄色
        ERROR   → 红色
        CRITICAL → 红底白字
    """

    _COLOR_MAP = {
        "DEBUG": "\033[38;5;244m",     # 灰色
        "INFO": "\033[0m",              # 默认
        "WARNING": "\033[38;5;214m",     # 黄色
        "ERROR": "\033[38;5;196m",       # 红色
        "CRITICAL": "\033[41;97m",       # 红底白字
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        # 在 Windows 上可能不支持 ANSI 转义，跳过
        if sys.platform == "win32":
            return super().format(record)

        color = self._COLOR_MAP.get(record.levelname, self._RESET)
        formatted = super().format(record)
        return f"{color}{formatted}{self._RESET}"


# ── 便捷函数 ──

def set_level(level: str):
    """全局设置日志级别"""
    global _LOG_LEVEL
    _LOG_LEVEL = level.upper()
    for logger in _loggers.values():
        logger.setLevel(_LOG_LEVEL)
        for handler in logger.handlers:
            handler.setLevel(_LOG_LEVEL)


def get_log_file_path() -> str | None:
    """获取当前日志文件路径（如果存在）"""
    path = os.path.join(_LOG_DIR, "jobs2.log")
    return path if os.path.isfile(path) else None