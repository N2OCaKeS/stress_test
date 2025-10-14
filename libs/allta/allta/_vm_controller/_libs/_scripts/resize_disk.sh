#!/usr/bin/env bash
set -euo pipefail

# ENV:
#   DRYRUN=1 — только показать команды
#   QUIET=1  — тихий режим
DRYRUN="${DRYRUN:-0}"
QUIET="${QUIET:-0}"

log(){ [[ "${QUIET}" -eq 1 ]] || echo -e "[*] $*"; }
err(){ echo -e "[!] $*" >&2; }
have(){ command -v "$1" >/dev/null 2>&1; }
run(){ if [[ "${DRYRUN}" -eq 1 ]]; then echo "+ $*"; else eval "$@"; fi; }

# --- Требуем bash ---
if ! ps -o comm= -p $$ | grep -q '^bash$'; then
  exec bash "$0" "$@"
fi

[[ $EUID -eq 0 ]] || { err "Нужны root-права. Запустите: sudo $0"; exit 1; }

# --- Детект ОС ---
OS_ID=""; OS_LIKE=""
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  OS_ID="${ID:-}"
  OS_LIKE="${ID_LIKE:-}"
fi
OS_ID="${OS_ID,,}"; OS_LIKE="${OS_LIKE,,}"

is_deb_family(){ [[ "$OS_ID" == "debian" || "$OS_ID" == "ubuntu" || "$OS_ID" == astra* || "$OS_LIKE" == *debian* ]]; }
is_rhel_family(){ [[ "$OS_ID" == "rhel" || "$OS_ID" == "rocky" || "$OS_ID" == "almalinux" || "$OS_ID" == "centos" || "$OS_ID" == "redos" || "$OS_LIKE" == *rhel* || "$OS_LIKE" == *fedora* ]]; }
is_alt_family(){  [[ "$OS_ID" == "alt" || "$OS_LIKE" == *alt* ]]; }

# --- Пакетный менеджер ---
PM=""
have apt-get && PM="apt"
have dnf && PM="dnf"
[[ -z "$PM" && "$(have yum && echo 1 || echo 0)" -eq 1 ]] && PM="yum"
[[ -z "$PM" && "$(have epm && echo 1 || echo 0)" -eq 1 ]] && PM="epm"
[[ -n "$PM" ]] || { err "Не удалось определить пакетный менеджер (apt/dnf/yum/epm)."; exit 1; }

pkg_update_once=0
pm_update(){
  (( pkg_update_once == 0 )) || return 0
  case "$PM" in
    apt)  log "Обновляю индексы пакетов..."; DEBIAN_FRONTEND=noninteractive apt-get update -yq || true ;;
    dnf|yum|epm) : ;;
  esac
  pkg_update_once=1
}

pm_install(){
  local pkg="$1"
  case "$PM" in
    apt)  DEBIAN_FRONTEND=noninteractive apt-get install -yq "$pkg" ;;
    dnf)  dnf install -y "$pkg" ;;
    yum)  yum install -y "$pkg" ;;
    epm)  epm install -y "$pkg" ;;
  esac
}

ensure_pkg_by_names(){
  local want_bin="$1"; shift
  have "$want_bin" && return 0
  pm_update
  local name
  for name in "$@"; do
    log "Устанавливаю $name (для $want_bin)..."
    if pm_install "$name"; then
      have "$want_bin" && return 0
    fi
  done
  have "$want_bin" || return 1
}

# --- Зависимости ---
ensure_pkg_by_names findmnt  util-linux || { err "Не удалось установить util-linux (findmnt)."; exit 1; }
ensure_pkg_by_names lsblk    util-linux || { err "Не удалось установить util-linux (lsblk)."; exit 1; }
ensure_pkg_by_names resize2fs e2fsprogs || { err "Не удалось установить e2fsprogs (resize2fs)."; exit 1; }
ensure_pkg_by_names parted   parted     || { err "Не удалось установить parted."; exit 1; }
ensure_pkg_by_names udevadm  udev systemd-udev systemd || true

if is_deb_family || is_alt_family; then
  ensure_pkg_by_names growpart cloud-guest-utils cloud-utils-growpart || true
elif is_rhel_family; then
  ensure_pkg_by_names growpart cloud-utils-growpart cloud-utils || true
else
  ensure_pkg_by_names growpart cloud-utils-growpart cloud-guest-utils cloud-utils || true
fi

# --- Определяем корень ---
ROOT_SRC="$(findmnt -no SOURCE / || true)"
ROOT_FSTYPE="$(findmnt -no FSTYPE / || true)"
[[ -n "$ROOT_SRC" ]] || { err "Не удалось определить устройство для /"; exit 1; }
[[ "$ROOT_FSTYPE" == "ext4" ]] || { err "Ожидалась ext4, получено: $ROOT_FSTYPE"; exit 1; }
[[ "$ROOT_SRC" == /dev/* ]] || { err "Корень не на блочном устройстве /dev/*: $ROOT_SRC"; exit 1; }

get_disk_and_part(){
  local dev="$1" base="" part=""
  if have udevadm; then
    local props; props="$(udevadm info -q property -n "$dev" 2>/dev/null || true)"
    if [[ -n "$props" ]]; then
      part="$(grep -E '^ID_PART_ENTRY_NUMBER=' <<<"$props" | cut -d= -f2 || true)"
      base="$(lsblk -no PKNAME "$dev" 2>/dev/null || true)"
      [[ -n "$base" ]] && base="/dev/$base"
    fi
  fi
  if [[ -z "$part" || -z "$base" ]]; then
    local name; name="$(basename "$dev")"
    case "$name" in
      nvme*n*p*[0-9]) base="/dev/${name%p*}"; part="${name##*p}";;
      mmcblk*p*[0-9]) base="/dev/${name%p*}"; part="${name##*p}";;
      *[0-9])         base="/dev/${name%%[0-9]*}"; part="${name##*[!0-9]}";;
      *) return 1;;
    esac
  fi
  [[ -n "$base" && -n "$part" ]] || return 1
  echo "$base $part"
}

read -r DISK PARTNUM < <(get_disk_and_part "$ROOT_SRC") || { err "Не удалось определить базовый диск/номер раздела для $ROOT_SRC"; exit 1; }

log "Корень:        $ROOT_SRC ($ROOT_FSTYPE)"
log "Базовый диск:  $DISK"
log "Номер раздела: $PARTNUM"

# --- Rescan диска (если увеличен на хосте) ---
BLK="$(basename "$DISK")"
if [[ -w "/sys/class/block/$BLK/device/rescan" ]]; then
  log "Rescan диска $DISK..."
  echo 1 > "/sys/class/block/$BLK/device/rescan" || true
  run "udevadm settle || true"
fi

# --- Расширение раздела ---
log "Расширяю раздел до конца диска..."
if have growpart; then
  run "growpart '$DISK' '$PARTNUM'"
else
  run "printf 'Yes\n' | parted ---pretend-input-tty -s '$DISK' unit % resizepart '$PARTNUM' 100%"
fi

# --- Обновляем таблицу разделов ---
have partprobe && run "partprobe '$DISK' || true"
run "udevadm settle || true"

# --- Расширяем ФС ---
log "Расширяю файловую систему ext4 на $ROOT_SRC..."
run "resize2fs '$ROOT_SRC'"

log "Готово. Раздел и файловая система расширены."
