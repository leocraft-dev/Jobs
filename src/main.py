"""
BOT 主入口 — 状态机驱动的运行主循环

状态机:
    INIT → SETUP → LOGIN_CHECK → [已登录] → SEARCH_LOOP
                                 → [未登录] → QR_WAIT → LOGIN_CHECK
                                                      → [失败] → STOP
    SEARCH_LOOP → 每一轮:
        1. 增量检索职位 (SpiderEngine)
        2. 对每个新职位执行沟通 (ActionExecutor)
        3. 飞书推送本轮统计
        4. 休眠直到下一轮

用法:
    python -m src.main
"""

import os
import sys
import time
import random
from datetime import datetime, timezone

from DrissionPage import ChromiumPage, ChromiumOptions

from src.config import config
from src.logger import get_logger
from src.lark_notifier import LarkNotifier
from src.fingerprint_store import FingerprintStore
from src.session_manager import SessionManager
from src.spider_engine import SpiderEngine
from src.action_executor import ActionExecutor
from src.rate_limiter import RateLimiter
from src.exceptions import (
    BotError,
    SessionExpired,
    SecurityBlocked,
    DailyLimitReached,
    classify_exception,
    should_stop_bot,
)

logger = get_logger("main")


class JobS2Bot:
    """JobS2 BOT 主控制器 — 状态机驱动的运行管理"""

    # ── 状态常量 ──
    STATE_INIT = "init"
    STATE_SETUP = "setup"
    STATE_LOGIN_CHECK = "login_check"
    STATE_QR_WAIT = "qr_wait"
    STATE_SEARCH_LOOP = "search_loop"
    STATE_STOPPED = "stopped"

    def __init__(self):
        self.state: str = self.STATE_INIT
        self.page: ChromiumPage | None = None

        # 模块引用（初始化时赋值）
        self.notifier: LarkNotifier | None = None
        self.store: FingerprintStore | None = None
        self.session: SessionManager | None = None
        self.spider: SpiderEngine | None = None
        self.executor: ActionExecutor | None = None
        self.limiter: RateLimiter | None = None

        # 运行状态
        self.start_time: float = 0.0
        self.consecutive_errors: int = 0
        self.max_consecutive_errors: int = 3
        self.running: bool = False

    # ── 公开接口 ──

    def run(self):
        """BOT 启动入口"""
        logger.info("=" * 50)
        logger.info("🚀 JobS2 BOT 启动")
        logger.info(f"配置: {config.to_dict()}")
        logger.info("=" * 50)

        self.start_time = time.time()
        self.running = True
        self.state = self.STATE_SETUP

        try:
            self._state_machine()
        except KeyboardInterrupt:
            logger.info("🛑 收到用户中断信号，BOT 安全退出")
            self.running = False
            self.state = self.STATE_STOPPED
        except Exception as e:
            logger.critical(f"💥 BOT 异常崩溃: {e}", exc_info=True)
            if self.notifier:
                self.notifier.send_critical("BOT 崩溃", str(e))
            self.state = self.STATE_STOPPED
        finally:
            self._cleanup()

    # ── 状态机 ──

    def _state_machine(self):
        """状态机主循环"""
        while self.running:
            logger.debug(f"[StateMachine] 当前状态: {self.state}")

            if self.state == self.STATE_SETUP:
                self._do_setup()
                self.state = self.STATE_LOGIN_CHECK

            elif self.state == self.STATE_LOGIN_CHECK:
                if self._do_login_check():
                    self.state = self.STATE_SEARCH_LOOP
                else:
                    self.state = self.STATE_STOPPED

            elif self.state == self.STATE_SEARCH_LOOP:
                self._do_search_loop()
                # 搜索循环结束后尝试恢复（若有不可恢复异常则停止）
                if self.state != self.STATE_STOPPED:
                    self.state = self.STATE_SEARCH_LOOP  # 继续循环

            elif self.state == self.STATE_QR_WAIT:
                if self._do_login_check():
                    self.state = self.STATE_SEARCH_LOOP
                else:
                    self.state = self.STATE_STOPPED

            elif self.state == self.STATE_STOPPED:
                logger.info("BOT 已停止")
                break

            # 状态间短暂延迟，防止死循环
            time.sleep(0.5)

    # ── 各状态执行逻辑 ──

    def _do_setup(self):
        """初始化 ChromiumPage 和所有模块"""
        logger.info("[状态] 初始化组件...")

        # 1. 创建 notifier（不依赖 browser）
        self.notifier = LarkNotifier()

        # 2. 初始化 DrissionPage
        co = ChromiumOptions()

        # 锁死浏览器路径（Docker 镜像中 Google Chrome 官方路径）
        co.set_browser_path('/usr/bin/google-chrome')

        # 核心：开启官方新版无头模式
        co.headless(True)

        # Docker 容器运行必需参数
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-setuid-sandbox')
        co.set_argument('--disable-dev-shm-usage')
        co.set_argument('--disable-gpu')

        # 指定端口和纯净的临时目录，规避挂载目录的所有可能冲突
        co.set_local_port(9222)
        co.set_argument('--remote-debugging-address', '0.0.0.0')
        co.set_user_data_path('/tmp/chrome_safe_data')

        self.page = ChromiumPage(co)
        logger.info(f"Chromium 浏览器已启动 (headless={config.headless})")

        # 3. 初始化业务模块
        self.store = FingerprintStore()
        self.session = SessionManager(self.page, self.notifier)
        self.spider = SpiderEngine(self.page, self.store, self.notifier)
        self.executor = ActionExecutor(self.page, self.store, self.notifier)
        self.limiter = RateLimiter()

        logger.info(f"✅ 组件初始化完成，指纹库已有 {self.store.count} 条记录")

    def _do_login_check(self) -> bool:
        """执行登录检测"""
        logger.info("[状态] 检测登录状态...")
        try:
            result = self.session.ensure_logged_in(max_retries=2)
            if result:
                logger.info("✅ 登录状态正常")
                self.notifier.send_info("BOT 已就绪，开始运行")
            else:
                logger.error("❌ 登录失败，无法继续")
                self.state = self.STATE_STOPPED
            return result
        except Exception as e:
            logger.error(f"登录检测异常: {e}")
            return False

    def _do_search_loop(self):
        """执行一轮搜索循环"""
        logger.info(f"\n{'='*50}")
        logger.info(f"🔄 开始新一轮检索 ({datetime.now(timezone.utc).isoformat()})")
        logger.info(f"{'='*50}")

        try:
            # 每日沟通计数重置（每次循环检查是否需要重置）
            self.executor.reset_daily_count()

            # 通过 SpiderEngine 增量检索
            new_jobs_count = 0
            for job_info in self.spider.search():
                new_jobs_count += 1

                # 频率限制：查看详情前等待
                self.limiter.wait("detail")
                self.limiter.record("detail")

                # 执行沟通过程
                result = self.executor.process_job(job_info)

                # 结果日志
                result_emoji = {
                    "contacted": "✅",
                    "skipped": "⏭️",
                    "blocked": "🚨",
                    "limit": "⏰",
                    "error": "❌",
                }.get(result, "❓")

                logger.info(
                    f"{result_emoji} [{job_info['company_name']}] "
                    f"{job_info['job_title']} ({job_info['salary']}) → {result}"
                )

                # 触发严重风控 → 停止 BOT
                if result == "blocked":
                    logger.critical("🚨 触发安全拦截，BOT 暂停")
                    self.state = self.STATE_STOPPED
                    return

                # 达日上限 → 等待次日
                if result == "limit":
                    logger.warning("⏰ 已达每日沟通上限，等待下一轮")
                    break

                # 频率限制（沟通后延迟）
                self.limiter.wait("chat")
                self.limiter.record("chat")

            # 推送本轮统计
            self._push_round_statistics(new_jobs_count)

            # 重置连续错误计数
            self.consecutive_errors = 0

        except SecurityBlocked as e:
            logger.critical(f"🚨 安全拦截异常: {e}")
            self.notifier.send_security_block_card(str(e))
            self.state = self.STATE_STOPPED
            return

        except DailyLimitReached as e:
            logger.warning(f"⏰ {e}")
            # 达上限后正常休眠等待下一轮

        except SessionExpired as e:
            logger.warning(f"会话过期: {e}")
            self.notifier.send_alert("会话过期", str(e))
            self.state = self.STATE_LOGIN_CHECK
            return

        except Exception as e:
            self.consecutive_errors += 1
            severity, recoverable, notify_type = classify_exception(e)
            logger.error(
                f"搜索循环异常 [{severity}]: {e} "
                f"(连续 {self.consecutive_errors}/{self.max_consecutive_errors})"
            )

            if notify_type:
                self.notifier.send_alert("运行异常", f"[{severity}] {e}")

            if self.consecutive_errors >= self.max_consecutive_errors:
                logger.critical("连续错误次数过多，BOT 停止")
                self.notifier.send_critical(
                    "连续错误",
                    f"已连续 {self.consecutive_errors} 次异常，BOT 停止运行",
                )
                self.state = self.STATE_STOPPED
                return

        # 休眠到下一轮
        interval = config.check_interval
        logger.info(f"💤 本轮完成，休眠 {interval} 秒后进入下一轮...")
        self._sleep_interruptible(interval)

    def _push_round_statistics(self, new_jobs_count: int):
        """推送本轮统计到飞书"""
        spider_stats = self.spider.stats if hasattr(self, "spider") else {}
        runtime_minutes = (time.time() - self.start_time) / 60

        stats_text = (
            f"📊 **BOT 运行统计**\n"
            f"- 运行时长: {runtime_minutes:.1f} 分钟\n"
            f"- 本轮扫描页数: {spider_stats.get('pages_scanned', 0)}\n"
            f"- 本轮扫描职位: {spider_stats.get('jobs_found', 0)}\n"
            f"- 本轮新职位: {spider_stats.get('new_jobs', 0)}\n"
            f"- 已沟通: {self.executor.daily_chat_count}\n"
            f"- 指纹库总计: {self.store.count}\n"
            f"- 下次检索时间: {datetime.now(timezone.utc).isoformat()}"
        )

        logger.info(f"\n{stats_text}")
        self.notifier.send_info(stats_text)

    def _sleep_interruptible(self, seconds: int):
        """可中断的休眠（每 10 秒检测一次是否需要退出）"""
        chunk = 10
        elapsed = 0
        while elapsed < seconds and self.running:
            time.sleep(min(chunk, seconds - elapsed))
            elapsed += chunk

    def _cleanup(self):
        """清理资源"""
        logger.info("正在清理资源...")
        if self.store:
            self.store.save()
        if self.page:
            try:
                self.page.quit()
            except Exception:
                pass
        logger.info("BOT 已完全停止")


# ==================== CLI 入口 ====================

def main():
    bot = JobS2Bot()
    bot.run()


if __name__ == "__main__":
    main()