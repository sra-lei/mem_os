"""评测批次记忆本体导入：memories_exports/run_<id>/*.json → 本地 memories.db（幂等合并）。

跨机记忆同步对侧：把评测方导出的记忆镜像（conv_meta / struct_memories /
conv_messages）合并进本地库，合并语义见 os_mem.admin.MemAdminService.import_memory_batch
（镜像=权威 whole-row LWW：conv_meta 本地 COMPLETED 不覆盖，非完成态被镜像完成态
覆盖 → dev 免重提取；struct/messages 冲突键幂等 upsert）。
写入一律经 os_mem.admin 窗口（对外纪律：不直连业务库）。

用法：
    uv run python tests/import_run_memories.py            # 导入 memories_exports 下最新一个 run
    uv run python tests/import_run_memories.py run_xxx    # 按 run 前缀
    uv run python tests/import_run_memories.py <目录路径>  # 直接指定镜像目录
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_EXPORTS_DIR = ROOT / "memories_exports"


def _find_dir(arg: str | None) -> Path:
    if arg and "/" in arg:  # 路径形式
        p = Path(arg)
        if not p.is_dir():
            raise SystemExit(f"镜像目录不存在: {p}")
        return p
    if not _EXPORTS_DIR.is_dir():
        raise SystemExit("memories_exports/ 不存在（先 pull 拿到镜像，或指定路径）")
    if arg:  # run 前缀（兼容 run_xxx 与裸 xxx）
        arg2 = arg.removeprefix("run_")
        matches = sorted(_EXPORTS_DIR.glob(f"run_{arg2}*"), reverse=True)
        if not matches:
            raise SystemExit(f"未找到 run 前缀 {arg!r} 的记忆镜像")
        return matches[0]
    runs = sorted(_EXPORTS_DIR.glob("run_*"), reverse=True)
    if not runs:
        raise SystemExit("memories_exports/ 下没有 run 镜像")
    return runs[0]


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    run_dir = _find_dir(arg)

    batch: dict[str, list[dict]] = {}
    for name in ("conv_meta", "struct_memories", "conv_messages"):
        f = run_dir / f"{name}.json"
        if f.exists():
            batch[name] = json.loads(f.read_text())

    from os_mem.admin import get_mem_admin_service

    res = get_mem_admin_service().import_memory_batch(batch)
    print(f"[mem-sync] import {run_dir.name}: {res}")


if __name__ == "__main__":
    main()
