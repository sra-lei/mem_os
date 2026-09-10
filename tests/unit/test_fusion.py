"""fuse_dense_lead 融合规则单测（纯逻辑，不连 Milvus）。"""
from __future__ import annotations

from os_mem.infra.storage.vec_storage import fuse_dense_lead


def _h(ids: list[str]) -> list[dict]:
    return [{"id": i, "fact": i} for i in ids]


def _ids(hits: list[dict]) -> list[str]:
    return [h["id"] for h in hits]


def test_dense_skeleton_order_preserved():
    # top_k=3、1 个补盲 → dense 前 2 保住，补盲 s1 追加尾部
    dense = _h(["d1", "d2", "d3"])
    out = fuse_dense_lead(dense, _h(["s1"]), top_k=3)
    assert _ids(out) == ["d1", "d2", "s1"]


def test_sparse_only_fills_true_blind_spot():
    # dense 没有 s1（真盲区），sparse 高名次 → 补入尾部
    dense = _h([f"d{i}" for i in range(15)])
    sparse = _h(["s1", "s2"])
    out = fuse_dense_lead(dense, sparse, top_k=15)
    ids = _ids(out)
    assert len(ids) == 15
    assert "s1" in ids and "s2" in ids
    # 补盲在 dense 之后；dense 前 13 保留（15 - ceil(15/3)=10 槽? 校验下方配额）
    assert ids[:13] == [f"d{i}" for i in range(13)]


def test_blind_quota_capped_at_one_third():
    dense = _h([f"d{i}" for i in range(30)])
    sparse = _h([f"s{i}" for i in range(10)])  # 全是 dense 盲区
    out = fuse_dense_lead(dense, sparse, top_k=15)
    blind = [i for i in _ids(out) if i.startswith("s")]
    # ceil(15/3) = 5
    assert blind == ["s0", "s1", "s2", "s3", "s4"]
    assert len(out) == 15


def test_sparse_cannot_demote_dense_hit():
    # 真实尺度（service 传 fetch_k=45）：target dense#3，sparse 即便带盲区噪音，
    # 补盲只占尾部 1/3（≤15），dense 前 30 骨架位置不变——#3 稳居 #3。
    dense = _h([f"d{i}" for i in range(45)])
    dense[2] = {"id": "target", "fact": "target"}
    sparse = _h([f"s{i}" for i in range(15)] + ["target"])
    out = fuse_dense_lead(dense, sparse, top_k=45)
    ids = _ids(out)
    assert ids[2] == "target"  # dense 原位不被 sparse 投票挪动
    blind = [i for i in ids if i.startswith("s")]
    assert blind == [f"s{i}" for i in range(15)]  # 盲区补尾部、配额内
    assert len(out) == 45


def test_empty_sparse_returns_dense_topk():
    dense = _h([f"d{i}" for i in range(20)])
    out = fuse_dense_lead(dense, [], top_k=15)
    assert _ids(out) == [f"d{i}" for i in range(15)]


def test_dense_hit_excluded_from_blind_set():
    # sparse 列里的 id 若 dense 已有，不算盲区、不重复补入
    dense = _h(["x", "y"])
    sparse = _h(["x", "z"])
    out = fuse_dense_lead(dense, sparse, top_k=3)
    ids = _ids(out)
    assert ids.count("x") == 1
    assert "z" in ids
