#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   sudo ./additional_disk.sh <serial> [fs_type] [mount_point]
# Examples:
#   sudo ./additional_disk.sh testvm1_disk3
#   sudo ./additional_disk.sh testvm1_disk2 ext3
#   sudo ./additional_disk.sh testvm1_disk1 btrfs /home/testuser
#   sudo ./additional_disk.sh testvm1_disk4 ntfs /mnt/win

SERIAL="${1:-}"
FSTYPE_RAW="${2:-ext4}"
MNT="${3:-}"   # опционально

log() { echo "[INFO] $*"; }
die(){ echo "[ERROR] $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run as root."
[[ -n "$SERIAL" ]] || die "Usage: $0 <serial> [fs_type] [mount_point]"

# нормализуем имя типа для fstab (ntfs -> ntfs-3g и т.п.)
norm_fstype() {
  case "$1" in
    ext2|ext3|ext4|xfs|btrfs|exfat|vfat|f2fs) echo "$1" ;;
    ntfs|ntfs-3g) echo "ntfs-3g" ;;  # запись в fstab и монтирование через ntfs-3g
    *) echo "$1" ;;
  esac
}

FSTYPE="$(norm_fstype "$FSTYPE_RAW")"

# Зависимости (Debian/Ubuntu/Astra)
export DEBIAN_FRONTEND=noninteractive
apt-get update -y || true
apt-get install -y --no-install-recommends \
  parted util-linux e2fsprogs xfsprogs btrfs-progs ntfs-3g exfatprogs dosfstools f2fs-tools || true

BYID="/dev/disk/by-id/virtio-${SERIAL}"
for i in {1..60}; do
  [[ -e "$BYID" ]] && break
  sleep 1
done
[[ -e "$BYID" ]] || die "Device $BYID not found."

DEV="$(readlink -f "$BYID")"
[[ -b "$DEV" ]] || die "Resolved $DEV is not a block device."

# Не трогаем корневой диск
ROOT_SRC="$(findmnt -no SOURCE / || true)"
if [[ "$ROOT_SRC" == "$DEV" || "$ROOT_SRC" == "${DEV}1" || "$ROOT_SRC" == "${DEV}p1" ]]; then
  die "Refuse to touch root disk ($ROOT_SRC)."
fi

# Имя первого раздела
if [[ "$DEV" == *"nvme"* || "$DEV" == *"mmcblk"* ]]; then
  PART="${DEV}p1"
else
  PART="${DEV}1"
fi

# Разметка, если пусто (тип ФС НЕ указываем в parted)
if [[ "$(lsblk -no NAME "$DEV" | awk 'NR>1{print}' | wc -l)" -eq 0 ]]; then
  log "No partitions on $DEV. Creating GPT + single partition..."
  parted -s "$DEV" mklabel gpt mkpart primary 0% 100%
  partprobe "$DEV" || true
  udevadm settle || true
  for i in {1..20}; do
    [[ -b "$PART" ]] && break
    sleep 0.5
  done
  [[ -b "$PART" ]] || die "Partition $PART did not appear."
else
  log "Partitions exist on $DEV; using $PART"
  [[ -b "$PART" ]] || die "Expected $PART but not found."
fi

# Текущая ФС
CUR_TYPE="$(blkid -s TYPE -o value "$PART" || true)"
if [[ -z "$CUR_TYPE" || "$CUR_TYPE" != "$FSTYPE" ]]; then
  log "Formatting $PART as $FSTYPE (current: ${CUR_TYPE:-none})"
  case "$FSTYPE" in
    ext4)   mkfs.ext4 -F "$PART" ;;
    ext3)   mkfs.ext3 -F "$PART" ;;
    ext2)   mkfs.ext2 -F "$PART" ;;
    xfs)    mkfs.xfs  -f "$PART" ;;
    btrfs)  mkfs.btrfs -f "$PART" ;;
    ntfs-3g) mkfs.ntfs -F "$PART" ;;        # создаёт ntfs; монтируем через ntfs-3g
    exfat)  mkfs.exfat -f "$PART" ;;
    vfat)   mkfs.vfat -F 32 "$PART" ;;      # FAT32
    f2fs)   mkfs.f2fs -f "$PART" ;;
    *)      die "Unsupported fs_type: $FSTYPE (supported: ext2, ext3, ext4, xfs, btrfs, ntfs, exfat, vfat, f2fs)" ;;
  esac
else
  log "Filesystem already $CUR_TYPE; skipping mkfs."
fi

# Если mount_point не задан — завершаем после разметки/ФС
if [[ -z "${MNT}" ]]; then
  log "No mount_point provided. Skipping fstab and mount."
  exit 0
fi

mkdir -p "$MNT"

UUID="$(blkid -s UUID -o value "$PART" || true)"
[[ -n "$UUID" ]] || die "Failed to get UUID for $PART"

FSTAB_FS="$FSTYPE"   # для ntfs используем ntfs-3g
FSTAB_LINE="UUID=${UUID} ${MNT} ${FSTAB_FS} defaults 0 2"

# Очистим старые записи для того же mount_point (резервная копия на всякий)
cp -f /etc/fstab /etc/fstab.bak.$(date +%s) || true
sed -i -E "s|^[^#][^[:space:]]+[[:space:]]+${MNT//\//\\/}[[:space:]].*$||" /etc/fstab
sed -i '/^[[:space:]]*$/d' /etc/fstab

# Добавим актуальную строку
echo "$FSTAB_LINE" >> /etc/fstab
log "fstab updated: $FSTAB_LINE"

# Если уже смонтировано — размонтируем, чтобы примонтировать наш UUID
if findmnt -no TARGET "$MNT" >/dev/null 2>&1; then
  umount "$MNT" || true
fi

# Монтируем по UUID и нужному типу
mount -t "$FSTAB_FS" -U "$UUID" "$MNT" || die "mount -t $FSTAB_FS -U $UUID $MNT failed"

log "Done."
