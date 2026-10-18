# AskToFit 售前智能导购

基于 FastAPI + Vue 3 + MySQL + Milvus 的电商服装售前知识问答系统。

覆盖多知识库检索、混合向量召回、重排序、查询改写、多轮记忆、SSE 流式输出、知识库解析与分块管理。

## 文档格式与知识库权限

- 支持 PDF、PPT / PPTX、Word（DOC / DOCX）、Markdown、PNG / JPG。
- PDF / PPTX / DOCX 内嵌的尺码图、流程图、海报等图片会自动 OCR，并作为可检索文本入库。
- 旧版 Word `.doc` 按 LibreOffice → antiword → OLE 文本提取的顺序自动降级解析。
- 知识库支持“公共 / 私有”两种可见范围：
  - 私有库仅创建者可查看、检索、上传和管理。
  - 公共库所有登录账号都可查看文档、预览分块并在对话中选择使用；只有创建者可以编辑、上传、删除和重建索引。

## 父子分块、增量更新与判重

- **父子分块**：先按标题 / 章节 / 页码聚合 800–1200 字左右的父分块保存完整上下文，再在父块内切 300–500 字的子分块用于向量检索；命中子块后自动用父块正文补全上下文。
- **正文判重**：入库前对文档做 NFKC 归一化、去空白 / 标点 / Markdown / HTML / URL，再计算 SHA-256 与 SimHash。
  - 只改标点、格式或文件名：识别为内容未变化（`skipped`），不会产生重复文档。
  - 正文相似度 ≥ `DOC_DEDUP_SIMILARITY`（默认 0.90）：作为同一逻辑文档的新版本，成功后自动切换，旧版本退出检索。
  - 低于阈值且文件名不同：作为独立新文档入库。
- **安全切换**：新版本先完成解析、切块、向量写入，数据库提交成功后才把旧版本设为非当前；任一步失败，旧版本继续可检索。
- **分块与检索去重**：父块、子块和检索结果分别按归一化哈希、SimHash 与文本相似度去重，避免近似资料重复进入提示词。
- 上传时可勾选“强制作为新文档入库”跳过判重；同名文档再次上传会按新版本处理。
- 相关参数见 `.env.example` 中的 `PARENT_CHUNK_SIZE`、`DOC_DEDUP_SIMILARITY`、`CHUNK_NEAR_DUP_SIMILARITY`、`RETRIEVAL_DEDUP_SIMILARITY`。

## MinerU 深度解析（可选）

复杂扫描件、多栏 PDF、跨页表格或排版混乱的 Office 文档，可在上传弹窗开启
「深度解析（MinerU）」。文档解析在独立 Docker 容器中完成，不影响主服务依赖。

```bash
# 构建 CPU 镜像（约 6-10GB，首次需下载模型，15-40 分钟）
docker build -t asktofit-mineru:cpu docker/mineru

# 后端启动后可在上传弹窗看到开关；也可直接检查接口
curl http://127.0.0.1:8000/api/parsers/options
```

- 仅 PDF / PPT / PPTX / DOC / DOCX / 图片支持 MinerU；Markdown 使用标准解析。
- 解析器的选择会记录在文档上，重建索引沿用同一解析器，判重口径保持一致。
- 显式选择 MinerU 后若 Docker、镜像或解析失败，文档标记为失败并保留原版本可检索，
  不会静默降级为标准解析。
- 默认单并发、单次超时 900 秒，容器超时会自动清理；参数见 `.env.example` 中的
  `MINERU_IMAGE`、`MINERU_TIMEOUT_SECONDS`、`MINERU_MAX_CONCURRENCY`、`MINERU_MEMORY`。
- 镜像构建与手动验证方式见 `docker/mineru/README.md`。

## 意图识别（本地 BERT）

- 默认加载 `models/intent-bert/`，对问题做 10 类意图分类：退换货、发票、物流、支付结算、退款与资金安全、尺码、会员与优惠、商品咨询、问候、综合咨询。
- 置信度低于 `INTENT_CONFIDENCE_THRESHOLD` 或 top1/top2 差值低于 `INTENT_MARGIN_THRESHOLD` 时，自动回退到加权关键词规则；模型异常不会阻断问答。
- 意图结果写入 Redis 缓存（`INTENT_CACHE_TTL`），对话气泡与知识缺口运营台会显示意图来源（BERT / 关键词）与置信度。
- 设置 `INTENT_ENABLED=false` 可完全关闭 BERT，仅使用关键词规则。
- 重新训练：

```bash
uv run python scripts/train_intent_bert.py --epochs 8
```

训练数据位于 `data/intent/`，脚本会输出 `models/intent-bert/metrics.json`（分类报告、混淆矩阵、各轮验证指标）。

## 缓存、限流与监控

- Redis 缓存：意图、查询向量、检索结果、完整回答分别使用独立前缀；知识库 / 文档 / 分块发生变更时自动失效检索与回答缓存。
- 接口限流：基于 Redis ZSet 滑动窗口，默认对提问接口按用户限流；Redis 不可用时放行并记录告警，不阻断服务。
- 监控：`GET /api/metrics` 输出 Prometheus 文本格式（HTTP 请求量 / 延迟、LLM 调用、检索与缓存命中）；可在 `.env` 中关闭。
- 会话归档：超过 `CONVERSATION_RETENTION_DAYS` 未更新的会话由后台任务自动归档，仍可在前端“查看已归档”中找回。

## 知识缺口运营

当检索最高分低于阈值且重排未命中时，系统会把问题、意图、置信度、向量分 / 重排分和检索模式写入知识缺口表。
前端「缺口运营」页支持按状态 / 意图 / 关键词筛选、查看趋势与高频问题、标记解决 / 忽略、重新打开以及填写处理备注。

## 多策略检索

- 规则规划器按 `subquery > backtrack > hyde > direct` 的优先级选择策略，LLM 只负责生成子查询 / 回溯问题 / HyDE 假设答案，不参与策略决策。
- 触发条件：多要素问题（并且、分别、对比、区别、多个问号）走子查询；条件/宽泛问题走回溯；抽象方法类问题走 HyDE；短实体、数字与金额问题保持直接检索。
- HyDE 文本只用于向量召回，绝不进入提示词、引用或 `sources`；重排序始终使用原始改写问题，避免丢失金额、时效等约束。
- 生成失败、超时或输出为空时自动回退直接检索；`STRATEGY_ENABLED=false` 可整体关闭。
- 问答 SSE 的 `meta` 事件会返回 `retrieval_strategy`、`strategy_source`、`retrieval_query_count`，不暴露 HyDE 文本。

## BM25 词法召回

- 纯 Python Okapi BM25（不依赖 rank-bm25 / jieba），中文按 unigram + bigram 分词；文档标题与章节权重高于正文。
- `BM25_MODE=fuse`（默认）时，BM25 结果与各 `(embedding_mode, query)` 向量结果一起做 RRF 融合；`fallback` 仅在向量召回为空或 embedding / Milvus 异常时兜底；`off` 关闭。
- 每个知识库一份进程内索引，TTL 默认 120 秒；知识库、文档、分块变更后统一调用 `invalidate_search_caches` 同时清理 Redis 缓存与 BM25 索引。
- 重排序关闭时，BM25-only 命中按 `BM25_MIN_QUERY_COVERAGE` 过滤；`RERANK_FAILURE_MODE=drop` 仍会丢弃 BM25-only 候选，需要降级运行时可改为 `vector`。

## 评估看板（官方 RAGAS）

检索指标由 `app/services/evaluation.py` 的纯函数计算（doc 级 / section 级 Hit@K、Recall@K、MRR、NDCG@K）；生成指标使用官方 RAGAS 0.3.0，通过独立 overlay 安装，不进入运行时 `.venv`。

```bash
# 数据集格式校验：不需要数据库、不需要 ragas（CI 使用）
uv run python scripts/evaluate_rag.py --validate-dataset

# 连知识库校验文档 / 章节 / 关键词
uv run python scripts/evaluate_rag.py --validate-only

# 仅检索指标
uv run python scripts/evaluate_rag.py --retrieval-only --limit 10

# 官方 RAGAS 生成指标
uv run --with-requirements scripts/eval_requirements.txt \
  python scripts/evaluate_rag.py --limit 5 \
  --judge-metrics faithfulness,answer_relevancy
```

评估结果默认写入 `eval_runs` / `eval_cases`，报告同时落盘到 `reports/eval_<timestamp>.json|.md`；`--no-persist` 可只出报告。数据集说明见 `data/evaluation/README.md`。

看板仅管理员可见。首次授权：

```bash
uv run python scripts/set_admin.py --username <账号>
# 或在 .env 中配置 ADMIN_USERNAMES=admin1,admin2，启动时幂等提升
```

管理员登录后导航出现「评估看板」，可查看概览、趋势、运行列表、逐题详情、失败筛选与删除；普通账号访问 `/api/evaluations/*` 返回 403，直接访问 `/evaluation` 会被重定向到对话页。

## 压测与背压

Locust 放在独立 `loadtest` 依赖组，不进入运行时依赖：

```bash
uv sync --group loadtest
uv run python scripts/prepare_load_test.py --users 20
uv run locust -f scripts/locustfile.py --headless -u 20 -r 2 -t 60s \
  --host http://127.0.0.1:8000
uv run python scripts/prepare_load_test.py --cleanup
```

- 准备脚本要求存在可检索的**公共**知识库，会创建带 `loadtest_` 前缀的临时用户，只把 JWT 写入 `loadtest/users.json`，不打印 token。
- 压测任务覆盖健康检查、知识库列表、会话列表、缺口统计与 SSE 问答；SSE 校验 `meta` / `done`、回答非空且无 `error`。
- `response_time` 对 `stream=True` 只统计首包，因此另记 `SSE_FIRST`（首 token 延迟）与 `SSE_TOTAL`（整段耗时）。
- 压测建议 `CACHE_ENABLED=false`，并按并发数调整 `RATE_LIMIT_REQUESTS`，否则会主要压到限流器。

## CI/CD 与容器

- `.github/workflows/ci.yml`：后端 pytest、评估数据集校验、前端 vitest + build、API/前端镜像构建验证；不依赖 MySQL / Milvus / 模型 / 密钥，也不跑真实检索与 RAGAS。
- `.github/workflows/cd.yml`：推送 `v*` tag 或手动触发，构建并推送 `ghcr.io/<owner>/asktofit-api` 与 `asktofit-web`，不自动部署。
- 本地镜像：

```bash
docker build -t asktofit-api:local .
docker build -t asktofit-web:local frontend
```

- 服务器部署：

```bash
export GHCR_OWNER=<github 用户名或组织>
docker compose -f docker-compose.deploy.yml pull
docker compose -f docker-compose.deploy.yml up -d
```

模型不打进镜像，compose 将 `./models` 只读挂载到 `/models`；MySQL / Redis / Milvus 默认按 `.env` 连接外部服务。前端 nginx 已对 SSE 关闭代理缓冲并放宽读取超时。

## 依赖服务

- MySQL 8（默认 `localhost:3306`，库名 `asktofit`）
- Milvus 2.4（默认 `localhost:19530`）
- Redis（默认 `localhost:6379`，用于图形验证码、意图 / 检索 / 回答缓存与接口限流）
- 本地 BGE-M3 / BGE Reranker / 意图分类 BERT 模型，默认目录为
  `models/bge-m3`、`models/bge-reranker-v2-m3`、`models/intent-bert`；
  模型放在其他位置时在 `.env` 中设置 `BGE_M3_PATH` / `BGE_RERANKER_PATH` / `INTENT_MODEL_PATH`
- pymilvus 需为 **2.4.x**：混合检索使用了 2.4 的底层 handler，启动检索时会校验版本

复制 `.env.example` 为 `.env` 并按实际环境填写。**不要把 `.env` 提交到版本库。**

## 启动后端

```bash
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

- 接口文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/health

## 启动前端

```bash
cd frontend
npm install
npm run dev
```

访问 http://127.0.0.1:5173 。开发服务器会把 `/api` 代理到 `http://127.0.0.1:8000`。

生产方式构建：

```bash
npm run build
npm run preview
```

## 业务文档生成与导入

`data/business_docs/` 下提供 9 份服装售前业务文档（退换货、尺码、面料、发票、物流、会员优惠、异常 SOP、618 海报、旧版 Word 应答卡），覆盖表格、流程图、尺码图与图片 OCR 场景。

```bash
# 生成业务文档（旧版 .doc 需要本机安装 Microsoft Word）
uv run python scripts/build_business_docs.py

# 导入 eli 账号名下的公共知识库，并执行真实检索抽查
uv run python scripts/import_business_docs.py --username eli
```

导入完成后，其他账号（如 `demo`）登录即可在知识库列表和对话设置中看到该公共知识库。

## 测试

```bash
# 后端单元测试
uv run python -m pytest tests/ -v

# 前端单元测试
cd frontend && npm run test

# 前端端到端验收（需先启动后端与前端）
cd frontend && npm run test:e2e
```

端到端脚本覆盖注册（含图形验证码）、登录、知识库创建、Markdown 上传解析、分块搜索与编辑、
会话创建、SSE 流式问答、知识引用、404 等场景；会自动创建并清理临时账号与知识库。
脚本默认使用本机 Playwright 缓存中的 Chromium，也可通过 `CHROME_PATH` 指定 Chrome/Edge。
验证码从本地 Redis 读取，因此仅适用于开发环境。

## 目录结构

```
app/                FastAPI 应用（API、服务、模型、Schema）
frontend/           Vue 3 前端（Vite + Pinia + Element Plus）
frontend/e2e/       端到端验收脚本与测试文档
scripts/            数据导入、检索验证、BERT 训练、评估与压测脚本
data/intent/        意图分类训练 / 验证 / 测试集
data/evaluation/    人工评估集（qa_eval.jsonl）与字段说明
models/             本地模型（BGE-M3、BGE Reranker、intent-bert）
reports/            评估报告（JSON / Markdown，已 gitignore）
loadtest/           压测用户文件（含 JWT，已 gitignore）
tests/              后端 pytest 单元测试
logs/               后端运行日志（控制台 + 文件）
uploads/            上传文档存储目录
```
