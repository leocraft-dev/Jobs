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
    TZ=Asia/Shanghai \
    # 告知 DrissionPage Chromium 二进制文件路径
    CHROME_BIN=/usr/bin/chromium

# ── Stage 2: 安装系统依赖 ──
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        # Chromium 浏览器（无需单独装 chromium-driver，DrissionPage 直接控制）
        chromium \
        chromium-common \
        # 虚拟显示（仅 headless=false 调试时需要，默认不强制安装以减小镜像）
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
        libasound2 \
        # 工具
        ca-certificates \
        curl \
        procps \
    && rm -rf /var/lib/apt/lists/*
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

# headless=true（默认）: DrissionPage 使用 Chromium 内建无头模式，不依赖显示设备
# headless=false（调试）: 需安装 xvfb: apt-get install -y xvfb xauth
#                        然后改为 ENTRYPOINT ["xvfb-run", "--auto-servernum", "--server-args=-screen 0 1920x1080x24"]
# BOT 默认使用临时目录存储浏览器配置和指纹数据（可通过环境变量覆盖）
ENTRYPOINT ["python", "-m", "src.main"]
