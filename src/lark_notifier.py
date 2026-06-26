"""
飞书通知模块 — 通过 Webhook 推送 BOT 状态消息

职责：
  - 发送纯文本通知
  - 发送富文本交互式卡片（含异常详情、二维码等）
  - 支持图片 URL 嵌入

用法:
    from src.lark_notifier import LarkNotifier
    
    notifier = LarkNotifier()
    notifier.send_text("BOT 已启动")
    notifier.send_alert("严重风控", "触发滑块验证，需要人工处理")
"""

import json
import requests
import os
from datetime import datetime
from src.config import config


class LarkNotifier:
    """飞书自定义机器人通知封装"""

    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url or config.lark_webhook_url
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    @property
    def enabled(self) -> bool:
        """Webhook 是否已配置"""
        return bool(self.webhook_url)

    # ── 基础发送 ──

    def _send(self, payload: dict) -> bool:
        """发送消息到飞书，返回是否成功"""
        if not self.enabled:
            print("[LarkNotifier] Webhook 未配置，跳过通知")
            return False

        try:
            resp = self._session.post(
                self.webhook_url,
                json=payload,
                timeout=10,
            )
            result = resp.json()
            if result.get("code") != 0:
                print(f"[LarkNotifier] 发送失败: {result.get('msg', '未知错误')}")
                return False
            return True
        except requests.RequestException as e:
            print(f"[LarkNotifier] 网络异常: {e}")
            return False
        except json.JSONDecodeError:
            print(f"[LarkNotifier] 响应解析失败, status={resp.status_code}")
            return False

    # ── 消息类型 ──

    def send_text(self, text: str, at_all: bool = False) -> bool:
        """发送纯文本消息"""
        content = {"text": text}
        if at_all:
            content["text"] += "\n<at user_id='all'>所有人</at>"
        return self._send({
            "msg_type": "text",
            "content": content,
        })

    def send_post(self, title: str, content_lines: list[dict]) -> bool:
        """发送富文本消息（飞书 post 格式）"""
        return self._send({
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": content_lines,
                    }
                }
            },
        })

    def send_interactive(self, card: dict) -> bool:
        """发送交互式卡片消息"""
        return self._send({
            "msg_type": "interactive",
            "card": card,
        })

    # ── 便捷方法 ──

    def send_info(self, message: str) -> bool:
        """发送信息通知"""
        return self.send_text(f"ℹ️ 【信息】\n{message}")

    def send_alert(self, title: str, detail: str) -> bool:
        """发送告警（需要人工关注）"""
        return self.send_text(f"⚠️ 【{title}】\n{detail}")

    def send_critical(self, title: str, detail: str) -> bool:
        """发送严重告警（需要立即人工处理）"""
        return self.send_text(f"🚨 【{title}】\n{detail}")

    def send_qr_notice(self, screenshot_path: str = "") -> bool:
        """发送扫码登录通知（含二维码截图路径）"""
        if screenshot_path and os.path.isfile(screenshot_path):
            return self.send_text(
                "🔑 **扫码登录提醒**\n\n"
                "会话已过期，请在浏览器中完成扫码登录。\n"
                f"二维码截图已保存至: `{screenshot_path}`\n"
                "请使用 Boss 直聘 App / 微信扫描二维码完成登录。\n\n"
                "BOT 正在轮询等待登录完成..."
            )
        return self.send_text(
            "🔑 会话已过期，请在浏览器中完成扫码登录。\n"
            "BOT 正在等待登录完成..."
        )

    def _upload_image_to_url(self, filepath: str) -> str:
        """
        将本地图片上传到公网可访问的 URL。

        使用免费图床服务，支持通过 URL 在飞书卡片中直接展示图片。
        当前使用 telegra.ph 上传接口。

        Args:
            filepath: 本地图片路径

        Returns:
            str: 图片可访问的 URL（失败返回空字符串）
        """
        if not os.path.isfile(filepath):
            return ""

        upload_services = [
            # 方案1: telegra.ph — 免费，无API key，支持 multipart
            {
                "url": "https://telegra.ph/upload",
                "field": "file",
            },
        ]

        for service in upload_services:
            try:
                with open(filepath, "rb") as f:
                    resp = requests.post(
                        service["url"],
                        files={service["field"]: f},
                        timeout=15,
                    )
                if resp.status_code == 200:
                    result = resp.json()
                    if isinstance(result, list) and len(result) > 0:
                        src = result[0].get("src", "")
                        if src:
                            url = f"https://telegra.ph{src}" if src.startswith("/") else src
                            print(f"[LarkNotifier] 📤 图片已上传: {url}")
                            return url
                    elif isinstance(result, dict) and result.get("src"):
                        url = result["src"]
                        print(f"[LarkNotifier] 📤 图片已上传: {url}")
                        return url
            except Exception as e:
                print(f"[LarkNotifier] 上传失败 ({service['url']}): {e}")
                continue

        return ""

    def send_qr_screenshot_card(self, screenshot_path: str) -> bool:
        """
        发送带二维码图片的交互式卡片（图片通过公网URL展示）

        图片会上传到临时图床，生成可访问URL后在飞书卡片中直接展示。
        """
        image_url = self._upload_image_to_url(screenshot_path)

        elements = []

        if image_url:
            # 有公网URL → 直接展示图片 + 备用路径
            elements.append({
                "tag": "image",
                "img_key": image_url,  # 部分飞书版本支持直接URL
                "alt": {"tag": "plain_text", "content": "登录二维码"},
            })
            # 飞书卡片标准方式：在markdown中嵌入图片
            elements.append({
                "tag": "markdown",
                "content": (
                    f"**会话已过期，需要重新登录**\n\n"
                    f"请使用 Boss 直聘 App 或微信 **扫描下方二维码** 完成登录：\n"
                    f"![]({image_url})\n\n"
                    f"_截图时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_"
                ),
            })
        else:
            # 无公网URL → 仅显示路径和操作指引
            elements.append({
                "tag": "markdown",
                "content": (
                    f"**会话已过期，需要重新登录**\n\n"
                    f"二维码截图已保存至服务器:\n"
                    f"`{screenshot_path}`\n\n"
                    "**操作步骤：**\n"
                    "1. 打开上述路径下的截图文件\n"
                    "2. 使用 Boss 直聘 App 或微信扫描二维码\n"
                    "3. 确认登录后 BOT 将自动恢复运行\n\n"
                    f"_截图时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_"
                ),
            })

        elements.append({"tag": "hr"})
        elements.append({
            "tag": "note",
            "elements": [
                {"tag": "plain_text", "content": "JobS2 BOT · 自动发送"},
            ],
        })

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🔑 扫码登录提醒"},
                "template": "blue",
            },
            "elements": elements,
        }
        return self.send_interactive(card)

    def send_recovery(self, message: str = "BOT 已恢复正常运行") -> bool:
        """发送恢复通知"""
        return self.send_text(f"✅ {message}")

    # ── 交互式卡片（严重风控告警） ──

    def send_security_block_card(self, detail: str = "") -> bool:
        """发送安全风控卡片（含确认按钮）"""
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🚨 严重安全风控告警"},
                "template": "red",
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"**触发类型**：安全验证拦截\n"
                        f"**时间**：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"**详情**：{detail or 'BOT 触发了滑块/验证码拦截，已自动暂停'}\n\n"
                        "请尽快前往服务器，在浏览器界面上完成验证后恢复运行。"
                    ),
                },
                {"tag": "hr"},
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": "JobS2 BOT · 自动发送",
                        }
                    ],
                },
            ],
        }
        return self.send_interactive(card)