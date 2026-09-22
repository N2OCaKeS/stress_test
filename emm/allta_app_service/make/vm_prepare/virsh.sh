#!/bin/bash

ACTION="$1"
VM="$2"
SNAPSHOT="$3"

function usage() {
    echo "Использование:"
    echo "  $0 list"
    echo "  $0 start <vm>"
    echo "  $0 stop <vm>"
    echo "  $0 reboot <vm>"
    echo "  $0 snapshot-create <vm> <snapshot>"
    echo "  $0 snapshot-revert <vm> <snapshot>"
    echo "  $0 snapshot-delete <vm> <snapshot>"
    echo "  $0 delete <vm>"
    exit 1
}

function list_vms() {
    virsh list --all
}

function start_vm() {
    virsh start "$VM"
}

function stop_vm() {
    virsh shutdown "$VM"
}

function reboot_vm() {
    virsh reboot "$VM"
}

function snapshot_create() {
    virsh snapshot-create-as --domain "$VM" --name "$SNAPSHOT" --description "Snapshot $SNAPSHOT" --atomic
}

function snapshot_revert() {
    virsh snapshot-revert "$VM" "$SNAPSHOT"
}

function snapshot_delete() {
    virsh snapshot-delete "$VM" "$SNAPSHOT"
}

function delete_vm() {
    echo "Отключение ВМ $VM (destroy)..."
    virsh destroy "$VM" 2>/dev/null

    echo "Удаление снимков ВМ $VM..."
    SNAP_LIST=$(virsh snapshot-list --name "$VM")
    for snap in $SNAP_LIST; do
        echo "  Удаляем снимок $snap"
        virsh snapshot-delete "$VM" "$snap"
    done

    echo "Удаление домена $VM..."
    virsh undefine "$VM" --remove-all-storage
    echo "ВМ $VM полностью удалена."
}

case "$ACTION" in
    list)
        list_vms
        ;;
    start)
        [ -z "$VM" ] && usage
        start_vm
        ;;
    stop)
        [ -z "$VM" ] && usage
        stop_vm
        ;;
    reboot)
        [ -z "$VM" ] && usage
        reboot_vm
        ;;
    snapshot-create)
        [ -z "$VM" ] || [ -z "$SNAPSHOT" ] && usage
        snapshot_create
        ;;
    snapshot-revert)
        [ -z "$VM" ] || [ -z "$SNAPSHOT" ] && usage
        snapshot_revert
        ;;
    snapshot-delete)
        [ -z "$VM" ] || [ -z "$SNAPSHOT" ] && usage
        snapshot_delete
        ;;
    delete)
        [ -z "$VM" ] && usage
        delete_vm
        ;;
    *)
        usage
        ;;
esac
