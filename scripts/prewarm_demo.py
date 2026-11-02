"""演示前预热普通 FAQ 与管理员演示问题。

通过真实 HTTP/SSE 链路提问，同时预热检索缓存与回答缓存。每条问题使用
一个全新会话（空历史），与现场首次提问的缓存键保持一致。

用法：
    python scripts/prewarm_demo.py --lane all --admin-username <管理员账号>
    python scripts/prewarm_demo.py --lane all --admin-username <账号> --verify
    python scripts/prewarm_demo.py --cleanup --admin-username <账号>
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from app.core.logger import get_logger, setup_logging  # noqa: E402
from app.core.security import create_access_token, hash_password_async  # noqa: E402
from app.db.session import (  # noqa: E402
    dispose_engine,
    ensure_database,
    ensure_schema_upgrades,
    get_session_maker,
    init_models,
)
from app.models.conversation import ChatMessage, Conversation  # noqa: E402
from app.models.knowledge_base import KnowledgeBase  # noqa: E402
from app.models.user import User  # noqa: E402
from config import Config  # noqa: E402
from scripts.loadtest_sse import parse_sse_events  # noqa: E402

logger = get_logger(__name__)

DEFAULT_NORMAL_FILE = PROJECT_ROOT / "data" / "demo" / "questions.txt"
DEFAULT_ADMIN_FILE = PROJECT_ROOT / "data" / "demo" / "admin_questions.txt"
DEFAULT_NORMAL_USER = "prewarm_demo"
PREWARM_TITLE_PREFIX = "[prewarm] "


@dataclass
class QuestionResult:
    lane: str
    question: str
    ok: bool
    cache_hit: bool
    ttft_ms: float
    total_ms: float
    error: str = ""


def load_questions(path: Path) -> list[str]:
    if not path.exists():
        raise SystemExit(f"问题文件不存在：{path}")
    questions: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line in seen:
            continue
        seen.add(line)
        questions.append(line)
    if not questions:
        raise SystemExit(f"问题文件为空：{path}")
    return questions


async def resolve_kb(kb_id: int | None, kb_name: str | None) -> KnowledgeBase:
    async with get_session_maker()() as db:
        if kb_id is not None:
            kb = await db.get(KnowledgeBase, int(kb_id))
        elif kb_name:
            kb = await db.scalar(
                select(KnowledgeBase)
                .where(KnowledgeBase.name == kb_name)
                .order_by(KnowledgeBase.id.asc())
            )
        else:
            # 未显式指定时，自动选择第一个有文档的公共知识库
            kb = await db.scalar(
                select(KnowledgeBase)
                .where(
                    KnowledgeBase.is_public.is_(True),
                    KnowledgeBase.document_count > 0,
                )
                .order_by(KnowledgeBase.id.asc())
            )
    if kb is None:
        raise SystemExit(f"找不到知识库 id={kb_id} name={kb_name}")
    if not kb.is_public:
        raise SystemExit(f"知识库 {kb.name}（id={kb.id}）不是公共知识库")
    if kb.document_count <= 0:
        raise SystemExit(f"知识库 {kb.name}（id={kb.id}）没有文档")
    return kb


async def ensure_normal_user(username: str) -> User:
    async with get_session_maker()() as db:
        user = await db.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(
                username=username,
                nickname="演示预热",
                password_hash=await hash_password_async(secrets.token_urlsafe(24)),
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            logger.info("已创建预热专用普通用户 username=%s", username)
        return user


async def resolve_admin_user(username: str) -> User:
    async with get_session_maker()() as db:
        user = await db.scalar(select(User).where(User.username == username))
        if user is None:
            raise SystemExit(f"管理员账号不存在：{username}")
        if not user.is_admin:
            raise SystemExit(
                f"账号 {username} 不是管理员；请先执行 scripts/set_admin.py 或配置 ADMIN_USERNAMES"
            )
        return user


def _token_for(user: User) -> dict[str, str]:
    token, _ = create_access_token(int(user.id), user.username)
    return {"Authorization": f"Bearer {token}"}


async def _consume_stream(
    client: httpx.AsyncClient,
    conversation_id: int,
    question: str,
    lane: str,
) -> QuestionResult:
    started = time.perf_counter()
    ttft_ms: float | None = None
    done: dict | None = None
    error = ""
    buffer = ""
    url = f"/api/chat/conversations/{conversation_id}/messages/stream"
    try:
        async with client.stream(
            "POST", url, json={"content": question}
        ) as response:
            if response.status_code != 200:
                body = (await response.aread()).decode("utf-8", errors="replace")
                raise RuntimeError(f"HTTP {response.status_code}: {body[:300]}")
            async for chunk in response.aiter_text():
                events, buffer = parse_sse_events(buffer + chunk)
                for event in events:
                    name = event["event"]
                    data = event["data"] or {}
                    if name == "delta" and ttft_ms is None:
                        ttft_ms = (time.perf_counter() - started) * 1000
                    elif name == "done":
                        done = data
                    elif name == "error":
                        error = str(data.get("message") or "未知错误")
    except Exception as exc:
        error = str(exc)

    total_ms = (time.perf_counter() - started) * 1000
    ok = done is not None and not error
    return QuestionResult(
        lane=lane,
        question=question,
        ok=ok,
        cache_hit=bool(done.get("cache_hit")) if done else False,
        ttft_ms=round(ttft_ms or 0.0, 1),
        total_ms=round(total_ms, 1),
        error=error or ("" if ok else "流结束但未收到 done 事件"),
    )


async def create_conversation(
    client: httpx.AsyncClient, kb_id: int, question: str
) -> int:
    response = await client.post(
        "/api/chat/conversations",
        json={
            "title": f"{PREWARM_TITLE_PREFIX}{question[:40]}",
            "kb_ids": [kb_id],
            "model_name": Config.model,
            "temperature": Config.temperature_default,
            "top_p": Config.top_p_default,
            "max_tokens": Config.max_tokens_default,
            "history_rounds": Config.history_rounds_default,
        },
    )
    if response.status_code != 201:
        raise RuntimeError(
            f"创建会话失败 HTTP {response.status_code}: {response.text[:300]}"
        )
    return int(response.json()["id"])


async def run_lane(
    client: httpx.AsyncClient,
    *,
    lane: str,
    user: User,
    kb: KnowledgeBase,
    questions: list[str],
    verify: bool,
) -> list[QuestionResult]:
    headers = _token_for(user)
    client.headers.update(headers)
    results: list[QuestionResult] = []
    for index, question in enumerate(questions, start=1):
        conversation_id = 0
        try:
            conversation_id = await create_conversation(client, int(kb.id), question)
            result = await _consume_stream(client, conversation_id, question, lane)
        except Exception as exc:
            result = QuestionResult(
                lane=lane,
                question=question,
                ok=False,
                cache_hit=False,
                ttft_ms=0.0,
                total_ms=0.0,
                error=str(exc),
            )
        results.append(result)
        marker = "HIT" if result.cache_hit else "MISS"
        logger.info(
            "[%s %d/%d] %s %s ttft=%.0fms total=%.0fms %s",
            lane,
            index,
            len(questions),
            marker,
            "OK" if result.ok else "FAIL",
            result.ttft_ms,
            result.total_ms,
            result.question[:60],
        )
        if result.error:
            logger.warning("[%s] 失败：%s", lane, result.error)

    if verify:
        misses = [item for item in results if item.ok and not item.cache_hit]
        failures = [item for item in results if not item.ok]
        if misses or failures:
            logger.error(
                "[%s] 预热校验未通过：miss=%d fail=%d",
                lane,
                len(misses),
                len(failures),
            )
        else:
            logger.info("[%s] 预热校验通过：%d 条全部命中缓存", lane, len(results))
    return results


async def cleanup(admin_username: str | None) -> None:
    usernames = [DEFAULT_NORMAL_USER]
    if admin_username:
        usernames.append(admin_username)
    async with get_session_maker()() as db:
        user_ids = list(
            await db.scalars(select(User.id).where(User.username.in_(usernames)))
        )
        if not user_ids:
            logger.info("没有需要清理的预热账号")
            return
        conversation_ids = list(
            await db.scalars(
                select(Conversation.id).where(
                    Conversation.user_id.in_(user_ids),
                    Conversation.title.like(f"{PREWARM_TITLE_PREFIX}%"),
                )
            )
        )
        if conversation_ids:
            await db.execute(
                delete(ChatMessage).where(
                    ChatMessage.conversation_id.in_(conversation_ids)
                )
            )
            await db.execute(
                delete(Conversation).where(Conversation.id.in_(conversation_ids))
            )
        await db.commit()
    logger.info("已清理预热会话 %d 个（缓存保留）", len(conversation_ids))


async def run(args: argparse.Namespace) -> int:
    setup_logging()
    await asyncio.to_thread(ensure_database)
    await init_models()
    await ensure_schema_upgrades()

    if args.cleanup:
        await cleanup(args.admin_username)
        return 0

    lanes = ["normal", "admin"] if args.lane == "all" else [args.lane]
    kb = await resolve_kb(args.kb_id, args.kb_name)
    timeout = httpx.Timeout(args.timeout, connect=10.0)
    all_results: list[QuestionResult] = []

    async with httpx.AsyncClient(base_url=args.base_url, timeout=timeout) as client:
        if "normal" in lanes:
            normal_user = await ensure_normal_user(args.normal_username)
            questions = load_questions(Path(args.normal_questions))
            all_results.extend(
                await run_lane(
                    client,
                    lane="normal",
                    user=normal_user,
                    kb=kb,
                    questions=questions,
                    verify=args.verify,
                )
            )
        if "admin" in lanes:
            if not args.admin_username:
                raise SystemExit("admin 通道必须提供 --admin-username")
            admin_user = await resolve_admin_user(args.admin_username)
            questions = load_questions(Path(args.admin_questions))
            all_results.extend(
                await run_lane(
                    client,
                    lane="admin",
                    user=admin_user,
                    kb=kb,
                    questions=questions,
                    verify=args.verify,
                )
            )

    ok_count = sum(1 for item in all_results if item.ok)
    hit_count = sum(1 for item in all_results if item.cache_hit)
    miss_count = sum(1 for item in all_results if item.ok and not item.cache_hit)
    fail_count = len(all_results) - ok_count
    print(
        f"预热完成：{len(all_results)} 条，成功 {ok_count}，缓存命中 {hit_count}，"
        f"未命中 {miss_count}，失败 {fail_count}"
    )
    for item in all_results:
        if not item.ok:
            print(f"  FAIL [{item.lane}] {item.question} -> {item.error}")
    if args.verify and (fail_count or miss_count):
        return 1
    return 0 if fail_count == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AskToFit 演示预热")
    parser.add_argument("--lane", choices=["normal", "admin", "all"], default="all")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--kb-id", type=int, default=None)
    parser.add_argument(
        "--kb-name",
        default=None,
        help="公共知识库名称；与 --kb-id 都不传时自动选择第一个有文档的公共库",
    )
    parser.add_argument("--normal-user", dest="normal_username", default=DEFAULT_NORMAL_USER)
    parser.add_argument("--admin-username", default=None)
    parser.add_argument("--normal-questions", default=str(DEFAULT_NORMAL_FILE))
    parser.add_argument("--admin-questions", default=str(DEFAULT_ADMIN_FILE))
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="重放并断言回答缓存命中；未命中或失败返回非零",
    )
    parser.add_argument("--cleanup", action="store_true", help="删除预热会话，保留缓存")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    async def runner() -> int:
        try:
            return await run(args)
        finally:
            await dispose_engine()

    return asyncio.run(runner())


if __name__ == "__main__":
    raise SystemExit(main())
