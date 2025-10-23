#!/usr/bin/env bash
set -euo pipefail
# Usage:
#   sudo ./additional_disk.sh <serial> [fs_type|KEEPFS] [mount_point]
# Notes:
#   - KEEPFS означает «не форматировать».
#   - Если ФС отсутствует и KEEPFS, и нет mount_point — ничего не делаем.

SERIAL="${1:-}"
FSTYPE_RAW="${2:-KEEPFS}"
MNT="${3:-}"

log() { echo "[INFO] $*"; }
die(){ echo "[ERROR] $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run as root."
[[ -n "$SERIAL" ]] || die "Usage: $0 <serial> [fs_type|KEEPFS] [mount_point]"

norm_fstype() {
  case "$1" in
    KEEPFS) echo "KEEPFS" ;;
    ext2|ext3|ext4|xfs|btrfs|exfat|vfat|f2fs) echo "$1" ;;
    ntfs|ntfs-3g) echo "ntfs-3g" ;;
    *) echo "$1" ;;
  esac
}
FSTYPE="$(norm_fstype "$FSTYPE_RAW")"

BYID="/dev/disk/by-id/virtio-${SERIAL}"
for i in {1..60}; do [[ -e "$BYID" ]] && break; sleep 1; done
[[ -e "$BYID" ]] || die "Device $BYID not found."

DEV="$(readlink -f "$BYID")"
[[ -b "$DEV" ]] || die "Resolved $DEV is not a block device."

ROOT_SRC="$(findmnt -no SOURCE / || true)"
if [[ "$ROOT_SRC" == "$DEV" || "$ROOT_SRC" == "${DEV}1" || "$ROOT_SRC" == "${DEV}p1" ]]; then
  die "Refuse to touch root disk ($ROOT_SRC)."
fi

PART1="${DEV}1"
[[ "$DEV" == *"nvme"* || "$DEV" == *"mmcblk"* ]] && PART1="${DEV}p1"

CUR_ON_DEV="$(blkid -s TYPE -o value "$DEV" || true)"
WORK_NODE="$DEV"

if [[ -z "$CUR_ON_DEV" ]]; then
  if [[ -b "$PART1" ]]; then
    WORK_NODE="$PART1"
  else
    if [[ "$FSTYPE" != "KEEPFS" ]]; then
      log "Create GPT + single partition on $DEV"
      parted -s "$DEV" mklabel gpt mkpart primary 0% 100%
      partprobe "$DEV" || true
      udevadm settle || true
      for i in {1..20}; do [[ -b "$PART1" ]] && break; sleep 0.5; done
      [[ -b "$PART1" ]] || die "Partition $PART1 did not appear."
      WORK_NODE="$PART1"
    else
      log "No filesystem and KEEPFS requested -> nothing to do."
      exit 0
    fi
  fi
else
  WORK_NODE="$DEV"
fi

CUR_TYPE="$(blkid -s TYPE -o value "$WORK_NODE" || true)"

if [[ "$FSTYPE" != "KEEPFS" && -z "$CUR_TYPE" ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y || true
  apt-get install -y --no-install-recommends \
    parted util-linux e2fsprogs xfsprogs btrfs-progs ntfs-3g exfatprogs dosfstools f2fs-tools || true

  log "Formatting $WORK_NODE as $FSTYPE"
  case "$FSTYPE" in
    ext4)    mkfs.ext4 -F "$WORK_NODE" ;;
    ext3)    mkfs.ext3 -F "$WORK_NODE" ;;
    ext2)    mkfs.ext2 -F "$WORK_NODE" ;;
    xfs)     mkfs.xfs  -f "$WORK_NODE" ;;
    btrfs)   mkfs.btrfs -f "$WORK_NODE" ;;
    ntfs-3g) mkfs.ntfs -F "$WORK_NODE" ;;
    exfat)   mkfs.exfat -f "$WORK_NODE" ;;
    vfat)    mkfs.vfat -F 32 "$WORK_NODE" ;;
    f2fs)    mkfs.f2fs -f "$WORK_NODE" ;;
    *)       die "Unsupported fs_type: $FSTYPE" ;;
  esac
  CUR_TYPE="$FSTYPE"
fi

if [[ -z "$MNT" ]]; then
  log "No mount_point specified. Done."
  exit 0
fi

mkdir -p "$MNT"

UUID="$(blkid -s UUID -o value "$WORK_NODE" || true)"
[[ -n "$UUID" ]] || die "Failed to get UUID for $WORK_NODE"

FSTAB_FS="$CUR_TYPE"
[[ "$FSTAB_FS" == "ntfs" ]] && FSTAB_FS="ntfs-3g"
if [[ -z "$FSTAB_FS" ]]; then
  FSTAB_FS="$(blkid -s TYPE -o value "$WORK_NODE" || true)"
fi
[[ -n "$FSTAB_FS" ]] || die "Unknown filesystem type for fstab."

cp -f /etc/fstab /etc/fstab.bak.$(date +%s) || true
sed -i -E "s|^[^#][^[:space:]]+[[:space:]]+${MNT//\//\\/}[[:space:]].*$||" /etc/fstab
sed -i '/^[[:space:]]*$/d' /etc/fstab
echo "UUID=${UUID} ${MNT} ${FSTAB_FS} defaults 0 2" >> /etc/fstab

if findmnt -no TARGET "$MNT" >/dev/null 2>&1; then umount "$MNT" || true; fi
mount -t "$FSTAB_FS" -U "$UUID" "$MNT"
log "Mounted $WORK_NODE to $MNT as $FSTAB_FS"
