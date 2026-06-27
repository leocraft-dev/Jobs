# ====================================================================
# JobS2 — 智能招聘 BOT Docker 镜像
# 基于 DrissionPage + Chromium
# ====================================================================

# ── Stage 1: 基础镜像 ──
FROM python:3.11-slim-bookworm AS base

LABEL org.opencontainers.image.title="JobS2 - 智能招聘 BOT"
LABEL org.opencontainers.image.description="基于 DrissionPage 的 Boss 直聘自动化增量投递 BOT"
LABEL org.opencontainers.image.source="https://github.com/leocraft-dev/Jobs"
LABEL org.opencontainers.image.licenses="MIT"

# 环境变量：禁止 Python 字节码缓存、开启实时日志输出
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# ── Stage 2: 安装系统依赖 ──
RUN set -eux; \
    # 安装依赖工具
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        procps; \
    \
    # 添加 Google Chrome 官方仓库（最稳定的 Chromium 来源）
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg; \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list; \
    \
    # 安装 Google Chrome
    apt-get update; \
    apt-get install -y --no-install-recommends \
        google-chrome-stable \
        # 中文字体支持（Boss 直聘页面中文渲染）
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        # Chromium 在容器中运行所需基础库
        libnss3 \
        libnspr4 \
        libatk-bridge2.0-0 \
        libdrm2 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxrandr2 \
        libgbm1 \
        libpango-1.0-0 \
        libcairo2 \
        libasound2; \
    \
    # 清理
    rm -rf /var/lib/apt/lists/*; \
    \
    # 验证 Chrome 安装
    echo "=== 验证 Chrome 二进制 ==="; \
    which google-chrome-stable; \
    google-chrome-stable --version; \
    echo "=== Chrome 安装验证通过 ==="

# ── Stage 3: 安装 Python 依赖 ──
WORKDIR /app

# 先拷贝依赖文件以利用 Docker 缓存
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt

# ── Stage 4: 拷贝应用代码 ──
COPY . .

# 运行时数据目录
RUN mkdir -p /app/logs

# ── Stage 5: 入口 ──
ENTRYPOINT ["python", "-m", "src.main"]