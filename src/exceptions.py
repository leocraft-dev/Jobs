"""
异常体系 — BOT 运行时自定义异常与自动恢复机制

职责：
  - 定义 BOT 特定异常层级
  - 提供异常分类与恢复建议
  - 支持异常严重级别判定

用法:
    from src.exceptions import (
        BotError, SessionExpired, SecurityBlocked,
        RateLimitExceeded, handle_exception,
    )
"""


# ==================== 异常基类 ====================

class BotError(Exception):
    """BOT 运行时异常基类"""

    SEVERITY = "info"  # info / warning / critical

    def __init__(self, message: str = "", recoverable: bool = True):
        super().__init__(message)
        self.message = message
        self.recoverable = recoverable  # 是否可自动恢复

    def __str__(self) -> str:
        return f"[{self.SEVERITY.upper()}] {self.message}"


# ==================== 具体异常 ====================

class SessionExpired(BotError):
    """会话过期，需要重新登录"""
    SEVERITY = "warning"

    def __init__(self, message: str = "会话已过期，需要重新扫码登录"):
        super().__init__(message, recoverable=True)


class SecurityBlocked(BotError):
    """触发安全拦截（滑块/验证码），需要人工处理"""
    SEVERITY = "critical"

    def __init__(self, message: str = "触发安全验证拦截，BOT 已暂停"):
        super().__init__(message, recoverable=False)


class RateLimitExceeded(BotError):
    """操作频率超限"""
    SEVERITY = "warning"

    def __init__(self, message: str = "操作频率超限，已自动降速"):
        super().__init__(message, recoverable=True)


class NavigationError(BotError):
    """页面导航异常（加载失败、页面崩溃等）"""
    SEVERITY = "warning"

    def __init__(self, message: str = "页面导航异常"):
        super().__init__(message, recoverable=True)


class ElementNotFound(BotError):
    """页面元素未找到"""
    SEVERITY = "info"

    def __init__(self, message: str = "预期页面元素未找到"):
        super().__init__(message, recoverable=True)


class DailyLimitReached(BotError):
    """每日操作上限"""
    SEVERITY = "info"

    def __init__(self, message: str = "已达每日操作上限"):
        super().__init__(message, recoverable=False)


class ConfigError(BotError):
    """配置错误"""
    SEVERITY = "critical"

    def __init__(self, message: str = "配置验证失败"):
        super().__init__(message, recoverable=False)


# ==================== 异常处理工具 ====================

# 异常严重级别到通知类型的映射
_SEVERITY_NOTIFICATION = {
    "info": None,          # 不推送
    "warning": "alert",    # 推送 alert
    "critical": "critical", # 推送 critical
}


def classify_exception(error: Exception) -> tuple[str, bool, str | None]:
    """
    对异常进行分类

    Returns:
        (severity, recoverable, notification_type)
        severity: info / warning / critical
        recoverable: True / False
        notification_type: None / "alert" / "critical"
    """
    if isinstance(error, BotError):
        return (
            error.SEVERITY,
            error.recoverable,
            _SEVERITY_NOTIFICATION.get(error.SEVERITY),
        )

    # 非 BOT 异常的统一处理
    return ("warning", True, "alert")


def get_recovery_suggestion(error: Exception) -> str:
    """根据异常类型给出恢复建议"""
    suggestions = {
        SessionExpired: "需要重新扫码登录，BOT 会自动发起扫码流程",
        SecurityBlocked: "请人工在浏览器界面完成滑块/验证码验证，然后重启 BOT",
        RateLimitExceeded: "BOT 已自动降低操作频率，无需人工干预",
        NavigationError: "可能是网络波动或页面结构变化，BOT 将在下一轮重试",
        ElementNotFound: "页面元素选择器可能需要更新，请检查目标网站结构",
        DailyLimitReached: "已达每日上限，BOT 将暂停至次日",
        ConfigError: "请检查配置文件或环境变量设置",
    }

    for exc_type, suggestion in suggestions.items():
        if isinstance(error, exc_type):
            return suggestion

    return f"未知异常: {error}，请查看日志排查"


def should_stop_bot(error: Exception) -> bool:
    """
    判断当前异常是否应完全停止 BOT

    返回 True 的情况：
    - 不可恢复异常（需要人工处理）
    - 配置错误
    """
    if isinstance(error, BotError):
        return not error.recoverable

    # 对于非 BOT 异常，连续 3 次发生可视为不可恢复
    return False