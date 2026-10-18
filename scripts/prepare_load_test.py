"""为 Locust 压测准备临时用户、JWT 与公共知识库。

不打印 token，只把 token 写入 ``loadtest/users.json``。
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import secrets
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import delete, select  # noqa: E402

from app.core.logger import get_logger  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.db.session import (  # noqa: E402
    dispose_engine,
    ensure_database,
    ensure_schema_upgrades,
    get_session_maker,
    init_models,
)
from app.models.conversation import ChatMessage, Conversation  # noqa: E402
from app.models.knowledge_base import KnowledgeBase  # noqa: E402
from app.models.knowledge_gap import KnowledgeGap  # noqa: E402
from app.models.user import User  # noqa: E402

logger = get_logger(__name__)

DEFAULT_OUTPUT = PROJECT_ROOT / "loadtest" / "users.json"
DEFAULT_KB_NAME = "服装售前知识库（2026 春夏）"


def _site() -> str:
    frame = inspect.currentframe()
    caller = frame.f_back if frame else None
    if caller is None:
        return Path(__file__).name
    return f"{Path(caller.f_code.co_filename).name}:{caller.f_lineno}"


async def resolve_public_kb(
    kb_id: int | None, kb_name: str | None
) -> KnowledgeBase:
    async with get_session_maker()() as db:
        if kb_id is not None:
            kb = await db.get(KnowledgeBase, int(kb_id))
        else:
            kb = await db.scalar(
                select(KnowledgeBase)
                .where(KnowledgeBase.name == (kb_name or DEFAULT_KB_NAME))
                .order_by(KnowledgeBase.id.asc())
            )
    if kb is None:
        raise SystemExit(
            f"找不到知识库（id={kb_id}, name={kb_name or DEFAULT_KB_NAME}）；"
            "压测不会自动创建空库"
        )
    if not kb.is_public:
        raise SystemExit(
            f"知识库 {kb.name}（id={kb.id}）不是公共知识库，临时压测用户无法访问；"
            "请在知识库设置中开启公开，或换一个公共知识库"
        )
    if kb.document_count <= 0:
        raise SystemExit(f"公共知识库 {kb.name}（id={kb.id}）没有文档，无法压测检索")
    return kb


async def create_users(prefix: str, count: int) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    async with get_session_maker()() as db:
        for index in range(1, count + 1):
            username = f"{prefix}{index:03d}_{secrets.token_hex(3)}"
            user = User(
                username=username,
                password_hash=hash_password(secrets.token_urlsafe(24)),
                nickname=f"压测用户 {index}",
            )
            db.add(user)
            await db.flush()
            token, expires_in = create_access_token(int(user.id), user.username)
            payload.append(
                {"username": username, "token": token, "expires_in": expires_in}
            )
        await db.commit()
    logger.info("已创建压测用户 %d 个（%s）", len(payload), _site())
    return payload


async def cleanup(prefix: str) -> None:
    async with get_session_maker()() as db:
        user_ids = list(
            await db.scalars(select(User.id).where(User.username.like(f"{prefix}%")))
        )
        if user_ids:
            conversation_ids = list(
                await db.scalars(
                    select(Conversation.id).where(Conversation.user_id.in_(user_ids))
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
            await db.execute(
                delete(KnowledgeGap).where(KnowledgeGap.user_id.in_(user_ids))
            )
            await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.commit()
    logger.info(
        "已清理压测用户 %d 个（前缀 %s，%s）", len(user_ids), prefix, _site()
    )


async def run(args: argparse.Namespace) -> int:
    if args.cleanup:
        await asyncio.to_thread(ensure_database)
        await init_models()
        await ensure_schema_upgrades()
        await cleanup(args.prefix)
        output = Path(args.output)
        if not output.is_absolute():
            output = PROJECT_ROOT / output
        output.unlink(missing_ok=True)
        print(f"已清理用户名前缀为 {args.prefix} 的压测数据，并移除 {output}")
        return 0

    await asyncio.to_thread(ensure_database)
    await init_models()
    await ensure_schema_upgrades()
    kb = await resolve_public_kb(args.kb_id, args.kb_name)
    users = await create_users(args.prefix, max(1, int(args.users)))

    output = Path(args.output)
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "kb_id": int(kb.id),
                "kb_name": kb.name,
                "host": args.host,
                "prefix": args.prefix,
                "users": users,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"已准备 {len(users)} 个压测用户，知识库：{kb.name}（id={kb.id}）")
    print(f"用户与 token 已写入：{output}")
    print("token 未打印；压测结束后请执行 --cleanup")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AskToFit Locust 压测准备")
    parser.add_argument("--users", type=int, default=20)
    parser.add_argument("--kb-id", type=int, default=None)
    parser.add_argument("--kb-name", default=DEFAULT_KB_NAME)
    parser.add_argument("--prefix", default="loadtest_")
    parser.add_argument("--host", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--cleanup", action="store_true")
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
