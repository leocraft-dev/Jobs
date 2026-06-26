"""
会话管理 — 登录状态检测、Cookie 持久化与扫码轮询

核心凭证说明（基于 Boss 直聘鉴权体系）:
  - wt (Wave Token): 扫码确认后下发的核心登录凭证，有效期约 7 天
  - zpToken: 匿名访问时的初始化临时 Token，登录后与 wt 绑定
  - 双接口哨兵: /wapi/zpuser/wap/getUserInfo.json + /wapi/zpchat/notify/setting/get

检测策略（组合判定）:
  1. DOM 快速探测: 检查页面元素（头像、用户中心、登录按钮）
  2. API 哨兵兜底: DOM 不明确时调用 getUserInfo.json 100% 确认

状态机流程:
    INIT → CHECK_LOGIN → [已登录] → 返回 True
                        → [未登录] → QR_WAIT → [成功] → 返回 True
                                             → [超时] → 返回 False

用法:
    from src.session_manager import SessionManager
    
    mgr = SessionManager(page)
    if mgr.ensure_logged_in():
        print("会话就绪")
"""

import time
import json
import os
from datetime import datetime
from DrissionPage import ChromiumPage
from src.config import config
from src.lark_notifier import LarkNotifier


class SessionManager:
    """会话生命周期管理（基于 Cookie 持久化 + DOM/API 组合判定）"""

    # 登录页强制重定向时的 URL 片段
    _LOGIN_REDIRECT_FRAGMENTS = ("login", "qrcode", "passport", "geek/login")

    # 已登录用户的 DOM 专属选择器（按优先级排列）
    _LOGGED_IN_SELECTORS = (
        "text:用户中心",
        "text:我的简历",
        ".nav-figure",
        ".header-user-avatar",
        ".user-name",
    )

    # 未登录（游客）的 DOM 标识
    _GUEST_SELECTORS = (
        "text:登录",
        ".login-btn",
        ".geek-login-btn",
    )

    def __init__(
        self,
        page: ChromiumPage,
        notifier: LarkNotifier | None = None,
    ):
        self.page = page
        self.notifier = notifier or LarkNotifier()
        self._logged_in: bool = False

    # ══════════════════════════════════════════════
    #  公开接口
    # ══════════════════════════════════════════════

    def ensure_logged_in(self, max_retries: int = 1) -> bool:
        """
        确保会话有效（组合检测策略）。

        流程:
          1. 访问首页（携带本地持久化 Cookie 中的 wt 凭证）
          2. DOM 快速探测 + API 哨兵兜底
          3. 已登录 → 直接返回
          4. 未登录 → 跳转登录页，触发飞书扫码通知，轮询等待
        """
        self._navigate_home()
        self._logged_in = self._detect_login_status_combined()

        if self._logged_in:
            print("[SessionManager] ✅ 已登录（wt 凭证有效），复用本地 Session")
            return True

        # 未登录 — 启动扫码流程（扫码成功后后端的 /wapi/zppassport/get/wt 接口会写入 wt Cookie）
        return self._wait_for_scan(max_retries=max_retries)

    def is_logged_in(self) -> bool:
        """返回当前缓存的登录状态"""
        return self._logged_in

    def refresh_status(self) -> bool:
        """重新检测当前页面登录状态（DOM + API 双通道）"""
        self._logged_in = self._detect_login_status_combined()
        return self._logged_in

    # ══════════════════════════════════════════════
    #  组合判定核心
    # ══════════════════════════════════════════════

    def _detect_login_status_combined(self) -> bool:
        """
        组合判定法：DOM 快速探测 → API 哨兵兜底

        返回 True 表示已登录，False 表示未登录/游客。
        """
        # ── 阶段 A：被动判定（DOM 元素检查） ──
        dom_status = self._detect_by_dom()
        if dom_status is True or dom_status is False:
            # DOM 明确给出结果 → 直接返回
            if dom_status is True:
                print("[SessionManager] DOM 探测: ✅ 已登录元素存在")
            return dom_status

        # DOM 结果不明确（返回 None）, 进入阶段 B
        return self._detect_by_api()

    def _detect_by_dom(self):
        """
        DOM 元素被动判定（消耗资源最少）

        返回:
          True  — 明确已登录（找到用户中心/头像/我的简历）
          False — 明确未登录（找到登录按钮）
          None  — 不确定（两种标识均未出现，需 API 兜底）
        """
        try:
            # 检查是否被强制重定向到登录页
            current_url = self.page.url
            if any(frag in current_url for frag in self._LOGIN_REDIRECT_FRAGMENTS):
                return False

            # 检查已登录标识
            for selector in self._LOGGED_IN_SELECTORS:
                try:
                    if self.page.ele(selector, timeout=1):
                        return True
                except Exception:
                    continue

            # 检查未登录标识（登录按钮）
            for selector in self._GUEST_SELECTORS:
                try:
                    if self.page.ele(selector, timeout=1):
                        return False
                except Exception:
                    continue

            # 两种标识都未出现 → 不确定，需要 API 兜底
            return None

        except Exception as e:
            print(f"[SessionManager] DOM 检测异常: {e}")
            return None  # DOM 失败，依赖 API 兜底

    def _detect_by_api(self) -> bool:
        """
        API 哨兵主动判定（100% 准确）

        调用 /wapi/zpuser/wap/getUserInfo.json
          - code: 0 + zpData 含用户信息 → 已登录
          - 其他错误码 → 未登录/凭证过期
        """
        print("[SessionManager] DOM 判定不明确，启动 API 哨兵检测...")

        api_url = (
            "https://www.zhipin.com/wapi/zpuser/wap/getUserInfo.json"
        )

        try:
            # 通过 DrissionPage 执行 AJAX 请求（携带当前页面的全部 Cookie）
            resp_data = self.page.run_js(f"""
                (async () => {{
                    try {{
                        const resp = await fetch('{api_url}', {{
                            credentials: 'include',
                            headers: {{ 'Accept': 'application/json' }}
                        }});
                        return await resp.json();
                    }} catch(e) {{
                        return {{code: -1, msg: e.message}};
                    }}
                }})();
            """)

            if isinstance(resp_data, dict):
                code = resp_data.get("code")
                if code == 0:
                    zpdata = resp_data.get("zpData", {})
                    username = zpdata.get("name", "") or zpdata.get(
                        "realName", ""
                    )
                    print(f"[SessionManager] API 哨兵: ✅ 已登录 (用户: {username})")
                    return True
                else:
                    print(
                        f"[SessionManager] API 哨兵: ❌ 未登录 "
                        f"(code={code})"
                    )
                    return False

            print(
                "[SessionManager] API 哨兵: ⚠️  响应格式异常，"
                "视为未登录"
            )
            return False

        except Exception as e:
            print(f"[SessionManager] API 哨兵请求异常: {e}")
            # API 调用失败 → 保守起见视为未登录
            return False

    # ══════════════════════════════════════════════
    #  导航与扫码流程
    # ══════════════════════════════════════════════

    def _navigate_home(self) -> None:
        """
        访问 Boss 直聘首页

        浏览器会自动携带本地持久化的 Cookie（含 wt 凭证）。
        如果 wt 有效，后端直接返回已登录页面；否则展示游客页。
        """
        print("[SessionManager] 正在访问 Boss 直聘首页（携带 wt Cookie）...")
        self.page.get("https://www.zhipin.com/")
        time.sleep(2)

    def _take_screenshot(self, prefix: str = "login_qr") -> str:
        """
        对当前页面截图并保存到 config.screenshot_dir

        通过 CDP Page.captureScreenshot 命令截取，最稳定可靠。

        返回:
            截图的完整文件路径（若失败返回空字符串）
        """
        try:
            screenshot_dir = config.screenshot_dir
            os.makedirs(screenshot_dir, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{prefix}_{timestamp}.png"
            filepath = os.path.join(screenshot_dir, filename)

            # 使用 Chrome DevTools Protocol 的 Page.captureScreenshot
            # run_cdp(cmd, **cmd_args) — 参数字典需要用 ** 解包为关键字参数
            result = self.page.run_cdp(
                "Page.captureScreenshot",
                format="png",
                fromSurface=True,
            )

            if result and "data" in result:
                import base64
                img_data = base64.b64decode(result["data"])
                with open(filepath, "wb") as f:
                    f.write(img_data)
                print(f"[SessionManager] 📸 截图已保存: {filepath}")
                return filepath
            else:
                print("[SessionManager] ⚠️  截图生成失败（CDP 返回空）")
                return ""

        except Exception as e:
            print(f"[SessionManager] 截图异常: {e}")
            return ""

    def _navigate_to_login(self) -> str:
        """
        打开登录界面（二维码弹窗）并截图

        Boss 直聘采用 SPA 架构，登录分两步：
          1. 点击首页的"登录"按钮 → 弹出登录模态框（默认显示密码/验证码登录面板）
          2. 点击模态框内的"微信登录/注册"链接 → 切换并展示二维码

        流程:
          1. 访问首页
          2. 点击"登录"按钮打开模态框
          3. 点击"微信登录/注册"触发二维码
          4. 等待二维码加载
          5. 截图

        返回:
            二维码截图路径（若截图失败返回空字符串）
        """
        print("[SessionManager] 打开登录弹窗...")
        self.page.get("https://www.zhipin.com/")
        time.sleep(2)

        # ── 第1步：点击首页登录按钮 → 弹出模态框 ──
        login_selectors = [
            "text:登录",
            ".login-btn",
            ".geek-login-btn",
            ".header-login-btn",
            "text:登录/注册",
        ]
        login_clicked = False
        for selector in login_selectors:
            try:
                btn = self.page.ele(selector, timeout=2)
                if btn:
                    print(f"[SessionManager] 🔍 找到首页登录按钮: {selector}")
                    btn.click()
                    login_clicked = True
                    time.sleep(2)
                    break
            except Exception:
                continue

        if not login_clicked:
            print("[SessionManager] ⚠️  未找到首页登录按钮，尝试 JS 触发...")
            try:
                self.page.run_js("""
                    const btn = document.querySelector('.login-btn')
                        || document.querySelector('.geek-login-btn')
                        || [...document.querySelectorAll('a, button, span, div')]
                            .find(el => el.textContent.includes('登录'));
                    if (btn) btn.click();
                """)
                time.sleep(2)
            except Exception as e:
                print(f"[SessionManager] JS 点击登录失败: {e}")

        # ── 第2步：检测模态框状态，确保二维码展示 ──
        # 模态框有时直接展示微信扫码，此时不需要额外点击
        # 如果模态框默认展示的是密码/验证码面板，则需要点击"微信登录/注册"切换
        def _is_qr_visible() -> bool:
            """检测当前页面是否已显示二维码"""
            try:
                return bool(self.page.run_js("""
                    // 检测二维码常见特征：canvas（二维码渲染）、img[src*='qrcode']、
                    // 或包含 visible QR 区域的 div
                    const qrCanvas = document.querySelector('canvas.qrcode, .qrcode-img canvas');
                    const qrImg = document.querySelector('img[src*="qrcode"], img[src*="qr"]');
                    const qrEle = document.querySelector('.qrcode-wrapper, .qrcode-container, '
                        + '.wechat-qrcode, [class*="qrcode"]');
                    // 查找模态框/登录区域中的 visible 二维码元素
                    const hasQrElement = qrCanvas || qrImg || qrEle;
                    if (hasQrElement) return true;
                    // 部分页面用 canvas 画二维码，检查登录模态框中是否有显著尺寸的 canvas
                    const canvases = document.querySelectorAll('.login-modal canvas, '
                        + '.dialog-content canvas, [class*="login"] canvas');
                    for (const c of canvases) {
                        if (c.width > 100 && c.height > 100) return true;
                    }
                    return false;
                """) or False)
            except Exception:
                return False

        if _is_qr_visible():
            print("[SessionManager] ✅ 二维码已显示，无需切换微信登录")
        else:
            print("[SessionManager] 🔍 未检测到二维码，尝试切换至微信登录...")
            wechat_clicked = False
            wechat_selectors = [
                "text:微信登录/注册",
                "text:微信登录",
                ".wechat-login-btn",
                ".qrcode-login-btn",
                "text:扫码登录",
            ]
            for selector in wechat_selectors:
                try:
                    wechat_btn = self.page.ele(selector, timeout=2)
                    if wechat_btn:
                        print(f"[SessionManager] 🔍 找到微信登录入口: {selector}")
                        wechat_btn.click()
                        wechat_clicked = True
                        time.sleep(3)  # 等待二维码渲染
                        break
                except Exception:
                    continue

            if not wechat_clicked:
                print("[SessionManager] ⚠️  未找到微信登录入口，尝试 JS 点击...")
                try:
                    self.page.run_js("""
                        const w = [...document.querySelectorAll('a, button, span, div')]
                            .find(el => el.textContent.includes('微信'));
                        if (w) w.click();
                    """)
                    time.sleep(3)
                except Exception as e:
                    print(f"[SessionManager] JS 点击微信登录失败: {e}")

        # 额外等待确保二维码完全渲染
        time.sleep(1)

        # 截图当前页面
        print(f"[SessionManager] 📸 当前页面URL: {self.page.url}")
        screenshot_path = self._take_screenshot("login_qr")
        return screenshot_path

    def _wait_for_scan(self, max_retries: int = 1) -> bool:
        """
        扫码等待循环

        用户扫码确认后，后端执行:
          1. /wapi/zppassport/qrcode/loginConfirm（扫码确认）
          2. /wapi/zppassport/get/wt（下发 wt 凭证，写入 Cookie）

        BOT 每 2 秒轮询检测登录状态，wt 入库后自动恢复。
        """
        for attempt in range(1, max_retries + 1):
            print(
                f"[SessionManager] 🔄 扫码流程 "
                f"(第 {attempt}/{max_retries} 次)"
            )
            screenshot_path = self._navigate_to_login()

            # 发送带截图的飞书通知
            if screenshot_path:
                self.notifier.send_qr_notice(screenshot_path)
                self.notifier.send_qr_screenshot_card(screenshot_path)
            else:
                self.notifier.send_qr_notice()

            poll_count = 0
            max_polls = 180  # 最多等待 6 分钟（180 × 2s）

            while poll_count < max_polls:
                time.sleep(2)
                poll_count += 1

                if self._detect_login_status_combined():
                    self._logged_in = True
                    self.notifier.send_recovery(
                        "用户已成功扫码（wt 凭证已写入 Cookie）"
                    )
                    print("[SessionManager] 🎉 登录成功！")
                    return True

                if poll_count % 30 == 0:
                    print(
                        f"[SessionManager] ⏳ 等待扫码... "
                        f"已等待 {poll_count * 2} 秒"
                    )

            print(
                f"[SessionManager] ⏰ 第 {attempt} 次扫码等待超时 "
                f"(6分钟)"
            )
            if attempt < max_retries:
                self.notifier.send_alert(
                    "扫码超时",
                    f"第 {attempt} 次扫码等待超时，"
                    f"即将重试（共 {max_retries} 次）",
                )

        self.notifier.send_critical(
            "登录失败",
            f"已重试 {max_retries} 次扫码均超时，请人工检查。",
        )
        self._logged_in = False
        return False
