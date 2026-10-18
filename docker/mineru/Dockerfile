# MinerU CPU 深度解析镜像
#
# 构建：
#   docker build -t asktofit-mineru:cpu docker/mineru
#
# 说明：
# - 构建期用 ModelScope 预下载 pipeline 模型，运行时可完全离线；
# - 同时安装 LibreOffice，用于 MinerU 解析 PPT / Word 等 Office 文档；
# - 镜像约 6-10GB，首次构建视网络情况需要 15-40 分钟。

FROM python:3.12-slim

ARG MINERU_VERSION=2.6.4
ARG MINERU_MODEL_SOURCE=modelscope
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    MINERU_MODEL_SOURCE=${MINERU_MODEL_SOURCE}

# Debian 官方源在国内可能较慢，构建时切换阿里云镜像（可用 --build-arg 覆盖）
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's#deb.debian.org#mirrors.aliyun.com#g' /etc/apt/sources.list.d/debian.sources; \
    fi \
    && if [ -f /etc/apt/sources.list ]; then \
        sed -i 's#deb.debian.org#mirrors.aliyun.com#g' /etc/apt/sources.list; \
    fi

# libgl1 / libglib2.0-0 供 OpenCV 使用；LibreOffice 负责 Office 格式转换
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libmagic1 \
        fonts-noto-cjk \
        libreoffice-writer \
        libreoffice-impress \
    && rm -rf /var/lib/apt/lists/*

# 先装 CPU 版 PyTorch，避免 mineru[core] 拉取体积巨大的 CUDA 依赖
RUN pip install --no-cache-dir \
        --index-url "${TORCH_INDEX_URL}" \
        torch torchvision

RUN pip install --no-cache-dir "mineru[core]==${MINERU_VERSION}"

# MinerU 的下载脚本会在模型下载完成后访问 jsdelivr 拉取模板；
# 该域名在国内不可达，先用合法的最小配置占位，脚本就会跳过远程模板。
RUN printf '%s\n' \
        '{"config_version": "1.3.1", "models-dir": {"pipeline": "", "vlm": ""}, "model-source": "local"}' \
        > /root/mineru.json

# 预下载 pipeline 模型；下载完成后脚本会把实际模型目录写回 mineru.json
RUN mineru-models-download -s "${MINERU_MODEL_SOURCE}" -m pipeline

# 构建期校验：模型目录必须已写入配置，避免镜像构建成功但运行时找不到模型
RUN python - <<'PY'
import json
from pathlib import Path

config = json.loads(Path('/root/mineru.json').read_text(encoding='utf-8'))
pipeline_dir = config.get('models-dir', {}).get('pipeline')
if not pipeline_dir or not Path(pipeline_dir).is_dir():
    raise SystemExit(f'pipeline 模型目录无效: {pipeline_dir!r}')
print(f'pipeline models: {pipeline_dir}')
PY

# 运行时强制使用本地模型，容器以 --network none 启动也不会尝试下载
ENV MINERU_MODEL_SOURCE=local

WORKDIR /data
ENTRYPOINT ["mineru"]
CMD ["--help"]
