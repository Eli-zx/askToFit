"""手工授予/撤销管理员权限。

用法：
    uv run python scripts/set_admin.py --username <name>
    uv run python scripts/set_admin.py --username <name> --revoke
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db.session import (  # noqa: E402
    dispose_engine,
    ensure_database,
    ensure_schema_upgrades,
    get_session_maker,
    init_models,
)
from app.models.user import User  # noqa: E402


async def _run(username: str, revoke: bool) -> int:
    await asyncio.to_thread(ensure_database)
    await init_models()
    await ensure_schema_upgrades()

    name = username.strip().lower()
    async with get_session_maker()() as db:
        user = await db.scalar(select(User).where(User.username == name))
        if user is None:
            print(f"用户不存在：{name}")
            return 1
        user.is_admin = not revoke
        await db.commit()

    print(("已撤销管理员：" if revoke else "已授予管理员：") + name)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="设置 AskToFit 管理员账号")
    parser.add_argument("--username", required=True, help="账号名（不区分大小写）")
    parser.add_argument("--revoke", action="store_true", help="撤销管理员权限")
    args = parser.parse_args()

    async def runner() -> int:
        try:
            return await _run(args.username, args.revoke)
        finally:
            await dispose_engine()

    return asyncio.run(runner())


if __name__ == "__main__":
    raise SystemExit(main())
