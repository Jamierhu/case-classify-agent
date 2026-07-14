FROM python:3.11-slim

# ---------- 系统依赖 ----------
# curl: 下载 Ollama
# libgl1/libglib2.0-0: opencv 运行时依赖
# procps: 提供 pkill 等进程管理工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgl1 \
    libglib2.0-0 \
    procps \
    && rm -rf /var/lib/apt/lists/*

# ---------- 安装 Ollama ----------
RUN curl -fsSL https://ollama.com/install.sh | sh

# ---------- 工作目录 ----------
WORKDIR /app

# 先复制依赖文件，利用 Docker 层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY case_classify_agent.py ./
COPY static/ ./static/

# 数据持久化目录（通过 volume 挂载）
RUN mkdir -p /app/case_output /app/case_images_temp /root/.ollama

# 环境变量
ENV OLLAMA_HOST=0.0.0.0:11434
ENV OLLAMA_NUM_PARALLEL=4
ENV OLLAMA_KEEP_ALIVE=-1
ENV OLLAMA_FLASH_ATTENTION=1

EXPOSE 8000 11434

# 启动脚本：先启动 Ollama → 拉取模型 → 启动 FastAPI
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]