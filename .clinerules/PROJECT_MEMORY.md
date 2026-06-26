# JobS2 — 智能招聘 BOT 项目记忆体

> 本文件记录项目关键信息、架构决策、实现状态与未来规划。  
> 每次重大变更后需同步更新此文件，保证记忆体与项目实际状态一致。

---

## 1. 项目概述

| 字段 | 值 |
|------|-----|
| **项目名称** | JobS2 — 智能招聘 BOT |
| **核心目标** | 基于 DrissionPage 实现 Boss 直聘自动化增量投递与沟通 |
| **技术栈** | Python 3.10+ / DrissionPage / requests / 飞书 Webhook |
| **开发状态** | 🔴 未开始（仅存在技术方案说明书） |

---

## 2. 架构概览

```
┌───────────────────────────────────────────────────────┐
│                    BOT Core                           │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │ Session Mgr │  │ Spider Engine│  │Action Executor│ │
│  │ (登录/扫码)  │  │ (增量检索)   │  │ (沟通/点击)   │ │
│  └──────┬──────┘  └──────┬───────┘  └──────┬───────┘ │
│         │                 │                  │          │
│  ┌──────┴──────────────────┴──────────────────┴──────┐ │
│  │              Lark Notifier (异常通知)              │ │
│  └───────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────┘
```

### 2.1 核心模块

| 模块 | 职责 | 关键依赖 |
|------|------|----------|
| `SessionManager` | 登录状态检测、Cookie 持久化、扫码轮询 | DrissionPage ChromiumPage |
| `SpiderEngine` | 增量检索、职位指纹去重、分页控制 | hashlib / 本地 JSON 持久化 |
| `ActionExecutor` | 职位详情打开、沟通按钮点击、人性化延迟 | DrissionPage / random |
| `LarkNotifier` | 飞书 Webhook 异常推送、扫码通知 | requests |

---

## 3. 技术决策记录 (ADR)

### ADR-001: 选用 DrissionPage 而非 Selenium/Playwright

- **背景**: 目标平台拥有严格的客户端环境检测体系
- **决策**: 使用 DrissionPage 的原生 Chromium 控制能力
- **依据**: DrissionPage 无需处理 `navigator.webdriver` 等指纹覆盖，天然具备无痕特征
- **影响**: 调试阶段保持有头模式，稳定后再切换无头

### ADR-002: 本地文件持久化而非 Redis

- **背景**: 职位指纹库需在重启后保持，且项目初期部署简单优先
- **决策**: 使用本地 JSON 文件存储 `known_jobs` 指纹库
- **依据**: 单机运行场景无需 Redis，降低运维复杂度
- **影响**: 未来多实例扩展时需迁移至 Redis

### ADR-003: 状态机驱动的生命周期设计

- **背景**: BOT 行为需要在不同阶段间有序切换，并处理异常回退
- **决策**: 采用显式状态机模式，而非简单的线性流程
- **状态定义**:
  ```
  INIT → LOGIN_CHECK → [已登录] → SEARCH_LOOP
                      → [未登录] → QR_WAIT → LOGIN_CHECK
  ```

### ADR-004: 临时目录存储而非项目本地数据

- **背景**: 用户要求部署时不依赖项目目录下的本地持久化数据，每次部署应为独立环境
- **决策**: `user_data_path` 和 `fingerprint_path` 默认指向 `%TEMP%`（系统临时目录）
- **依据**: 
  - 部署时无需保留 `.env` 以外的任何本地文件
  - 每次部署自动获得干净浏览器会话和空指纹库
  - 可通过 `BOT_USER_DATA_PATH` / `BOT_FINGERPRINT_PATH` 覆盖为持久路径
- **实现**: 使用 `field(default_factory=...)` 延迟求值，确保 `.env` 加载后生效

---

## 4. 当前实现状态

### ✅ 已完成（全部完成）

| 优先级 | 模块 | 文件 | 状态 |
|--------|------|------|------|
| 🔴 P0 | 项目目录结构 | `src/`, `data/`, `logs/`, `browser_profile/` | ✅ |
| 🔴 P0 | 依赖配置 | `requirements.txt` | ✅ |
| 🔴 P0 | 配置中心 | `src/config.py` | ✅ 含 BotConfig dataclass + 环境变量/文件加载 + validate() |
| 🟡 P1 | 飞书通知模块 | `src/lark_notifier.py` | ✅ 含 text/post/interactive + 6 种便捷方法 |
| 🟡 P1 | 职位指纹持久化 | `src/fingerprint_store.py` | ✅ 含 CRUD + MD5 生成 + 提前终止判定 + JSON 持久化 |
| 🟡 P1 | 会话管理 | `src/session_manager.py` | ✅ 含登录检测 + 扫码轮询 + 飞书通知联动 |
| 🟡 P1 | 职位检索引擎 | `src/spider_engine.py` | ✅ 含多页检索 + 增量去重 + 安全拦截检测 |
| 🟡 P1 | 动作执行器 | `src/action_executor.py` | ✅ 含详情页打开 + 按钮判定点击 + 日上限控制 |
| 🟢 P2 | 频率限制器 | `src/rate_limiter.py` | ✅ 含滑动窗口 + 动态降速 + 3 种动作类型 |
| 🟢 P2 | 异常体系 | `src/exceptions.py` | ✅ 含 7 种自定义异常 + classify + should_stop |
| 🟢 P2 | 日志系统 | `src/logger.py` | ✅ 含控制台彩色 + 文件按天滚动 + get_logger |
| 🟢 P2 | 主入口 & 状态机 | `src/main.py` | ✅ 含 JobS2Bot 状态机 + CLI 入口 |

### 📋 实现验证

- 2026-06-26: 全部 11 个 Python 模块导入验证通过
- 依赖: DrissionPage 4.1.1.4 ✅ / requests 2.34.2 ✅

---

## 5. 关键数据模型

### PositionFingerprint（职位指纹）
```python
{
    "fingerprint": "md5(job_title_company_name_salary)",
    "job_title": str,
    "company_name": str,
    "salary": str,
    "first_seen_at": "ISO-8601 timestamp",
    "status": "new" | "contacted" | "skipped",
    "url": str
}
```

### BotConfig（BOT 配置）
```python
{
    "search_url": str,          # 搜索目标 URL
    "check_interval": 300,      # 轮询间隔（秒）
    "min_delay": 3.0,           # 最小人性化延迟
    "max_delay": 7.0,           # 最大人性化延迟
    "daily_chat_limit": 150,    # 每日沟通上限
    "max_consecutive_empty": 3, # 连续空结果触发告警阈值
    "lark_webhook_url": str,    # 飞书 Webhook URL
}
```

---

## 6. 风控策略矩阵

| 触发条件 | 系统反应 | 通知级别 |
|----------|----------|----------|
| Cookie 过期 / 未登录 | 触发扫码流程，飞书推送二维码通知 | 🚨 需要人工 |
| URL 含 `security-check` | 立即暂停自动化，推送严重告警 | 🚨 需要人工 |
| 连续 3 次空列表 | 降低检索频率，推送警告 | ⚠️ 观察 |
| 沟通按钮异常（灰色/消失） | 跳过该职位，记录日志 | ℹ️ 记录 |
| 操作频率超阈值 | 自动增加延迟，动态降速 | ⚠️ 记录 |

---

## 7. 目录结构规划

```
d:\mini\JobS2\
├── .clinerules/
│   ├── index.md            # 技术方案说明书（Cline 规则）
│   └── PROJECT_MEMORY.md   # 项目记忆体（本文件）
├── src/
│   ├── __init__.py
│   ├── config.py           # 配置管理
│   ├── session_manager.py  # 登录与会话管理
│   ├── spider_engine.py    # 增量检索引擎
│   ├── action_executor.py  # 动作执行器
│   ├── lark_notifier.py    # 飞书通知
│   ├── fingerprint_store.py # 指纹持久化
│   ├── rate_limiter.py     # 频率限制
│   └── main.py             # 主入口
├── browser_profile/        # Chromium 用户数据目录（gitignore）
├── data/
│   └── known_fingerprints.json  # 职位指纹持久化文件
├── logs/                   # 运行日志（gitignore）
├── pyproject.toml          # 项目元信息与依赖
├── requirements.txt        # pip 依赖清单
└── .gitignore
```

---

## 8. 依赖清单（草案）

```txt
# requirements.txt
DrissionPage>=4.2.0
requests>=2.30.0
```

---

## 9. 变更日志

| 日期 | 变更 | 作者 |
|------|------|------|
| 2026-06-26 | 初始创建 | Cline |

---

## 10. 相关链接 / 参考

- [DrissionPage 官方文档](https://drissionpage.cn/)
- [飞书自定义机器人文档](https://open.feishu.cn/document/ukzMwcjNxMDMwYzMxYTM4NDM)