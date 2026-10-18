"""检查 MySQL / Redis / Milvus 连通性。

用法：uv run python scripts/check_services.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymysql  # noqa: E402
import redis.asyncio as aioredis  # noqa: E402
from pymilvus import MilvusClient  # noqa: E402

from config import Config  # noqa: E402


def check_mysql() -> bool:
    try:
        conn = pymysql.connect(
            host=Config.host,
            port=Config.port,
            user=Config.user,
            password=Config.password,
            database=Config.database,
            connect_timeout=5,
        )
        conn.close()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[MySQL] 失败: {exc}")
        return False


async def check_redis() -> bool:
    client = aioredis.Redis(
        host=Config.redis_host,
        port=Config.redis_port,
        password=Config.redis_password,
        db=Config.db,
        socket_connect_timeout=5,
    )
    try:
        return bool(await client.ping())
    except Exception as exc:  # noqa: BLE001
        print(f"[Redis] 失败: {exc}")
        return False
    finally:
        await client.aclose()


def check_milvus() -> bool:
    try:
        client = MilvusClient(uri=f"http://{Config.milvus_host}:{Config.milvus_port}")
        client.list_databases()
        client.close()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[Milvus] 失败: {exc}")
        return False


async def main() -> None:
    results = {
        "MySQL": check_mysql(),
        "Redis": await check_redis(),
        "Milvus": check_milvus(),
    }
    for name, ok in results.items():
        print(f"[{name}] {'OK' if ok else 'FAILED'}")
    if not all(results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
