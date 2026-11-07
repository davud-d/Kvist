#!/usr/bin/env bash
# Provisions one customer VM on the dedicated host: minimal Debian 12, user "kvist", Kvist + runner installed.
#
# Usage:  KVIST_MACHINE_TOKEN=<token> setup_vm.sh <ru|en> <machine_id> <vcpu> <memory_gb> <disk_gb> <gateway_url>
#
# The token goes through the environment, never argv (argv is visible to every local user).
# Host inputs:
#   WHEELS_DIR (default /opt/kvist-cloud/wheels)  wheels of kvist + kvist-runner and all their dependencies,
#                                                 built with: pip wheel -w "$WHEELS_DIR" . cloud/runner
#   SKEL_DIR   (default /opt/kvist-cloud/skel)    optional; copied into the guest's ~/.kvist. No secrets in it.
# The VM is named kvist-<machine_id>, the same id as machines.id in the database, so one id ties disk,
# domain and owner together. "One machine per account" is enforced by the backend (machines.live_machine).
set -euo pipefail

LANG_CODE="${1:-}"
MACHINE_ID="${2:-}"
VCPU="${3:-}"
MEMORY_GB="${4:-}"
DISK_GB="${5:-}"
GATEWAY_URL="${6:-}"
TOKEN="${KVIST_MACHINE_TOKEN:-}"

WHEELS_DIR="${WHEELS_DIR:-/opt/kvist-cloud/wheels}"
SKEL_DIR="${SKEL_DIR:-/opt/kvist-cloud/skel}"
IMAGES_DIR=/var/lib/libvirt/images
IMAGE_NAME=debian-12-genericcloud-amd64.qcow2
IMAGE_BASE_URL=https://cloud.debian.org/images/cloud/bookworm/latest
BASE_IMAGE="$IMAGES_DIR/$IMAGE_NAME"

say() { # say "<ru text>" "<en text>"
    if [ "$LANG_CODE" = "ru" ]; then echo "$1"; else echo "$2"; fi
}

usage() {
    echo "Usage: KVIST_MACHINE_TOKEN=<token> $0 <ru|en> <machine_id> <vcpu> <memory_gb> <disk_gb> <wss-gateway-url>" >&2
    exit 2
}

is_number() { [[ "$1" =~ ^[1-9][0-9]*$ ]]; }

{ [ "$LANG_CODE" = "ru" ] || [ "$LANG_CODE" = "en" ]; } || usage
for value in "$MACHINE_ID" "$VCPU" "$MEMORY_GB" "$DISK_GB"; do is_number "$value" || usage; done
[ -n "$TOKEN" ] || usage
# A token over plain ws:// would cross the network in clear text.
[[ "$GATEWAY_URL" == wss://* ]] || { say "Шлюз должен быть wss://" "Gateway URL must be wss://" >&2; exit 2; }
# Hard ceilings = the largest plan in plans.py (max). The backend picks the numbers from the customer's plan;
# this is the last line of defence against a bug or a bad call handing out the whole server.
MAX_VCPU=4
MAX_MEMORY_GB=8
MAX_DISK_GB=150
# What the host keeps for itself and for the other VMs.
HOST_RESERVE_MEMORY_GB=2
HOST_RESERVE_DISK_GB=20

if [ "$VCPU" -gt "$MAX_VCPU" ] || [ "$MEMORY_GB" -gt "$MAX_MEMORY_GB" ] || [ "$DISK_GB" -gt "$MAX_DISK_GB" ]; then
    say "Запрошено больше потолка: ядер ≤ $MAX_VCPU, ОЗУ ≤ $MAX_MEMORY_GB ГБ, диск ≤ $MAX_DISK_GB ГБ" \
        "Request over the ceiling: vcpu <= $MAX_VCPU, memory <= $MAX_MEMORY_GB GB, disk <= $MAX_DISK_GB GB" >&2
    exit 2
fi

[ "$(id -u)" -eq 0 ] || { say "Нужен root" "Run as root" >&2; exit 1; }

available_memory_gb=$(awk '/^MemAvailable:/ {print int($2 / 1048576)}' /proc/meminfo)
if [ "$available_memory_gb" -lt "$((MEMORY_GB + HOST_RESERVE_MEMORY_GB))" ]; then
    say "На хосте мало свободной ОЗУ (${available_memory_gb} ГБ)" "Not enough free memory on the host (${available_memory_gb} GB)" >&2
    exit 1
fi
mkdir -p "$IMAGES_DIR"
available_disk_gb=$(df --output=avail -BG "$IMAGES_DIR" | tail -n 1 | tr -dc '0-9')
if [ "$available_disk_gb" -lt "$((DISK_GB + HOST_RESERVE_DISK_GB))" ]; then
    say "На хосте мало места на диске (${available_disk_gb} ГБ)" "Not enough free disk on the host (${available_disk_gb} GB)" >&2
    exit 1
fi

DOMAIN="kvist-$MACHINE_ID"
VM_DISK="$IMAGES_DIR/$DOMAIN.qcow2"
SEED_ISO="$IMAGES_DIR/$DOMAIN-seed.iso"

say "Благодарим за покупку услуг Kvist Cloud. Сейчас активируем VM" \
    "Thank you for purchasing Kvist Cloud services. Now activating your VM"

# Refuse to overwrite: recreating the disk would wipe the customer's data.
if [ -f "$VM_DISK" ] || virsh dominfo "$DOMAIN" > /dev/null 2>&1; then
    say "VM $DOMAIN уже существует" "VM $DOMAIN already exists" >&2
    exit 1
fi
[ -d "$WHEELS_DIR" ] || { echo "No wheels in $WHEELS_DIR" >&2; exit 1; }

# --- host packages --------------------------------------------------------------------------------
if ! command -v virt-install > /dev/null 2>&1 || ! command -v qemu-img > /dev/null 2>&1 \
    || ! command -v genisoimage > /dev/null 2>&1; then
    say "Ставлю QEMU/libvirt..." "Installing QEMU/libvirt..."
    apt-get update
    apt-get install -y qemu-system-x86 qemu-utils libvirt-daemon-system virtinst genisoimage
fi
systemctl enable --now libvirtd
virsh net-info default 2> /dev/null | grep -q 'Active:.*yes' || virsh net-start default
virsh net-autostart default > /dev/null

# --- base image: shared by all VMs, verified against Debian's published checksum ---------------------
mkdir -p "$IMAGES_DIR"
if [ ! -f "$BASE_IMAGE" ]; then
    say "Скачиваю образ Debian 12..." "Downloading the Debian 12 image..."
    work="$(mktemp -d)"
    curl -fL -o "$work/$IMAGE_NAME" "$IMAGE_BASE_URL/$IMAGE_NAME"
    curl -fL -o "$work/SHA512SUMS" "$IMAGE_BASE_URL/SHA512SUMS"
    (cd "$work" && grep " $IMAGE_NAME\$" SHA512SUMS | sha512sum -c -)
    mv "$work/$IMAGE_NAME" "$BASE_IMAGE"
    rm -rf "$work"
fi

# From here on, any failure removes what this run created.
seed_dir="$(mktemp -d)"
cleanup_on_error() {
    virsh destroy "$DOMAIN" > /dev/null 2>&1 || true
    virsh undefine "$DOMAIN" > /dev/null 2>&1 || true
    rm -f "$VM_DISK" "$SEED_ISO"
}
trap 'rm -rf "$seed_dir"' EXIT
trap 'cleanup_on_error' ERR

# --- the seed: cloud-init config, runner config and wheels travel on one read-only ISO -------------
umask 077
cat > "$seed_dir/meta-data" << EOF
instance-id: $DOMAIN
# The machine id stays on the host side; `hostname` inside the VM does not reveal it.
local-hostname: kvist
EOF

# JSON built by Python so a token can never break the quoting. Same shape as LocalProvider's runner.json.
MACHINE_ID="$MACHINE_ID" VCPU="$VCPU" MEMORY_GB="$MEMORY_GB" DISK_GB="$DISK_GB" GATEWAY_URL="$GATEWAY_URL" \
    TOKEN="$TOKEN" python3 -I - > "$seed_dir/runner.json" << 'PY'
import json, os
env = os.environ
print(json.dumps({
    "machine_id": int(env["MACHINE_ID"]),
    "gateway_url": env["GATEWAY_URL"],
    "token": env["TOKEN"],
    # Projects are ~/<name> and Kvist keeps its config in ~/.kvist; the runner's own state lives apart.
    "root": "/var/lib/kvist-runner",
    "work_dir": "/home/kvist",
    "home_dir": "/home/kvist",
    "kvist_command": ["/opt/kvist/bin/kvist"],
    "heartbeat_seconds": 15,
    "machine": {"vcpu": int(env["VCPU"]), "memory_gb": int(env["MEMORY_GB"]), "disk_gb": int(env["DISK_GB"])},
}))
PY

cp -r "$WHEELS_DIR" "$seed_dir/wheels"
[ -d "$SKEL_DIR" ] && cp -r "$SKEL_DIR" "$seed_dir/skel"

cat > "$seed_dir/user-data" << 'EOF'
#cloud-config
# The customer never logs in over ssh: no password, no key, sshd off. Everything goes through the runner,
# which only dials out to the gateway.
users:
  - name: kvist
    shell: /bin/bash
    lock_passwd: true
ssh_pwauth: false
disable_root: true
package_update: true
packages:
  - python3
  - python3-venv
  - python3-pip
  - build-essential
  - git
  - curl
  - ca-certificates
  - nodejs
  - npm
  - bubblewrap
write_files:
  - path: /etc/systemd/system/kvist-runner.service
    content: |
      [Unit]
      Description=Kvist Cloud runner
      After=network-online.target
      Wants=network-online.target

      [Service]
      User=kvist
      WorkingDirectory=/home/kvist
      ExecStart=/opt/kvist/bin/kvist-runner --config /etc/kvist-runner/runner.json
      Restart=always
      RestartSec=5
      NoNewPrivileges=true

      [Install]
      WantedBy=multi-user.target
runcmd:
  - mount -o ro /dev/disk/by-label/cidata /mnt
  - python3 -m venv /opt/kvist
  - /opt/kvist/bin/pip install --no-index --find-links /mnt/wheels kvist kvist-runner
  - install -d -o kvist -g kvist /home/kvist/.kvist /var/lib/kvist-runner
  - install -d /etc/kvist-runner
  - install -m 600 -o kvist -g kvist /mnt/runner.json /etc/kvist-runner/runner.json
  - if [ -d /mnt/skel ]; then cp -r /mnt/skel/. /home/kvist/.kvist/ && chown -R kvist:kvist /home/kvist/.kvist; fi
  - 'echo "PS1=''kvist :-) ''" >> /home/kvist/.bashrc'
  - umount /mnt
  - systemctl disable --now ssh
  - systemctl enable --now kvist-runner
EOF

genisoimage -quiet -output "$SEED_ISO" -volid cidata -joliet -rock "$seed_dir"

# --- disk and domain ------------------------------------------------------------------------------
say "Создаю VM..." "Creating VM..."
qemu-img create -f qcow2 -F qcow2 -b "$BASE_IMAGE" "$VM_DISK" "${DISK_GB}G" > /dev/null
virt-install --name "$DOMAIN" --import \
    --vcpus "$VCPU" --memory "$((MEMORY_GB * 1024))" \
    --disk "path=$VM_DISK" --disk "path=$SEED_ISO,device=cdrom,readonly=on" \
    --os-variant debian12 --network network=default \
    --graphics none --noautoconsole
virsh autostart "$DOMAIN" > /dev/null

say "Готово: $DOMAIN. Раннер сам подключится к шлюзу." "Done: $DOMAIN. The runner will dial the gateway by itself."
