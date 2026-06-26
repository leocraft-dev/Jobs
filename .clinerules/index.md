智能招聘 BOT 自动化方案设计技术说明书 (基于 DrissionPage)一、 系统架构与关键选型说明1. 核心选型：为什么选择 DrissionPage？目标平台拥有极为严格的客户端环境检测体系。普通的 Selenium、Playwright（默认配置）会暴露出 navigator.webdriver 变量、特定的浏览器指纹、或因 CDP 混淆特征引发反爬。DrissionPage 的优势：它不使用 WebDriver 协议，而是通过原生控制浏览器端口或直接通过 Chromium 的底层协议进行通信，天生具备无痕、防检测特性。无需复杂的 JS 注入覆盖，即可完美隐藏自动化特征。2. 核心架构设计系统采用状态机架构与事件驱动机制，整体分为以下四大核心模块：控制中心（BOT Core）：管理整体会话生命周期与核心业务流转。数据检索与过滤模块（Spider Engine）：执行增量职位检索，维护本地已爬取指纹库。动作执行器（Action Executor）：基于 DrissionPage 实现高仿真的人性化点击、滚动及文本输入。通知支撑系统（Lark Notifier）：处理需要人工干预的异常，通过 Webhook 向飞书推送即时消息。二、 核心用户旅程与增量检索设计系统的运行以核心用户旅程为主线，同时穿插数据增量判定逻辑。1. 状态机与生命周期流转BOT 的核心运行流程如下状态机所示：[初始化] ──> [检测本地 Cookie] ──(有效)──> [进入主页 / 增量检索]
                │
              (失效/首次)
                ▼
         [触发飞书扫码通知] ──> [等待扫码成功] ──> [进入主页 / 增量检索]
                                                       │
   ┌───────────────────────────────────────────────────┘
   ▼
[解析职位列表] ──> [比对 MD5 指纹库]
                     ├─ (已存在) ──> [跳过]
                     └─ (新职位) ──> [进入职位详情] ──> [判定沟通条件] ──> [发送打招呼消息]
2. 增量检索与去重逻辑为了实现“增量检索”，系统在本地或 Redis 中维护一个职位指纹库：唯一标识生成：提取职位的 jobId、securityId 或将“公司名 + 职位名 + 薪资”通过 MD5 加密生成唯一 Hash。增量判定：每次抓取搜索结果页（如前 3 页）时，依次比对指纹。若发现连续 N 个职位已存在于本地库中，则提前终止本轮检索，判定无更多增量更新，以节省风控额度。三、 代码实现规范与框架以下为基于 DrissionPage 搭建的 BOT 核心骨架代码，包含初始化、飞书通知、登录校验、增量检索及打招呼的完整链路：Pythonimport time
import hashlib
import requests
from DrissionPage import ChromiumPage, ChromiumOptions

# ==================== 配置中心 ====================
LARK_WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxxxx"
SEARCH_URL = "https://www.zhipin.com/web/geek/job?query=Python&city=101020100"  # 示例：上海 Python
CHECK_INTERVAL = 300  # 轮询间隔（秒）

class ZhipinBot:
    def __init__(self):
        # 初始化 Chromium 配置，确保无痕特征
        co = ChromiumOptions()
        # co.headless() # 调试阶段建议保持有头模式，稳定后再考虑无头
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-gpu')
        # 设置固定的用户数据目录，以便复用本地 Cookie 状态
        co.set_user_data_path(path='./browser_profile')
        
        self.page = ChromiumPage(co)
        self.known_jobs = set()  # 本地已处理职位库（实际生产中请持久化至文件或数据库）

    def send_lark_notification(self, text, image_url=None):
        """飞书 Webhook 通知机制"""
        headers = {"Content-Type": "application/json"}
        payload = {
            "msg_type": "text",
            "content": {
                "text": f"🚨 【BOT 状态通知】\n{text}"
            }
        }
        # 如果需要富文本或图片（如二维码链接），可在此扩展飞书的 post/interactive 格式
        try:
            res = requests.post(LARK_WEBHOOK_URL, json=payload, headers=headers)
            return res.json()
        except Exception as e:
            print(f"发送飞书通知失败: {e}")

    def check_login_status(self):
        """核心用户旅程 1：登录状态检测与扫码辅助机制"""
        print("[Step 1] 检查登录状态...")
        self.page.get("https://www.zhipin.com/")
        time.sleep(2)
        
        # 通过页面右上角是否包含“用户头像”或“我的简历”判定是否登录
        if self.page.ele('text:用户中心') or self.page.ele('.nav-figure'):
            print("✅ 状态：已成功登录，复用本地 Session。")
            return True
        
        print("❌ 状态：未登录或 Cookie 失效，跳转登录页...")
        self.page.get("https://www.zhipin.com/web/geek/login")
        time.sleep(3)
        
        # 截取登录二维码并发送给飞书（或发送人工干预提醒）
        self.send_lark_notification("检测到会话失效，需要人工辅助扫码登录！请尽快处理。")
        
        # 轮询等待用户在物理浏览器上完成扫码，直到跳转回主页
        while True:
            if "login" not in self.page.url:
                print("🎉 登录成功！已检测到页面跳转。")
                self.send_lark_notification("用户已成功扫码，BOT 恢复运行。")
                break
            time.sleep(2)

    def execute_incremental_search(self):
        """核心用户旅程 2：增量检索机制"""
        print(f"[Step 2] 开始增量检索职位... URL: {SEARCH_URL}")
        self.page.get(SEARCH_URL)
        time.sleep(4) # 模拟人眼等待加载
        
        # 获取职位卡片列表
        job_cards = self.page.eles('.job-card-wrapper')
        if not job_cards:
            print("⚠️ 未找到职位列表，可能触发了零结果或临时风控")
            return
        
        new_jobs_found = 0
        
        for card in job_cards:
            try:
                # 提取关键信息用于去重
                job_title = card.ele('.job-name').text
                comp_name = card.ele('.company-name').text
                salary = card.ele('.salary').text
                
                # 生成唯一指纹
                fingerprint = hashlib.md5(f"{job_title}_{comp_name}_{salary}".encode('utf-8')).hexdigest()
                
                if fingerprint in self.known_jobs:
                    # 增量核心：已存在则跳过
                    continue
                
                print(f"🔥 发现新职位: [{comp_name}] - {job_title} ({salary})")
                self.known_jobs.add(fingerprint)
                new_jobs_found += 1
                
                # 核心用户旅程 3：自动进入详情并进行聊天
                self.process_job_detail(card)
                
                # 人性化延迟，防止点击过快被风控
                time.sleep(5)
                
            except Exception as card_err:
                print(f"解析卡片异常: {card_err}")
                continue
                
        print(f"ℹ️ 本轮检索完成，共处理新职位: {new_jobs_found} 个。")

    def process_job_detail(self, card_element):
        """核心用户旅程 3：新页面新标签打开职位详情并自动聊天"""
        try:
            # 采用新标签页打开详情，避免主列表页刷新丢失状态
            # DrissionPage 点击链接会自动处理新开标签页
            job_link = card_element.ele('.job-card-left')
            
            # 点击并获取新开的标签页
            self.page.set.window_tabs_to_all() # 确保捕获新标签
            job_link.click()
            time.sleep(2)
            
            # 切换到最新打开的标签页（详情页）
            detail_tab = self.page.get_tab(self.page.latest_tab)
            print(f"-> 已进入详情页: {detail_tab.title}")
            
            # 寻找“立即沟通”按钮
            chat_btn = detail_tab.ele('.btn-container') or detail_tab.ele('text:立即沟通')
            
            if chat_btn:
                btn_text = chat_btn.text
                if "继续沟通" in btn_text or "有新消息" in btn_text:
                    print("ℹ️ 该职位之前已经沟通过，跳过。")
                elif "立即沟通" in btn_text:
                    print("🚀 正在发起自动聊天...")
                    chat_btn.click()
                    time.sleep(3) # 等待动作完成
                    print("✅ 已成功发送打招呼信息！")
                else:
                    print(f"⚠️ 按钮状态异常 ({btn_text})，可能无法沟通。")
            else:
                # 如果检测到滑块或其他拦截
                if "security-check" in detail_tab.url or detail_tab.ele('text:验证码'):
                    self.send_lark_notification("🚨 触发严重安全风控（滑块验证），BOT 已暂停，请前往服务器排查！")
                    input("人工解决后请按回车继续...")
            
            # 关闭详情页标签，切回主列表页
            detail_tab.close()
            
        except Exception as e:
            print(f"处理职位详情异常: {e}")

    def run_loop(self):
        """BOT 运行主循环"""
        self.check_login_status()
        while True:
            try:
                self.execute_incremental_search()
                print(f"休眠中，将在 {CHECK_INTERVAL} 秒后进行下一轮增量检索...")
                time.sleep(CHECK_INTERVAL)
            except KeyboardInterrupt:
                print("BOT 安全退出。")
                break
            except Exception as e:
                self.send_lark_notification(f"系统运行异常中断: {e}")
                time.sleep(60)

if __name__ == "__main__":
    bot = ZhipinBot()
    bot.run_loop()
四、 关键风控对抗与稳定性保障指南为了确保 0-1 开发的 BOT 不会在上线运行几小时内就被封禁，必须严格遵守以下反爬规避方案：1. 人性化行为模拟 (Anti-Bot Avoidance)不要使用原生高频循环：点击卡片和标签页之间，必须引入 random.uniform(3, 7) 的动态随机等待时间。高仿点击：DrissionPage 的 .click() 默认执行无痕点击。在需要输入文本时（如有自定义聊天话术），尽量使用类似人手敲击键盘的延迟，不要直接使用大段文本 value = "..." 暴力覆盖。2. 精细化的频率限制建议表请将您的 BOT 运行频率严格控制在以下安全阈值以内：动作类型业务对应接口/操作推荐安全频率风控高危表现全量/增量搜索搜索结果页切换、关键词变更＞10 秒 / 次触发 IP 封禁或強制跳转至 login 页详情页查看点击职位卡片查看详情＞5 秒 / 次页面跳转至 about:blank 或安全检查页沟通打招呼点击“立即沟通”按钮＞15 秒 / 次，且每日上限建议不超过 100-200 次按钮变灰、提示操作频繁、账号涉嫌违规被限制沟通3. Lark Webhook 异常通知触发矩阵必须在以下“用户辅助环节”嵌入飞书通知：未登录或会话（wt Cookie）过期：触发扫码通知。页面出现验证码/拼图滑块：由于 DrissionPage 遭遇强制安全跳转（URL 含有 security-check），立即发送带有服务器当前环境警告的飞书卡片，暂停自动化，等待人工在界面上手动完成滑块验证后恢复。连续 3 次获取列表为空：可能被临时实施了 IP 限制或信誉度降级。