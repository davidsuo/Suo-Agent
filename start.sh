#!/bin/bash
# start.sh - Render 部署启动脚本
# 作用：自动下载 Reranker 模型（如未下载），然后启动 uvicorn

#!/bin/bash
# 强制切到仓库根目录（Render 的 Root Directory 是 bus_memory，需要切回 src）
cd /opt/render/project/src || exit 1

# start.sh - Render 部署启动脚本
# 自动下载 Reranker 模型（如未下载），然后启动 uvicorn
...

set -e

MODEL_NAME="${RERANKER_MODEL:-BAAI/bge-reranker-base}"
MODEL_CACHE="/app/uploads/models"
MODEL_SAFE_NAME=$(echo "$MODEL_NAME" | sed 's|/|--|g')
MODEL_DIR="$MODEL_CACHE/models--$MODEL_SAFE_NAME"

echo "###启动脚本### Reranker 模型: $MODEL_NAME"
echo "###启动脚本### 缓存目录: $MODEL_DIR"

# 检查模型是否已完整下载
NEED_DOWNLOAD=1
if [ -d "$MODEL_DIR/snapshots" ]; then
    if find "$MODEL_DIR/snapshots" \( -name "*.safetensors" -o -name "pytorch_model.bin" \) 2>/dev/null | grep -q .; then
        NEED_DOWNLOAD=0
        echo "###启动脚本### 模型已存在且完整，跳过下载"
    fi
fi

if [ "$NEED_DOWNLOAD" = "1" ]; then
    echo "###启动脚本### 开始下载模型（首次约 1.1GB，请耐心等待）..."

    export HF_HOME="$MODEL_CACHE"
    export HF_HUB_CACHE="$MODEL_CACHE"
    export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
    export HF_HUB_ENABLE_HF_TRANSFER=0

    DOWNLOAD_OK=0
    for i in 1 2 3 4 5; do
        echo "###启动脚本### 下载尝试 $i/5 ..."
        if hf download "$MODEL_NAME" --cache-dir "$MODEL_CACHE"; then
            DOWNLOAD_OK=1
            echo "###启动脚本### 下载成功"
            break
        fi
        echo "###启动脚本### 第 $i 次失败，10 秒后重试..."
        sleep 10
    done

    # 再次校验
    if [ "$DOWNLOAD_OK" = "0" ]; then
        if find "$MODEL_DIR/snapshots" \( -name "*.safetensors" -o -name "pytorch_model.bin" \) 2>/dev/null | grep -q .; then
            echo "###启动脚本### 模型文件已就位"
        else
            echo "###启动脚本### ⚠️ 模型下载失败，将以纯 RRF 模式启动（不影响服务可用）"
        fi
    fi
fi

echo "###启动脚本### 启动 Uvicorn ..."
exec uvicorn common.main:app --host 0.0.0.0 --port "${PORT:-10000}"