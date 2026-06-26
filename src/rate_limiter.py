"""
频率限制器 — 基于滑动窗口的动作频率控制

职责：
  - 对指定动作类型进行频率限制（如搜索、详情、沟通）
  - 支持最小间隔限制和滑动窗口计数限制
  - 自动动态降速（当达到阈值时增加间隔）

用法:
    from src.rate_limiter import RateLimiter
    
    limiter = RateLimiter()
    limiter.wait("search")      # 等待适合的时间后执行搜索
    limiter.wait("chat")        # 等待适合的时间后执行沟通
    limiter.record("chat")      # 记录一次沟通动作
"""

import time
import random
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from src.config import config


class RateLimiter:
    """
    频率限制器，基于滑动窗口与最小间隔双重控制

    支持的 action_type:
        - "search"  : 搜索/翻页
        - "detail"  : 查看详情
        - "chat"    : 沟通打招呼
    """

    # 每种动作的安全间隔配置（秒）
    # (min_interval, window_minutes, max_count_in_window)
    _DEFAULT_RULES = {
        "search": (8.0, 5, 30),    # 至少间隔 8s，5 分钟内最多 30 次
        "detail": (5.0, 5, 40),    # 至少间隔 5s，5 分钟内最多 40 次
        "chat":   (15.0, 60, 10),  # 至少间隔 15s，60 分钟内最多 10 次
    }

    def __init__(self):
        # 最近一次动作的时间戳
        self._last_time: dict[str, float] = defaultdict(float)
        # 滑动窗口记录 { action_type: deque[timestamp] }
        self._window: dict[str, deque] = defaultdict(deque)
        # 当前动态延迟倍率（自动降速）
        self._dynamic_factor: dict[str, float] = defaultdict(lambda: 1.0)

    # ── 公开接口 ──

    def wait(self, action_type: str = "default") -> float:
        """
        阻塞直到可以安全执行指定动作

        Returns:
            float: 实际等待的秒数
        """
        rule = self._get_rule(action_type)
        base_interval = rule[0]

        # 1. 计算最小间隔等待
        wait_time = self._calc_interval_wait(action_type, base_interval)

        # 2. 计算滑动窗口等待
        window_wait = self._calc_window_wait(action_type, rule)

        # 3. 取最大值
        total_wait = max(wait_time, window_wait)

        # 4. 应用动态因子（自动降速）
        total_wait *= self._dynamic_factor[action_type]

        # 5. 添加随机抖动 ±20%
        jitter = random.uniform(0.8, 1.2)
        total_wait *= jitter

        if total_wait > 0:
            time.sleep(total_wait)

        # 更新最后执行时间
        self._last_time[action_type] = time.time()

        return total_wait

    def record(self, action_type: str = "default"):
        """
        记录一次动作执行

        应在动作完成后调用，用于更新滑动窗口计数
        """
        now = time.time()
        self._window[action_type].append(now)
        self._last_time[action_type] = now

        # 如果接近阈值，提升动态因子（自动降速）
        rule = self._get_rule(action_type)
        window_minutes = rule[1]
        max_count = rule[2]
        window_window = window_minutes * 60

        # 清除过期记录
        self._prune_window(action_type, window_window)

        count = len(self._window[action_type])
        if count >= max_count * 0.8:
            # 达到 80% 阈值 → 提速因子 1.5x
            self._dynamic_factor[action_type] = 1.5
        elif count >= max_count * 0.9:
            # 达到 90% 阈值 → 提速因子 2.0x
            self._dynamic_factor[action_type] = 2.0
        else:
            # 正常 → 恢复 1.0x
            self._dynamic_factor[action_type] = 1.0

    def can_execute(self, action_type: str = "default") -> bool:
        """检查当前是否可以执行（非阻塞）"""
        rule = self._get_rule(action_type)
        base_interval = rule[0]
        window_minutes = rule[1]
        max_count = rule[2]
        window_window = window_minutes * 60

        # 最小间隔检查
        elapsed = time.time() - self._last_time[action_type]
        if elapsed < base_interval:
            return False

        # 滑动窗口检查
        self._prune_window(action_type, window_window)
        if len(self._window[action_type]) >= max_count:
            return False

        return True

    def reset(self):
        """重置所有计数"""
        self._last_time.clear()
        self._window.clear()
        self._dynamic_factor.clear()

    def status(self, action_type: str = "default") -> dict:
        """查询指定动作的频率状态"""
        rule = self._get_rule(action_type)
        window_minutes = rule[1]
        window_window = window_minutes * 60

        self._prune_window(action_type, window_window)

        now = time.time()
        elapsed = now - self._last_time.get(action_type, 0)

        return {
            "action_type": action_type,
            "last_executed_ago": round(elapsed, 1),
            "count_in_window": len(self._window[action_type]),
            "max_count": rule[2],
            "window_minutes": window_minutes,
            "dynamic_factor": round(self._dynamic_factor[action_type], 2),
            "can_execute": self.can_execute(action_type),
        }

    # ── 内部方法 ──

    def _get_rule(self, action_type: str) -> tuple:
        """获取动作规则，未知动作使用默认安全值"""
        if action_type in self._DEFAULT_RULES:
            return self._DEFAULT_RULES[action_type]
        # 未知动作：保守间隔 10s，30 分钟内最多 20 次
        return (10.0, 30, 20)

    def _calc_interval_wait(self, action_type: str, min_interval: float) -> float:
        """计算满足最小间隔所需的等待时间"""
        elapsed = time.time() - self._last_time.get(action_type, 0)
        return max(0.0, min_interval - elapsed)

    def _calc_window_wait(self, action_type: str, rule: tuple) -> float:
        """
        计算满足滑动窗口限制所需的等待时间

        如果窗口已满，等待最早记录过期
        """
        min_interval, window_minutes, max_count = rule
        window_window = window_minutes * 60

        self._prune_window(action_type, window_window)

        if len(self._window[action_type]) < max_count:
            return 0.0  # 窗口未满，无需等待

        # 窗口已满，等待最早的那条记录过期
        oldest = self._window[action_type][0]
        wait = (oldest + window_window) - time.time()
        return max(0.0, wait)

    def _prune_window(self, action_type: str, window_window: float):
        """清除滑动窗口中已过期的记录"""
        cutoff = time.time() - window_window
        q = self._window[action_type]
        while q and q[0] < cutoff:
            q.popleft()