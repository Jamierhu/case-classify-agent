#!/bin/bash
# =====================================================================
# Docker 容器入口脚本
# 职责：启动 Ollama → 拉取模型 → 启动 FastAPI 应用
# =====================================================================
set -e

MODEL_NAME="${OLLAMA_MODEL:-qwen3-vl:8b}"

echo "[entrypoint] 启动 Ollama 服务..."
# 后台启动 Ollama
ollama serve &
OLLAMA_PID=$!

# 等待 Ollama API 就绪（最多 60 秒）
echo "[entrypoint] 等待 Ollama API 就绪..."
for i in $(seq 1 60); do
    if curl -s http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
        echo "[entrypoint] Ollama API 已就绪"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "[entrypoint] Ollama 启动超时"
        exit 1
    fi
    sleep 1
done

# 拉取模型
echo "[entrypoint] 检查模型 $MODEL_NAME..."
if ! ollama list 2>/dev/null | grep -q "$MODEL_NAME"; then
    echo "[entrypoint] 拉取模型 $MODEL_NAME（首次拉取体积较大）..."
    ollama pull "$MODEL_NAME"
else
    echo "[entrypoint] 模型 $MODEL_NAME 已存在，跳过拉取"
fi

echo "[entrypoint] 启动 FastAPI 应用..."
# 前台运行 FastAPI（使用 exec 替换进程，确保接收信号）
exec uvicorn case_classify_agent:app --host 0.0.0.0 --port 8000