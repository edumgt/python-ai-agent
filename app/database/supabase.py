"""Supabase 기반 데이터 저장 및 CRUD 처리.

Supabase는 PostgreSQL + REST API(PostgREST) 위에 구축된 BaaS입니다.
MongoDB(NoSQL)와 달리 스키마 정의가 필요하며, 관계형 조인·RLS(행 수준 보안)를
네이티브로 지원합니다.

사용 전 Supabase Dashboard에서 아래 테이블을 생성하거나
SQL Editor에서 docs/supabase_schema.sql을 실행하세요.

설정 필요 환경변수:
  SUPABASE_URL         – 프로젝트 URL (https://xxx.supabase.co)
  SUPABASE_SERVICE_KEY – service_role 키 (관리자 전용, RLS 우회)
  SUPABASE_ANON_KEY    – anon 키 (클라이언트 사이드용)
"""
import asyncio
from functools import partial
from typing import Any, Optional

from app.config import settings

try:
    from supabase import create_client, Client as SyncClient
    _SUPABASE_AVAILABLE = True
except ImportError:
    _SUPABASE_AVAILABLE = False

_client: Optional[Any] = None  # SyncClient


def _get_client() -> Any:
    global _client
    if not _SUPABASE_AVAILABLE:
        raise RuntimeError(
            "supabase 패키지가 설치되지 않았습니다. "
            "`pip install supabase` 후 재시도하세요."
        )
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
        raise RuntimeError(
            "Supabase 설정이 없습니다. "
            "SUPABASE_URL 과 SUPABASE_SERVICE_KEY 환경변수를 설정하세요."
        )
    if _client is None:
        _client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    return _client


async def _run_sync(fn, *args, **kwargs):
    """동기 supabase-py 호출을 비동기 컨텍스트에서 실행합니다."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(fn, *args, **kwargs))


# ── 범용 CRUD 베이스 ──────────────────────────────────────────────────────────

class CRUDBase:
    """테이블 이름을 주입받아 표준 CRUD 메서드를 제공합니다.

    사용 예::

        portfolio_crud = CRUDBase("portfolios")
        await portfolio_crud.create({"user_id": uid, "symbol": "005930.KS", "qty": 10})
        items = await portfolio_crud.list(filters={"user_id": uid})
    """

    def __init__(self, table: str):
        self.table = table

    def _tbl(self):
        return _get_client().table(self.table)

    # ── Create ────────────────────────────────────────────────────────────────

    async def create(self, data: dict) -> dict:
        """단일 행 삽입 후 삽입된 행을 반환합니다."""
        def _insert():
            return self._tbl().insert(data).execute()
        res = await _run_sync(_insert)
        return res.data[0] if res.data else {}

    async def create_many(self, rows: list[dict]) -> list[dict]:
        """여러 행을 일괄 삽입합니다."""
        def _insert():
            return self._tbl().insert(rows).execute()
        res = await _run_sync(_insert)
        return res.data or []

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get(self, id: Any, id_col: str = "id") -> Optional[dict]:
        """기본 키로 단일 행을 조회합니다."""
        def _select():
            return self._tbl().select("*").eq(id_col, id).limit(1).execute()
        res = await _run_sync(_select)
        return res.data[0] if res.data else None

    async def list(
        self,
        filters: Optional[dict] = None,
        order_by: Optional[str] = None,
        ascending: bool = False,
        limit: int = 50,
        offset: int = 0,
        select: str = "*",
    ) -> list[dict]:
        """필터·정렬·페이지네이션을 적용해 여러 행을 반환합니다."""
        def _select():
            q = self._tbl().select(select).range(offset, offset + limit - 1)
            if filters:
                for col, val in filters.items():
                    if isinstance(val, (list, tuple)):
                        q = q.in_(col, list(val))
                    else:
                        q = q.eq(col, val)
            if order_by:
                q = q.order(order_by, desc=not ascending)
            return q.execute()
        res = await _run_sync(_select)
        return res.data or []

    async def count(self, filters: Optional[dict] = None) -> int:
        """조건에 맞는 행 수를 반환합니다."""
        def _count():
            q = self._tbl().select("*", count="exact").limit(0)
            if filters:
                for col, val in filters.items():
                    q = q.eq(col, val)
            return q.execute()
        res = await _run_sync(_count)
        return res.count or 0

    # ── Update ────────────────────────────────────────────────────────────────

    async def update(self, id: Any, data: dict, id_col: str = "id") -> dict:
        """단일 행을 부분 업데이트 후 변경된 행을 반환합니다."""
        def _update():
            return self._tbl().update(data).eq(id_col, id).execute()
        res = await _run_sync(_update)
        return res.data[0] if res.data else {}

    async def update_where(self, filters: dict, data: dict) -> list[dict]:
        """조건에 맞는 모든 행을 업데이트합니다."""
        def _update():
            q = self._tbl().update(data)
            for col, val in filters.items():
                q = q.eq(col, val)
            return q.execute()
        res = await _run_sync(_update)
        return res.data or []

    # ── Delete ────────────────────────────────────────────────────────────────

    async def delete(self, id: Any, id_col: str = "id") -> bool:
        """단일 행을 삭제합니다."""
        def _delete():
            return self._tbl().delete().eq(id_col, id).execute()
        await _run_sync(_delete)
        return True

    async def delete_where(self, filters: dict) -> int:
        """조건에 맞는 모든 행을 삭제 후 삭제 수를 반환합니다."""
        def _delete():
            q = self._tbl().delete()
            for col, val in filters.items():
                q = q.eq(col, val)
            return q.execute()
        res = await _run_sync(_delete)
        return len(res.data) if res.data else 0

    # ── Upsert ────────────────────────────────────────────────────────────────

    async def upsert(self, data: dict, on_conflict: str = "id") -> dict:
        """INSERT OR UPDATE – conflict 컬럼 기준으로 행을 삽입하거나 갱신합니다."""
        def _upsert():
            return self._tbl().upsert(data, on_conflict=on_conflict).execute()
        res = await _run_sync(_upsert)
        return res.data[0] if res.data else {}

    async def upsert_many(self, rows: list[dict], on_conflict: str = "id") -> list[dict]:
        def _upsert():
            return self._tbl().upsert(rows, on_conflict=on_conflict).execute()
        res = await _run_sync(_upsert)
        return res.data or []


# ── 테이블별 CRUD 인스턴스 ─────────────────────────────────────────────────────
#
#  Supabase Dashboard 또는 docs/supabase_schema.sql 로 생성 필요:
#
#  conversations  – 대화 스레드 (id, user_id, title, created_at, updated_at)
#  messages       – 메시지 (id, conversation_id, role, content, citations, created_at)
#  portfolios     – 포트폴리오 (id, user_id, symbol, qty, avg_price, updated_at)
#  watchlist      – 관심 종목 (id, user_id, symbol, added_at)
#
conversation_crud = CRUDBase("conversations")
message_crud = CRUDBase("messages")
portfolio_crud = CRUDBase("portfolios")
watchlist_crud = CRUDBase("watchlist")
