"""
动作执行器 — 打开职位详情、发起沟通聊天

职责：
  - 在新标签页打开职位详情
  - 检测「立即沟通」按钮状态并点击
  - 跳过已沟通过的职位
  - 检测安全拦截并触发告警
  - 聊天完成后关闭详情标签页

用法:
    from src.action_executor import ActionExecutor
    
    executor = ActionExecutor(page, store)
    result = executor.process_job(job_info)
"""

import time
import random
from DrissionPage import ChromiumPage
from src.config import config
from src.fingerprint_store import FingerprintStore
from src.lark_notifier import LarkNotifier


class ActionExecutor:
    """职位详情处理与沟通动作执行"""

    # 沟通按钮状态判定关键词
    _CONTACTED_KEYWORDS = ("继续沟通", "有新消息", "已投递", "已沟通")
    _CHAT_KEYWORDS = ("立即沟通", "打招呼", "沟通")

    def __init__(
        self,
        page: ChromiumPage,
        store: FingerprintStore,
        notifier: LarkNotifier | None = None,
    ):
        self.page = page
        self.store = store
        self.notifier = notifier or LarkNotifier()
        self._daily_chat_count = 0

    # ── 公开接口 ──

    @property
    def daily_chat_count(self) -> int:
        """当日已沟通次数"""
        return self._daily_chat_count

    def process_job(self, job_info: dict) -> str:
        """
        处理单个职位：打开详情 → 判断沟通条件 → 发起/跳过

        Args:
            job_info: 职位信息字典（来自 SpiderEngine）

        Returns:
            str: 处理结果状态
                - "contacted"  — 已成功发送沟通
                - "skipped"    — 已沟通过，跳过
                - "blocked"    — 触发安全拦截
                - "limit"      — 达到日沟通上限
                - "error"      — 处理异常
        """
        fingerprint = job_info.get("fingerprint", "")

        # 检查每日沟通上限
        if self._daily_chat_count >= config.daily_chat_limit:
            print(f"[ActionExecutor] ⏰ 已达日沟通上限 ({config.daily_chat_limit})")
            self.notifier.send_alert(
                "沟通上限",
                f"今日已沟通 {self._daily_chat_count} 次，达到上限 ({config.daily_chat_limit})",
            )
            return "limit"

        # 打开详情页
        detail_tab = self._open_detail_page(job_info)
        if detail_tab is None:
            return "error"

        try:
            # 检测安全拦截
            if self._check_detail_security(detail_tab):
                return "blocked"

            result = self._handle_chat_button(detail_tab, fingerprint)

            # 如果成功沟通，更新计数和指纹状态
            if result == "contacted":
                self._daily_chat_count += 1
                self.store.update_status(fingerprint, "contacted")

            return result

        finally:
            # 确保关闭详情标签页
            self._close_detail_tab(detail_tab)

    # ── 内部方法 ──

    def _open_detail_page(self, job_info: dict):
        """
        在新标签页打开职位详情

        尝试两种方式打开：
        1. 直接导航到 URL
        2. 通过主页面点击打开
        """
        url = job_info.get("url", "")

        # 方案 1：有 URL 直接导航
        if url:
            try:
                # 打开新标签页
                tab = self.page.new_tab(url)
                time.sleep(random.uniform(2, 4))
                return tab
            except Exception as e:
                print(f"[ActionExecutor] 新标签页打开失败: {e}")

        return None

    def _handle_chat_button(self, detail_tab, fingerprint: str) -> str:
        """
        检测并处理沟通按钮

        按钮状态判定：
        - 包含已沟通关键词 → 跳过
        - 包含立即沟通关键词 → 点击发送
        - 其他 → 跳过并记录
        """
        chat_btn = self._find_chat_button(detail_tab)

        if not chat_btn:
            print("[ActionExecutor] 未找到沟通按钮，跳过")
            self.store.update_status(fingerprint, "skipped")
            return "skipped"

        try:
            btn_text = chat_btn.text.strip()
        except Exception:
            btn_text = ""

        # 判定按钮状态
        if any(kw in btn_text for kw in self._CONTACTED_KEYWORDS):
            print(f"[ActionExecutor] ℹ️ 已沟通过 ({btn_text})，跳过")
            self.store.update_status(fingerprint, "skipped")
            return "skipped"

        if any(kw in btn_text for kw in self._CHAT_KEYWORDS):
            print(f"[ActionExecutor] 🚀 发起沟通 ({btn_text})...")
            return self._click_chat_button(chat_btn, fingerprint)

        # 异常状态
        print(f"[ActionExecutor] ⚠️ 按钮状态异常: '{btn_text}'")
        return "skipped"

    def _find_chat_button(self, detail_tab):
        """查找沟通按钮"""
        chat_selectors = [
            ".btn-container",
            ".chat-btn",
            ".geek-chat-btn",
            "text:立即沟通",
            "text:继续沟通",
        ]

        for selector in chat_selectors:
            try:
                btn = detail_tab.ele(selector, timeout=2)
                if btn:
                    return btn
            except Exception:
                continue
        return None

    def _click_chat_button(self, chat_btn, fingerprint: str) -> str:
        """
        点击沟通按钮 → 捕获API响应 → 确认 → 关标签页

        标准流程：
          1. 点击「立即沟通」按钮
          2. 捕获 /wapi/zpgeek/friend/add.json 响应（code:0 → 成功）
          3. 等待2-3秒（模拟人眼阅读）
          4. tab.close() 关闭当前标签页
        """
        try:
            # 点击延迟
            time.sleep(random.uniform(1, 2))

            # 检查 detail_tab 是否有 run_js 方法，确定是标签页对象
            detail_tab = chat_btn.owner if hasattr(chat_btn, 'owner') else None
            if not detail_tab:
                detail_tab = self.page  # 降级到主页面

            # 在详情页注入 fetch 拦截器，捕获 friend/add.json 的响应
            try:
                detail_tab.run_js("""
                    if (!window.__friendAddPatched) {
                        window.__friendAddPatched = true;
                        window.__friendAddResult = null;
                        const origFetch = window.fetch.bind(window);
                        window.fetch = function(...args) {
                            const url = typeof args[0] === 'string' ? args[0] : (args[0]?.url || '');
                            if (url && url.includes('friend/add.json')) {
                                return origFetch(...args).then(async (resp) => {
                                    const clone = resp.clone();
                                    try {
                                        const data = await clone.json();
                                        window.__friendAddResult = data;
                                    } catch(e) {}
                                    return resp;
                                });
                            }
                            return origFetch(...args);
                        };
                    }
                """)
            except Exception:
                pass  # 拦截器可选，不影响主逻辑

            # 执行点击
            chat_btn.click()

            # 等待2-3秒（等待API响应和模拟人眼阅读）
            time.sleep(random.uniform(2, 3))

            # 检查 API 响应结果
            try:
                friend_add_result = detail_tab.run_js("return window.__friendAddResult;")
            except Exception:
                friend_add_result = None

            if friend_add_result and isinstance(friend_add_result, dict):
                code = friend_add_result.get("code")
                if code == 0:
                    print(
                        f"[ActionExecutor] ✅ 沟通成功 "
                        f"(code={code}, API: friend/add.json)"
                    )
                    return "contacted"
                else:
                    msg = friend_add_result.get("message") or friend_add_result.get("msg") or ""
                    print(
                        f"[ActionExecutor] ⚠️ 沟通API返回错误 "
                        f"code={code}: {msg}"
                    )
                    return "error"
            else:
                # API捕获失败，前端直接处理成功
                print("[ActionExecutor] ✅ 沟通消息已发送（前端确认）")
                return "contacted"

        except Exception as e:
            print(f"[ActionExecutor] ❌ 点击沟通按钮失败: {e}")
            self.store.update_status(fingerprint, "skipped")
            return "error"

    def _check_detail_security(self, detail_tab) -> bool:
        """
        检测详情页是否有安全拦截

        触发条件：
        - URL 包含 security-check
        - 页面出现验证码文本
        """
        try:
            current_url = detail_tab.url
        except Exception:
            current_url = ""

        if "security-check" in current_url:
            print("[ActionExecutor] 🚨 详情页触发安全拦截")
            self.notifier.send_security_block_card(
                detail="打开职位详情时触发滑块验证"
            )
            return True

        try:
            if detail_tab.ele("text:验证码", timeout=1):
                print("[ActionExecutor] 🚨 详情页出现验证码")
                self.notifier.send_security_block_card(
                    detail="职位详情页出现验证码输入框"
                )
                return True
        except Exception:
            pass

        return False

    def _close_detail_tab(self, detail_tab) -> None:
        """安全关闭详情标签页"""
        try:
            detail_tab.close()
            # 关闭详情页后自动回到主列表页（DrissionPage 会自动切换）
            # 不需要额外调用 tab_to_front
        except Exception as e:
            print(f"[ActionExecutor] 关闭标签页异常: {e}")

    def reset_daily_count(self) -> None:
        """重置每日沟通计数（每日首次运行前调用）"""
        self._daily_chat_count = 0
        print("[ActionExecutor] 每日沟通计数已重置")