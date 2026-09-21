#!/bin/bash
cd /opt/render/project/src || exit 1

MODEL_NAME="${RERANKER_MODEL:-BAAI/bge-reranker-base}"
MODEL_CACHE="/app/uploads/models"
MODEL_SAFE_NAME=$(echo "$MODEL_NAME" | sed 's|/|--|g')
MODEL_DIR="$MODEL_CACHE/models--$MODEL_SAFE_NAME"

echo "###启动脚本### Reranker 模型: $MODEL_NAME"
echo "###启动脚本### 缓存目录: $MODEL_DIR"

echo "###启动脚本### 清理残留临时文件..."
find "$MODEL_CACHE" -name "*.incomplete" -delete 2>/dev/null || true
find "$MODEL_CACHE" -name "*.lock" -delete 2>/dev/null || true
rm -rf "$MODEL_CACHE/models--cross-encoder--ms-marco-MiniLM-L-6-v2" 2>/dev/null || true

echo "###启动脚本### 当前磁盘:"
df -h /app/uploads

NEED_DOWNLOAD=1
if [ -d "$MODEL_DIR/snapshots" ]; then
    if find "$MODEL_DIR/snapshots" \( -name "*.safetensors" -o -name "pytorch_model.bin" \) 2>/dev/null | grep -q .; then
        NEED_DOWNLOAD=0
        echo "###启动脚本### 模型已存在且完整，跳过下载"
    fi
fi

if [ "$NEED_DOWNLOAD" = "1" ]; then
    echo "###启动脚本### 开始下载模型（约 1.1GB，首次部署请耐心等待）..."

    export HF_HOME="$MODEL_CACHE"
    export HF_HUB_CACHE="$MODEL_CACHE"
    export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
    export HF_HUB_ENABLE_HF_TRANSFER=0
    export HF_HUB_DISABLE_XET=1

    DOWNLOAD_OK=0
    for i in 1 2 3 4 5; do
        echo "###启动脚本### 下载尝试 $i/5 ..."
        if hf download "$MODEL_NAME" --cache-dir "$MODEL_CACHE"; then
            DOWNLOAD_OK=1
            echo "###启动脚本### 下载成功"
            break
        fi
        echo "###启动脚本### 第 $i 次失败，10 秒后重试..."
        find "$MODEL_CACHE" -name "*.incomplete" -delete 2>/dev/null || true
        sleep 10
    done

    if [ "$DOWNLOAD_OK" = "0" ]; then
        if find "$MODEL_DIR/snapshots" \( -name "*.safetensors" -o -name "pytorch_model.bin" \) 2>/dev/null | grep -q .; then
            echo "###启动脚本### 模型文件已就位"
        else
            echo "###启动脚本### 模型下载失败，将以纯 RRF 模式启动（不影响服务可用）"
        fi
    fi
fi

echo "###启动脚本### 启动 Uvicorn ..."
exec uvicorn common.main:app --host 0.0.0.0 --port "${PORT:-10000}"