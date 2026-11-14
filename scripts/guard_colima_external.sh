#!/usr/bin/env bash
# Colima 迁移到外接盘后的开机/启动守卫。
#
# 背景：Colima 的数据目录已符号链接到外接盘 /Volumes/未命名/colima-data。
# 如果外接盘没有挂载（拔线、硬盘故障、开机顺序），Colima 会用一个空目录启动，
# 表现为「所有数据凭空消失」——这是最危险的静默故障，所以启动前必须拦一道。
#
# 用法：
#   bash scripts/guard_colima_external.sh          # 只检查，不启动
#   bash scripts/guard_colima_external.sh --start  # 检查通过后启动 Colima
set -euo pipefail
export PATH=/opt/homebrew/bin:$PATH

PROFILE="${COLIMA_PROFILE:-leziwu}"
EXTERNAL_MOUNT="${COLIMA_EXTERNAL_MOUNT:-/Volumes/未命名}"
DATA_DIR="${COLIMA_DATA_DIR:-$EXTERNAL_MOUNT/colima-data}"
SENTINEL="$DATA_DIR/_lima/_disks/colima-leziwu/datadisk"

fail() {
  echo "✗ $*" >&2
  echo "" >&2
  echo "外接盘未就绪，拒绝启动 Colima——避免用空目录覆盖真实数据。" >&2
  echo "请先确认外接盘已挂载，再重试。" >&2
  exit 1
}

echo "=== 检查外接盘 ==="
[ -d "$EXTERNAL_MOUNT" ] || fail "挂载点不存在: $EXTERNAL_MOUNT"
mount | grep -q "on $EXTERNAL_MOUNT " || fail "外接盘未挂载: $EXTERNAL_MOUNT"
echo "挂载点 OK: $EXTERNAL_MOUNT"
df -h "$EXTERNAL_MOUNT" | tail -1

echo
echo "=== 检查数据目录 ==="
[ -d "$DATA_DIR" ] || fail "数据目录不存在: $DATA_DIR"
[ -f "$SENTINEL" ] || fail "磁盘镜像缺失: $SENTINEL"
echo "数据目录 OK: $DATA_DIR"
echo "磁盘镜像: $(ls -lh "$SENTINEL" | awk '{print $5}')"

echo
echo "=== 检查符号链接 ==="
LINK="$HOME/.colima"
if [ ! -L "$LINK" ]; then
  fail "$LINK 不是符号链接（可能被误还原成内置盘目录）"
fi
TARGET="$(readlink "$LINK")"
[ "$TARGET" = "$DATA_DIR" ] || fail "符号链接指向 $TARGET，预期 $DATA_DIR"
echo "符号链接 OK: $LINK -> $TARGET"

echo
echo "=== 检查剩余空间 ==="
AVAIL_GB=$(df -g "$EXTERNAL_MOUNT" | tail -1 | awk '{print $4}')
echo "可用: ${AVAIL_GB}GB"
if [ "${AVAIL_GB:-0}" -lt 20 ]; then
  echo "⚠ 外接盘剩余不足 20GB，数据库继续增长前请处理" >&2
fi

echo
if [ "${1:-}" = "--start" ]; then
  echo "=== 启动 Colima ==="
  colima start --profile "$PROFILE"
else
  echo "检查全部通过（加 --start 可直接启动 Colima）"
fi
