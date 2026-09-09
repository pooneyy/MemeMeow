"""SearchRepository 的单一来源、错误码和向量排序回归测试。"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from backend.database import DatabaseError, EMBEDDING_DIMENSIONS, SearchRepository


def _vector(value: float, *, index: int = 0) -> list[float]:
    """构造固定维度的稀疏测试向量，避免依赖真实 pgvector。"""
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[index] = value
    return vector


def test_query_uses_only_incremental_source(monkeypatch) -> None:
    """query 只允许当前增量来源，旧 generation 不再作为运行时回退。"""
    repository = object.__new__(SearchRepository)
    vector = _vector(1.0)
    calls: list[str] = []

    monkeypatch.setattr(repository, "source_mode", lambda _model: "incremental")
    monkeypatch.setattr(repository, "query_incremental", lambda *_args: calls.append("incremental") or [(UUID(int=1), 1.0)])

    assert repository.query("model", vector) == [(UUID(int=1), 1.0)]
    assert calls == ["incremental"]

    calls.clear()
    monkeypatch.setattr(repository, "source_mode", lambda _model: "not_ready")

    with pytest.raises(DatabaseError, match="cache_not_ready"):
        repository.query("model", vector)
    assert calls == []


def test_incremental_query_does_not_fallback_to_in_memory_vectors() -> None:
    """没有数据库 Session 时，增量查询明确不可用而不加载 Python 向量。"""
    repository = object.__new__(SearchRepository)
    with pytest.raises(DatabaseError, match="cache_not_ready"):
        repository.query_incremental("model", _vector(1.0), 5)


def test_valid_text_embedding_ids_does_not_fallback_to_in_memory_vectors() -> None:
    """没有数据库 Session 时，状态批量查询不回退为 Python 向量读取。"""
    repository = object.__new__(SearchRepository)
    ready_id = UUID("00000000-0000-0000-0000-000000000001")
    pending_id = UUID("00000000-0000-0000-0000-000000000002")
    ready_meme = SimpleNamespace(id=ready_id)
    pending_meme = SimpleNamespace(id=pending_id)
    assert repository.valid_text_embedding_ids("model", [ready_meme, pending_meme]) == set()


def test_valid_text_embedding_ids_uses_row_facts_when_search_source_is_not_ready(monkeypatch) -> None:
    """列表状态按单张图片的有效向量判断，不受搜索来源发布状态影响。"""
    repository = object.__new__(SearchRepository)
    ready_id = UUID("00000000-0000-0000-0000-000000000001")
    repository.scope = SimpleNamespace(scope_id="test-scope")
    repository.session = SimpleNamespace(execute=lambda _statement: [(ready_id,)])

    def unexpected_source_mode(_model: str) -> str:
        """确保状态查询不会复用搜索来源门禁。"""
        raise AssertionError("列表状态不应读取搜索来源状态")

    monkeypatch.setattr(repository, "source_mode", unexpected_source_mode)

    assert repository.valid_text_embedding_ids("model", [SimpleNamespace(id=ready_id)]) == {ready_id}


def test_query_rejects_invalid_dimensions_and_zero_norm() -> None:
    """输入维度和范数错误必须继续 fail-closed。"""
    repository = object.__new__(SearchRepository)
    with pytest.raises(DatabaseError, match="embedding_dimensions_mismatch"):
        repository.query("model", [1.0])

    repository.source_mode = lambda _model: "not_ready"
    with pytest.raises(DatabaseError, match="embedding_zero_norm"):
        repository.query("model", _vector(0.0))
