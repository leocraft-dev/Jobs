"""
配置中心 — 集中管理 BOT 所有运行时配置

职责：
  - 从环境变量 / 配置文件加载 BotConfig
  - 提供参数验证与默认值
  - 运行时可通过 setter 动态更新

用法:
    from src.config import config
    print(config.search_url)
    config.daily_chat_limit = 200
"""

import os
import json
from dataclasses import dataclass, field
from typing import Optional


# ==================== 工具函数 ====================

def _get_temp_dir() -> str:
    """获取系统临时目录"""
    return os.environ.get(
        "TMPDIR",
        os.environ.get("TEMP", os.environ.get("TMP", "/tmp")),
    )


def _load_dotenv(path: str = ".env") -> None:
    """
    加载 .env 文件到 os.environ

    支持格式:
        KEY=VALUE
        # 注释
        空行跳过
        值中可包含 = 号（VALUE=A=B → 取 A=B）
        可选引号包裹（KEY="value" / KEY='value'）
    """
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # 去除可选引号
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
    except (IOError, OSError) as e:
        print(f"[Config] .env 文件读取失败: {e}")


# ==================== 默认值常量 ====================

_DEFAULT_SEARCH_URL = (
    "https://www.zhipin.com/web/geek/job?query=Python&city=101020100"
)
_DEFAULT_CHECK_INTERVAL = 300        # 轮询间隔（秒）
_DEFAULT_MIN_DELAY = 3.0             # 最小人性化延迟（秒）
_DEFAULT_MAX_DELAY = 7.0             # 最大人性化延迟（秒）
_DEFAULT_DAILY_CHAT_LIMIT = 150      # 每日沟通上限
_DEFAULT_MAX_CONSECUTIVE_EMPTY = 3   # 连续空结果触发告警阈值
_DEFAULT_PAGES_TO_SCAN = 3           # 每轮检索扫描页数


@dataclass
class BotConfig:
    """BOT 运行时全部配置项"""

    # ── 搜索相关 ──
    search_url: str = _DEFAULT_SEARCH_URL
    pages_to_scan: int = _DEFAULT_PAGES_TO_SCAN

    # ── 轮询相关 ──
    check_interval: int = _DEFAULT_CHECK_INTERVAL

    # ── 人性化延迟 ──
    min_delay: float = _DEFAULT_MIN_DELAY
    max_delay: float = _DEFAULT_MAX_DELAY

    # ── 限制相关 ──
    daily_chat_limit: int = _DEFAULT_DAILY_CHAT_LIMIT
    max_consecutive_empty: int = _DEFAULT_MAX_CONSECUTIVE_EMPTY

    # ── 飞书通知 ──
    lark_webhook_url: str = ""

    # ── Chromium 配置 ──
    # 默认使用系统临时目录，确保不依赖本地数据
    # 可通过 .env 的 BOT_USER_DATA_PATH 覆盖
    user_data_path: str = field(
        default_factory=lambda: os.path.join(
            _get_temp_dir(), "jobs2_browser_profile",
        ),
    )
    headless: bool = False

    # ── 截图存储 ──
    # 扫码二维码截图保存路径，可通过 .env 的 BOT_SCREENSHOT_DIR 覆盖
    screenshot_dir: str = field(
        default_factory=lambda: os.path.join(
            _get_temp_dir(), "jobs2_screenshots",
        ),
    )

    # ── 本地持久化 ──
    # 默认使用系统临时目录，确保不依赖项目本地数据
    # 可通过 .env 的 BOT_FINGERPRINT_PATH 覆盖
    fingerprint_path: str = field(
        default_factory=lambda: os.path.join(
            _get_temp_dir(), "jobs2_known_fingerprints.json",
        ),
    )

    # ── 内部状态（非持久化配置，运行时动态更新） ──
    consecutive_empty_count: int = 0  # 当前连续空结果计数

    def validate(self) -> list[str]:
        """校验配置合法性，返回所有错误信息列表（空列表表示无问题）"""
        errors: list[str] = []

        if not self.search_url.startswith("http"):
            errors.append("search_url 必须为有效的 HTTP(S) 链接")

        if self.check_interval < 10:
            errors.append("check_interval 过短（< 10 秒），建议 >= 60 秒")

        if self.min_delay < 0.5:
            errors.append("min_delay 过短（< 0.5 秒），建议 >= 3.0 秒")

        if self.max_delay < self.min_delay:
            errors.append("max_delay 必须 >= min_delay")

        if self.daily_chat_limit < 1 or self.daily_chat_limit > 500:
            errors.append("daily_chat_limit 推荐范围 1-500")

        if self.lark_webhook_url and not self.lark_webhook_url.startswith("http"):
            errors.append("lark_webhook_url 必须为有效的 HTTP(S) 链接")

        return errors

    def to_dict(self) -> dict:
        """序列化为字典（排除内部状态字段）"""
        return {
            "search_url": self.search_url,
            "pages_to_scan": self.pages_to_scan,
            "check_interval": self.check_interval,
            "min_delay": self.min_delay,
            "max_delay": self.max_delay,
            "daily_chat_limit": self.daily_chat_limit,
            "max_consecutive_empty": self.max_consecutive_empty,
            "lark_webhook_url": self.lark_webhook_url if self.lark_webhook_url else "",
        }


# ==================== 配置加载器 ====================

def _load_from_env(config: BotConfig) -> BotConfig:
    """从环境变量覆盖配置（环境变量名 = BOT_ + 大写字段名）"""
    mapping = {
        "BOT_SEARCH_URL": "search_url",
        "BOT_PAGES_TO_SCAN": "pages_to_scan",
        "BOT_CHECK_INTERVAL": "check_interval",
        "BOT_MIN_DELAY": "min_delay",
        "BOT_MAX_DELAY": "max_delay",
        "BOT_DAILY_CHAT_LIMIT": "daily_chat_limit",
        "BOT_MAX_CONSECUTIVE_EMPTY": "max_consecutive_empty",
        "BOT_LARK_WEBHOOK_URL": "lark_webhook_url",
        "BOT_USER_DATA_PATH": "user_data_path",
        "BOT_HEADLESS": "headless",
        "BOT_SCREENSHOT_DIR": "screenshot_dir",
        "BOT_FINGERPRINT_PATH": "fingerprint_path",
    }

    for env_name, field_name in mapping.items():
        value = os.environ.get(env_name)
        if value is None:
            continue

        current = getattr(config, field_name)
        expected_type = type(current)

        try:
            if expected_type == bool:
                setattr(config, field_name, value.lower() in ("1", "true", "yes"))
            elif expected_type == int:
                setattr(config, field_name, int(value))
            elif expected_type == float:
                setattr(config, field_name, float(value))
            else:
                setattr(config, field_name, value)
        except (ValueError, TypeError) as e:
            print(f"[Config] 环境变量 {env_name} 解析失败: {e}，使用默认值")

    return config


def _load_from_file(config: BotConfig, path: str = "./config.json") -> BotConfig:
    """从 JSON 配置文件加载"""
    if not os.path.isfile(path):
        return config

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"[Config] 配置文件 {path} 读取失败: {e}")
        return config

    for key, value in data.items():
        if hasattr(config, key):
            setattr(config, key, value)
        else:
            print(f"[Config] 未知配置项: {key}")

    return config


def _init_config() -> BotConfig:
    """初始化并返回 BotConfig 单例"""
    # 1. 先加载 .env 到 os.environ
    _load_dotenv(".env")

    cfg = BotConfig()
    cfg = _load_from_file(cfg)
    cfg = _load_from_env(cfg)
    errors = cfg.validate()
    if errors:
        for err in errors:
            print(f"[Config] ⚠️  配置校验警告: {err}")
    return cfg


# ==================== 全局单例 ====================

config: BotConfig = _init_config()