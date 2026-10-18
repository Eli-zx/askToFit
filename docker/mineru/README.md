# MinerU CPU 深度解析镜像

用于解析复杂版式文档（多栏 PDF、扫描件、复杂表格、PPT / Word / 图片）。
后端通过 `docker run` 调用，输入文件只读挂载，输出目录读取 `*_content_list.json`。

## 构建

```bash
docker build -t asktofit-mineru:cpu docker/mineru
```

- 镜像约 6-10GB，首次构建需要下载 CPU 版 PyTorch、MinerU 依赖与 pipeline 模型。
- 如果 ModelScope 模型源不可用，可尝试：

```bash
docker build \
  --build-arg MINERU_MODEL_SOURCE=huggingface \
  -t asktofit-mineru:cpu docker/mineru
```

- 默认使用阿里云 PyPI / Debian 镜像加速；如需使用官方源：

```bash
docker build \
  --build-arg PIP_INDEX_URL=https://pypi.org/simple \
  -t asktofit-mineru:cpu docker/mineru
```

- 自定义 MinerU 版本：`--build-arg MINERU_VERSION=2.6.4`。

## 验证

```bash
# 查看镜像
docker image inspect asktofit-mineru:cpu --format '{{.Id}}'

# 手动解析一个 PDF（输出到当前目录的 out/）
docker run --rm --network none \
  -v "$PWD/sample.pdf:/input/sample.pdf:ro" \
  -v "$PWD/out:/output" \
  asktofit-mineru:cpu \
  mineru -p /input/sample.pdf -o /output -b pipeline -l ch -d cpu

ls out/*/auto/*_content_list.json
```

Windows PowerShell 请把 `$PWD` 换成绝对路径，例如 `D:/data`。

## 资源配置

- 建议至少 8GB 内存、10GB 可用磁盘。
- 默认单并发，由 `MINERU_MAX_CONCURRENCY` 控制。
- 单次解析超时由 `MINERU_TIMEOUT_SECONDS` 控制（默认 900 秒），超时会自动清理容器。
