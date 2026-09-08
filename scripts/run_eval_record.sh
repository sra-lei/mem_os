#!/usr/bin/env bash
# 评测一条龙（跨机记录+记忆同步入口）：pytest(--record-db) → export run → export 记忆镜像 → git commit → push
# 用法: bash scripts/run_eval_record.sh -m layer1 --memory-provider struct --top-k 15
# 说明: 裸跑 pytest 不会自动同步；本 wrapper 保证"跑完即导出入库镜像（评测记录 + 该批记忆本体）"。
#       退出码 = pytest 退出码；push 失败只警告不阻塞（本地 commit 已留）。
# 详见 docs/方案-评测记录跨机同步.md
set -u
cd "$(dirname "$0")/.."

uv run pytest "$@" --record-db
rc=$?

file=$(uv run python tests/export_run.py 2>/dev/null | tail -1)
if [ -z "$file" ] || [ ! -f "$file" ]; then
    echo "[sync] ⚠ 未找到新 run（export 无输出）——本轮可能没落库，跳过 git"
else
    echo "[sync] 已导出: $file"
    if git rev-parse --git-dir >/dev/null 2>&1; then
        # 记忆本体镜像（该 run 涉及用户的 conv_meta/struct_memories；失败不阻塞 run 记录）
        rid=$(basename "$file" .json)
        memdir=$(uv run python scripts/export_run_memories.py --run "$rid" 2>&1 | tail -1)
        if [ -n "$memdir" ] && [ -d "$memdir" ]; then
            echo "[sync] 记忆镜像: $memdir"
        else
            echo "[sync] ⚠ 记忆镜像导出失败（不影响 run 记录）：$(echo "$memdir" | head -1)"
        fi
        git add evals/runs/ memories_exports/ >/dev/null 2>&1
        if ! git diff --cached --quiet; then
            git commit -q -m "chore(eval): record run $rid (+memories)"
            echo "[sync] 已 commit: $rid"
            if git push origin HEAD >/dev/null 2>&1; then
                echo "[sync] 已推送 origin"
            else
                echo "[sync] ⚠ push 失败：本地已 commit，稍后 git push 或下次 wrapper 补推"
            fi
        else
            echo "[sync] 无新 run 文件（最新 run 已导出过）"
        fi
    fi
fi
exit $rc
