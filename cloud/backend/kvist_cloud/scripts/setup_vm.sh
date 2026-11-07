#!/usr/bin/env bash
# Usage: setup_vm.sh <ru|en> <disk-size, e.g. 20G>
set -euo pipefail

LANG_CODE="${1:-}"
DISK_SIZE="${2:-}"
IMAGES_DIR=/var/lib/libvirt/images
BASE_IMAGE="$IMAGES_DIR/debian-base.qcow2"
VM_IMAGE="$IMAGES_DIR/user-1001.qcow2"
BASE_URL=https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2

if [ -z "$DISK_SIZE" ] || { [ "$LANG_CODE" != "ru" ] && [ "$LANG_CODE" != "en" ]; }; then
    echo "Usage: $0 <ru|en> <disk-size, e.g. 20G>" >&2
    exit 2
fi

if [ "$LANG_CODE" = "ru" ]; then
    echo "Благодарим за покупку услуг Kvist Cloud. Сейчас активируем VM"
else
    echo "Thank you for purchasing Kvist Cloud services. Now activating your VM"
fi
sleep 3

# The base image is shared by all VMs; the per-user disk is a thin overlay on it.
if [ -f "$BASE_IMAGE" ]; then
    echo "Base image exists"
else
    echo "Base image missing, downloading..."
    mkdir -p "$IMAGES_DIR"
    # Download to a temp name so an interrupted curl never leaves a corrupt base image.
    curl -fL -o "$BASE_IMAGE.part" "$BASE_URL"
    mv "$BASE_IMAGE.part" "$BASE_IMAGE"
fi

if command -v qemu-system-x86_64 > /dev/null 2>&1; then
    echo "QEMU exists"
else
    echo "QEMU missing, installing..."
    apt-get update
    apt-get install -y qemu-system-x86 qemu-utils
fi

# Refuse to overwrite: recreating the overlay would wipe the user's data.
if [ -f "$VM_IMAGE" ]; then
    echo "VM disk $VM_IMAGE already exists, nothing to do" >&2
    exit 1
fi

echo "Creating VM..."
qemu-img create -f qcow2 -F qcow2 -b "$BASE_IMAGE" "$VM_IMAGE" "$DISK_SIZE"
echo "Done: $VM_IMAGE"
