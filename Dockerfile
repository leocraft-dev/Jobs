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
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        # Chromium 浏览器
        chromium \
        chromium-driver \
        # 虚拟显示（无头环境需要）
        xvfb \
        xauth \
        # 中文字体支持（Boss 直聘页面中文渲染）
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        # 工具
        ca-certificates \
        curl \
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

# 使用 xvfb-run 启动虚拟显示，使 Chromium 可在无显示器环境下运行
# BOT 默认使用临时目录存储浏览器配置和指纹数据（可通过环境变量覆盖）
ENTRYPOINT ["xvfb-run", "--auto-servernum", "--server-args=-screen 0 1920x1080x24"]
CMD ["python", "-m", "src.main"]