# 评估数据集说明

`qa_eval.jsonl` 是 AskToFit 的人工评估集，共 60 条，覆盖 `data/business_docs/`
下的 9 份业务资料、9 类业务意图，其中 12 条为知识库未收录的不可回答问题。
数据逐条手写，不使用脚本生成。

## 字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 唯一编号，格式 `eval-001` |
| `question` | 是 | 评估问题（用户原始问法） |
| `ground_truth` | 是 | 参考答案；不可回答时说明“知识库未收录” |
| `expected_doc_names` | 是 | 期望命中的原始文档名（`Document.original_name`） |
| `expected_sections` | 是 | 期望命中的章节；无章节结构时为空数组 |
| `expected_keywords_all` | 是 | 上下文中必须全部出现的关键词 |
| `expected_keywords_any` | 是 | 上下文中至少出现一个的关键词 |
| `answerable` | 是 | 知识库是否有证据回答 |
| `intent` | 是 | 业务意图，见 `app/services/intent.py` |
| `tags` | 是 | `single-hop` / `multi-hop` / `conditional` / `unanswerable` |

不可回答问题必须满足 `answerable=false` 且 `expected_doc_names=[]`；
可回答问题至少有文档、章节或关键词之一作为证据。

## 相关性判定口径

- 匹配前统一做 NFKC、转小写、去除空白与标点，再做双向包含匹配。
- 文档级单元为 `doc_name`；章节级单元为 `section`，命中章节时还要求
  命中片段来自期望文档之一（多文档问题不做文档×章节笛卡尔积）。
- 同一文档或同一章节被多个分块命中时，只计一次，不重复加分。
- 关键词覆盖只做字符串匹配，不调用 LLM：`expected_keywords_all` 全部命中、
  `expected_keywords_any` 至少命中一项时记 1.0，否则 0.0；两个列表都为空时为
  `null`，不参与均值。

## 指标与报告

检索指标由本地纯函数计算：`Hit@K`、`Recall@K`、`MRR`、`NDCG@K`，
分别输出 doc 级与 section 级。生成指标由官方 RAGAS 0.3.0 计算
（Faithfulness、Answer Relevancy、Context Precision、Context Recall），
未安装 overlay 或未开启 LLM 时标记为不可用，而不是记 0。

```bash
# 仅校验数据格式（CI 同款，不需要数据库与 ragas）
uv run python scripts/evaluate_rag.py --validate-dataset

# 检索指标
uv run python scripts/evaluate_rag.py --retrieval-only --limit 10

# 官方 RAGAS 生成指标
uv run --with-requirements scripts/eval_requirements.txt \
  python scripts/evaluate_rag.py --limit 5 --judge-metrics faithfulness,answer_relevancy
```

报告写入 `reports/eval_<timestamp>.json` 与 `.md`，逐题明细与失败项一并保留。
