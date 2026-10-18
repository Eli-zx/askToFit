"""基于本地 BERT 底座微调售前意图分类模型。

用法：
    uv run python scripts/train_intent_bert.py

数据：data/intent/train.jsonl（600 条）、data/intent/test.jsonl（150 条）
输出：models/intent-bert/（模型、分词器、标签映射、评估指标）
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from torch import nn
from torch.utils.data import DataLoader, TensorDataset
from transformers import AutoModelForSequenceClassification, AutoTokenizer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.logger import get_logger  # noqa: E402
from config import BASE_DIR  # noqa: E402

logger = get_logger(__name__)

# 标签顺序固定，训练/推理/数据库统计共用
INTENT_LABELS: tuple[str, ...] = (
    "return_exchange",
    "invoice",
    "logistics",
    "payment",
    "refund",
    "sizing",
    "membership",
    "product",
    "greeting",
    "other",
)
LABEL_TO_ID = {label: index for index, label in enumerate(INTENT_LABELS)}


def load_rows(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            label = item.get("label")
            text = (item.get("text") or "").strip()
            if label not in LABEL_TO_ID:
                raise ValueError(f"{path}:{line_number} 标签非法：{label!r}")
            if not text:
                raise ValueError(f"{path}:{line_number} 文本为空")
            rows.append({"text": text, "label": label})
    if not rows:
        raise ValueError(f"{path} 没有可用样本")
    return rows


def encode(
    tokenizer,
    texts: list[str],
    labels: list[int],
    max_length: int,
) -> TensorDataset:
    encoded = tokenizer(
        texts,
        truncation=True,
        padding="max_length",
        max_length=max_length,
        return_tensors="pt",
    )
    return TensorDataset(
        encoded["input_ids"],
        encoded["attention_mask"],
        encoded.get("token_type_ids", torch.zeros_like(encoded["input_ids"])),
        torch.tensor(labels, dtype=torch.long),
    )


def stratified_split(
    rows: list[dict[str, str]], val_ratio: float, seed: int
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """按类别分层切出验证集，验证集不参与参数更新。"""
    rng = random.Random(seed)
    buckets: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        buckets.setdefault(row["label"], []).append(row)

    train_rows: list[dict[str, str]] = []
    val_rows: list[dict[str, str]] = []
    for label in INTENT_LABELS:
        items = buckets.get(label, [])
        rng.shuffle(items)
        val_size = max(1, int(round(len(items) * val_ratio)))
        val_rows.extend(items[:val_size])
        train_rows.extend(items[val_size:])
    rng.shuffle(train_rows)
    rng.shuffle(val_rows)
    return train_rows, val_rows


@torch.no_grad()
def evaluate(
    model: nn.Module, loader: DataLoader
) -> tuple[list[int], list[int], float]:
    model.eval()
    predictions: list[int] = []
    targets: list[int] = []
    total_loss = 0.0
    batches = 0
    criterion = nn.CrossEntropyLoss()
    for input_ids, attention_mask, token_type_ids, labels in loader:
        outputs = model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
        )
        logits = outputs.logits
        total_loss += float(criterion(logits, labels).item())
        batches += 1
        predictions.extend(logits.argmax(dim=-1).tolist())
        targets.extend(labels.tolist())
    return predictions, targets, total_loss / max(1, batches)


def metrics_for(targets: list[int], predictions: list[int]) -> dict[str, float]:
    return {
        "accuracy": float(accuracy_score(targets, predictions)),
        "macro_f1": float(
            f1_score(targets, predictions, average="macro", zero_division=0)
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="训练 AskToFit 意图分类 BERT")
    parser.add_argument(
        "--base",
        default=str(BASE_DIR / "models" / "bert-base-chinese"),
        help="本地 BERT 底座目录",
    )
    parser.add_argument(
        "--output", default=str(BASE_DIR / "models" / "intent-bert"), help="输出目录"
    )
    parser.add_argument(
        "--train-file", default=str(BASE_DIR / "data" / "intent" / "train.jsonl")
    )
    parser.add_argument(
        "--test-file", default=str(BASE_DIR / "data" / "intent" / "test.jsonl")
    )
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=3e-5)
    parser.add_argument("--max-length", type=int, default=64)
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--freeze-embeddings",
        action="store_true",
        help="冻结词嵌入层，进一步降低 CPU 训练成本",
    )
    args = parser.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(max(1, os.cpu_count() or 4))

    base_dir = Path(args.base)
    output_dir = Path(args.output)
    if not base_dir.exists():
        raise FileNotFoundError(
            f"BERT 底座不存在：{base_dir}（scripts/train_intent_bert.py:main）"
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    train_all = load_rows(Path(args.train_file))
    test_rows = load_rows(Path(args.test_file))
    train_rows, val_rows = stratified_split(train_all, args.val_ratio, args.seed)
    logger.info(
        "数据加载完成 train=%d val=%d test=%d labels=%d",
        len(train_rows),
        len(val_rows),
        len(test_rows),
        len(INTENT_LABELS),
    )

    tokenizer = AutoTokenizer.from_pretrained(str(base_dir))
    model = AutoModelForSequenceClassification.from_pretrained(
        str(base_dir),
        num_labels=len(INTENT_LABELS),
        id2label={index: label for index, label in enumerate(INTENT_LABELS)},
        label2id=LABEL_TO_ID,
        ignore_mismatched_sizes=True,
    )
    if args.freeze_embeddings:
        for parameter in model.bert.embeddings.parameters():
            parameter.requires_grad = False
        logger.info("已冻结词嵌入层")

    train_dataset = encode(
        tokenizer,
        [row["text"] for row in train_rows],
        [LABEL_TO_ID[row["label"]] for row in train_rows],
        args.max_length,
    )
    val_dataset = encode(
        tokenizer,
        [row["text"] for row in val_rows],
        [LABEL_TO_ID[row["label"]] for row in val_rows],
        args.max_length,
    )
    test_dataset = encode(
        tokenizer,
        [row["text"] for row in test_rows],
        [LABEL_TO_ID[row["label"]] for row in test_rows],
        args.max_length,
    )
    generator = torch.Generator().manual_seed(args.seed)
    train_loader = DataLoader(
        train_dataset, batch_size=args.batch_size, shuffle=True, generator=generator
    )
    val_loader = DataLoader(val_dataset, batch_size=args.batch_size)
    test_loader = DataLoader(test_dataset, batch_size=args.batch_size)

    optimizer = torch.optim.AdamW(
        [parameter for parameter in model.parameters() if parameter.requires_grad],
        lr=args.lr,
        weight_decay=0.01,
    )
    total_steps = max(1, len(train_loader) * args.epochs)
    warmup_steps = max(1, int(total_steps * 0.1))

    def lr_lambda(step: int) -> float:
        if step < warmup_steps:
            return step / warmup_steps
        return max(0.0, (total_steps - step) / max(1, total_steps - warmup_steps))

    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)
    criterion = nn.CrossEntropyLoss()

    best_f1 = -1.0
    best_epoch = 0
    history: list[dict[str, float]] = []
    started = time.perf_counter()

    for epoch in range(1, args.epochs + 1):
        model.train()
        epoch_started = time.perf_counter()
        running_loss = 0.0
        for input_ids, attention_mask, token_type_ids, labels in train_loader:
            optimizer.zero_grad(set_to_none=True)
            outputs = model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                token_type_ids=token_type_ids,
            )
            loss = criterion(outputs.logits, labels)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            scheduler.step()
            running_loss += float(loss.item())

        val_predictions, val_targets, val_loss = evaluate(model, val_loader)
        val_metrics = metrics_for(val_targets, val_predictions)
        train_loss = running_loss / max(1, len(train_loader))
        elapsed = time.perf_counter() - epoch_started
        logger.info(
            "epoch=%d/%d train_loss=%.4f val_loss=%.4f val_acc=%.4f val_macro_f1=%.4f %.1fs",
            epoch,
            args.epochs,
            train_loss,
            val_loss,
            val_metrics["accuracy"],
            val_metrics["macro_f1"],
            elapsed,
        )
        history.append(
            {
                "epoch": epoch,
                "train_loss": train_loss,
                "val_loss": val_loss,
                **val_metrics,
            }
        )

        if val_metrics["macro_f1"] > best_f1:
            best_f1 = val_metrics["macro_f1"]
            best_epoch = epoch
            model.save_pretrained(str(output_dir))
            tokenizer.save_pretrained(str(output_dir))
            logger.info("保存当前最佳模型 epoch=%d macro_f1=%.4f", epoch, best_f1)

    logger.info(
        "训练完成 best_epoch=%d best_val_macro_f1=%.4f total=%.1fs",
        best_epoch,
        best_f1,
        time.perf_counter() - started,
    )

    # 用最佳权重做最终测试集评估
    best_model = AutoModelForSequenceClassification.from_pretrained(str(output_dir))
    test_predictions, test_targets, test_loss = evaluate(best_model, test_loader)
    test_metrics = metrics_for(test_targets, test_predictions)
    label_names = [INTENT_LABELS[index] for index in range(len(INTENT_LABELS))]
    report = classification_report(
        test_targets,
        test_predictions,
        labels=list(range(len(INTENT_LABELS))),
        target_names=label_names,
        output_dict=True,
        zero_division=0,
    )
    matrix = confusion_matrix(
        test_targets, test_predictions, labels=list(range(len(INTENT_LABELS)))
    ).tolist()

    summary = {
        "accuracy": test_metrics["accuracy"],
        "macro_f1": test_metrics["macro_f1"],
        "test_loss": test_loss,
        "best_epoch": best_epoch,
        "best_val_macro_f1": best_f1,
        "train_samples": len(train_rows),
        "val_samples": len(val_rows),
        "test_samples": len(test_rows),
        "labels": list(INTENT_LABELS),
        "classification_report": report,
        "confusion_matrix": matrix,
        "history": history,
        "base_model": str(base_dir),
        "max_length": args.max_length,
    }
    (output_dir / "metrics.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "labels.json").write_text(
        json.dumps(
            {
                "labels": list(INTENT_LABELS),
                "label2id": LABEL_TO_ID,
                "id2label": {str(i): label for i, label in enumerate(INTENT_LABELS)},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    logger.info(
        "测试集结果 accuracy=%.4f macro_f1=%.4f test_loss=%.4f",
        test_metrics["accuracy"],
        test_metrics["macro_f1"],
        test_loss,
    )
    print(
        json.dumps(
            {
                "accuracy": test_metrics["accuracy"],
                "macro_f1": test_metrics["macro_f1"],
                "best_epoch": best_epoch,
                "output": str(output_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
