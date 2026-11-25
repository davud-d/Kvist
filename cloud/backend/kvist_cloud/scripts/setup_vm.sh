#!/usr/bin/env bash
# Provisions customers' VMs on the dedicated host: minimal Debian 12, user "kvist", Kvist + runner installed.
#
# Usage:  KVIST_MACHINE_TOKEN=<token> setup_vm.sh <ru|en> <machine_id> <vcpu> <memory_gb> <disk_gb> <gateway_url>
#         setup_vm.sh golden       builds the image every customer VM starts from (see below)
#
# The token goes through the environment, never argv (argv is visible to every local user).
# Host inputs:
#   WHEELS_DIR (default /opt/kvist-cloud/wheels)  wheels of kvist + kvist-runner and all their dependencies,
#                                                 built with: pip wheel -w "$WHEELS_DIR" . cloud/runner
#   SKEL_DIR   (default /opt/kvist-cloud/skel)    optional; copied into the guest's ~/.kvist. No secrets in it.
# The VM is named kvist-<machine_id>, the same id as machines.id in the database, so one id ties disk,
# domain and owner together. "One machine per account" is enforced by the backend (machines.live_machine).
#
# The golden image: installing the guest's packages (a browser, a virtual screen, build tools) takes minutes, so it
# is done once, not per customer. `golden` boots a throw-away VM that installs everything and powers off; the disk is
# then wiped of what must be unique (machine id, ssh host keys, cloud-init's state) and kept as
# kvist-golden-<hash>.qcow2, the hash covering the recipe and the wheels. A customer's disk is a thin layer over it
# and its first boot only has to receive its runner config: seconds, not minutes. A deploy builds the image when the
# wheels changed (scripts/install_vm_host.sh); a create builds it only if that was never done.
set -euo pipefail

WHEELS_DIR="${WHEELS_DIR:-/opt/kvist-cloud/wheels}"
SKEL_DIR="${SKEL_DIR:-/opt/kvist-cloud/skel}"
IMAGES_DIR=/var/lib/libvirt/images
IMAGE_NAME=debian-12-genericcloud-amd64.qcow2
IMAGE_BASE_URL=https://cloud.debian.org/images/cloud/bookworm/latest
BASE_IMAGE="$IMAGES_DIR/$IMAGE_NAME"
# The smallest plan's disk is 10 GB; a customer's disk may not be smaller than the image it is a layer over.
GOLDEN_DISK_GB=8
GOLDEN_BUILD_SECONDS=1500
BUILD_DOMAIN=kvist-golden-build
BUILD_DISK="$IMAGES_DIR/$BUILD_DOMAIN.qcow2"
BUILD_SEED="$IMAGES_DIR/$BUILD_DOMAIN-seed.iso"
GOLDEN=""

LANG_CODE="en"
say() { # say "<ru text>" "<en text>"
    if [ "$LANG_CODE" = "ru" ]; then echo "$1"; else echo "$2"; fi
}

usage() {
    echo "Usage: KVIST_MACHINE_TOKEN=<token> $0 <ru|en> <machine_id> <vcpu> <memory_gb> <disk_gb> <wss-gateway-url>" >&2
    echo "       $0 golden" >&2
    exit 2
}

is_number() { [[ "$1" =~ ^[1-9][0-9]*$ ]]; }

# --- host: packages, libvirt, the default network, the Debian base image -----------------------------------
ensure_host() {
    if ! command -v virt-install > /dev/null 2>&1 || ! command -v qemu-img > /dev/null 2>&1 \
        || ! command -v genisoimage > /dev/null 2>&1; then
        say "Ставлю QEMU/libvirt..." "Installing QEMU/libvirt..."
        apt-get update
        apt-get install -y qemu-system-x86 qemu-utils libvirt-daemon-system virtinst genisoimage
    fi
    systemctl enable --now libvirtd
    # awk reads everything: `| grep -q` would quit early, virsh would get SIGPIPE and pipefail would call it a failure.
    [ "$(virsh net-info default 2> /dev/null | awk '/^Active:/ {print $2}')" = yes ] || virsh net-start default
    virsh net-autostart default > /dev/null
    mkdir -p "$IMAGES_DIR"
}

ensure_base_image() {
    [ -f "$BASE_IMAGE" ] && return 0
    say "Скачиваю образ Debian 12..." "Downloading the Debian 12 image..."
    local work
    work="$(mktemp -d)"
    curl -fL -o "$work/$IMAGE_NAME" "$IMAGE_BASE_URL/$IMAGE_NAME"
    curl -fL -o "$work/SHA512SUMS" "$IMAGE_BASE_URL/SHA512SUMS"
    (cd "$work" && grep " $IMAGE_NAME\$" SHA512SUMS | sha512sum -c -)
    mv "$work/$IMAGE_NAME" "$BASE_IMAGE"
    rm -rf "$work"
}

# --- the golden image --------------------------------------------------------------------------------------
# What a guest has to be, written once. One missing name fails the whole apt transaction, so every package below
# is a Debian 12 (bookworm) one.
golden_user_data() {
    cat << 'EOF'
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
  # Python for the core and the runner, a compiler for wheels that have to be built, the usual project tools.
  - python3
  - python3-venv
  - python3-pip
  - build-essential
  - git
  - openssh-client
  - curl
  - ca-certificates
  - nodejs
  - npm
  - ripgrep
  - jq
  - unzip
  - zip
  - procps
  # The sandbox of the core and of the skills.
  - bubblewrap
  # Virtual screen of the skills: Xvfb plus a window manager (i3 for browser, JWM for app_screen).
  - xvfb
  - i3-wm
  - jwm
  - feh
  - xclip
  # A skill's runner starts under the system python3 -I, so python-xlib (app_screen) has to come from apt.
  - python3-xlib
  # browser skill: Chromium renders in software; Mesa gives app_screen's gl=true programs a software OpenGL.
  - chromium
  - libgl1-mesa-dri
  - libglx-mesa0
  - libegl-mesa0
  # A program app_screen can open without OpenGL.
  - xterm
  # Fonts with Cyrillic and emoji, or pages and windows show boxes.
  - fonts-dejavu-core
  - fonts-liberation
  - fonts-noto-color-emoji
  - qemu-guest-agent
write_files:
  # The guest answers nobody on the network: no LLMNR (5355) and, below, no mDNS (avahi, 5353).
  - path: /etc/systemd/resolved.conf.d/kvist.conf
    content: |
      [Resolve]
      LLMNR=no
      MulticastDNS=no
  # Starts only once a customer's config has arrived (the per-VM first boot writes it).
  - path: /etc/systemd/system/kvist-runner.service
    content: |
      [Unit]
      Description=Kvist Cloud runner
      After=network-online.target
      Wants=network-online.target
      ConditionPathExists=/etc/kvist-runner/runner.json

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
  - umount /mnt
  - install -d -o kvist -g kvist /home/kvist/.kvist /var/lib/kvist-runner
  # Every bundled skill with Python pins gets its environment now: one left out shows up as "unavailable" in the
  # customer's VM (a test keeps this list equal to the manifests).
  - runuser -u kvist -- /opt/kvist/bin/kvist --workspace /home/kvist --skill-op install-deps --skill browser --yes
  - runuser -u kvist -- /opt/kvist/bin/kvist --workspace /home/kvist --skill-op install-deps --skill code_nav --yes
  - runuser -u kvist -- /opt/kvist/bin/kvist --workspace /home/kvist --skill-op install-deps --skill documents --yes
  - install -d /etc/kvist-runner
  - 'echo "PS1=''kvist :-) ''" >> /home/kvist/.bashrc'
  - systemctl enable kvist-runner qemu-guest-agent
  - systemctl disable avahi-daemon.socket avahi-daemon.service || true
  - systemctl disable ssh
power_state:
  mode: poweroff
  message: golden image ready
  timeout: 30
  condition: true
EOF
}

# One name per recipe and per set of wheels: a change to either makes a new image.
golden_name() {
    local hash
    [ -d "$WHEELS_DIR" ] && ls "$WHEELS_DIR"/*.whl > /dev/null 2>&1 || { echo "No wheels in $WHEELS_DIR" >&2; exit 1; }
    hash="$({ golden_user_data; echo "$IMAGE_NAME"; LC_ALL=C cat "$WHEELS_DIR"/*.whl; } | sha256sum | cut -c1-12)"
    echo "$IMAGES_DIR/kvist-golden-$hash.qcow2"
}

# Wipes what must be unique per VM from the finished disk: the next boot is a first boot, with its own identity.
sysprep_disk() {
    local disk="$1" dev="" candidate mount_dir
    modprobe nbd max_part=8
    for candidate in /dev/nbd{0..15}; do
        if [ ! -e "/sys/block/${candidate#/dev/}/pid" ]; then dev="$candidate"; break; fi
    done
    [ -n "$dev" ] || { echo "No free nbd device" >&2; return 1; }
    qemu-nbd --connect="$dev" "$disk"
    mount_dir="$(mktemp -d)"
    local waited=0
    while [ ! -b "${dev}p1" ] && [ "$waited" -lt 20 ]; do sleep 0.5; waited=$((waited + 1)); done
    if mount "${dev}p1" "$mount_dir"; then
        rm -rf "$mount_dir"/var/lib/cloud/* "$mount_dir"/var/log/cloud-init*.log "$mount_dir"/var/log/journal/* \
            "$mount_dir"/var/lib/systemd/random-seed "$mount_dir"/var/lib/dhcp/* \
            "$mount_dir"/etc/network/interfaces.d/50-cloud-init "$mount_dir"/etc/systemd/network/10-cloud-init-*
        rm -f "$mount_dir"/etc/ssh/ssh_host_*
        # An empty machine-id is made anew at the next boot; DHCP identity and the journal follow it.
        : > "$mount_dir/etc/machine-id"
        umount "$mount_dir"
        rmdir "$mount_dir"
        qemu-nbd --disconnect "$dev" > /dev/null
        return 0
    fi
    rmdir "$mount_dir"
    qemu-nbd --disconnect "$dev" > /dev/null
    echo "Could not mount the golden disk" >&2
    return 1
}

drop_build() {
    virsh destroy "$BUILD_DOMAIN" > /dev/null 2>&1 || true
    virsh undefine "$BUILD_DOMAIN" --nvram > /dev/null 2>&1 || virsh undefine "$BUILD_DOMAIN" > /dev/null 2>&1 || true
    rm -f "$BUILD_DISK" "$BUILD_SEED"
}

build_golden() {
    local seed_dir waited=0 state
    say "Собираю базовый образ гостя (один раз, несколько минут)..." "Building the guest image (once, a few minutes)..."
    seed_dir="$(mktemp -d)"
    trap 'rm -rf "$seed_dir"; drop_build' EXIT
    drop_build
    umask 077
    printf 'instance-id: %s\nlocal-hostname: kvist\n' "$BUILD_DOMAIN" > "$seed_dir/meta-data"
    golden_user_data > "$seed_dir/user-data"
    cp -r "$WHEELS_DIR" "$seed_dir/wheels"
    genisoimage -quiet -output "$BUILD_SEED" -volid cidata -joliet -rock "$seed_dir"
    qemu-img create -f qcow2 -F qcow2 -b "$BASE_IMAGE" "$BUILD_DISK" "${GOLDEN_DISK_GB}G" > /dev/null
    virt-install --name "$BUILD_DOMAIN" --import --vcpus 2 --memory 2048 \
        --disk "path=$BUILD_DISK" --disk "path=$BUILD_SEED,device=disk,bus=virtio,format=raw,readonly=on" \
        --os-variant debian12 --network network=default --graphics none --noautoconsole
    # The guest powers itself off when it is done.
    while true; do
        state="$(virsh domstate "$BUILD_DOMAIN" 2> /dev/null || echo gone)"
        [ "$state" = "shut off" ] && break
        if [ "$waited" -ge "$GOLDEN_BUILD_SECONDS" ]; then
            echo "The golden image did not finish in $GOLDEN_BUILD_SECONDS s (state: $state)" >&2
            exit 1
        fi
        sleep 5
        waited=$((waited + 5))
    done
    sysprep_disk "$BUILD_DISK"
    # Flattened: the image stands on its own, whatever happens to the base image later.
    qemu-img convert -O qcow2 "$BUILD_DISK" "$GOLDEN.tmp"
    chmod 644 "$GOLDEN.tmp"
    if id libvirt-qemu > /dev/null 2>&1; then chown libvirt-qemu:libvirt-qemu "$GOLDEN.tmp"; fi
    mv "$GOLDEN.tmp" "$GOLDEN"
    trap - EXIT
    rm -rf "$seed_dir"
    drop_build
    say "Образ готов: $(basename "$GOLDEN")" "Image ready: $(basename "$GOLDEN")"
}

# An older image goes once no customer's disk stands on it.
prune_golden() {
    local old vm info in_use
    for old in "$IMAGES_DIR"/kvist-golden-*.qcow2; do
        [ -e "$old" ] || continue
        [ "$old" = "$GOLDEN" ] && continue
        in_use=no
        for vm in "$IMAGES_DIR"/kvist-[0-9]*.qcow2; do
            [ -e "$vm" ] || continue
            info="$(qemu-img info -U "$vm" 2> /dev/null || true)"
            if [[ "$info" == *"$old"* ]]; then in_use=yes; break; fi
        done
        if [ "$in_use" = no ]; then rm -f "$old"; fi
    done
}

ensure_golden() {
    GOLDEN="$(golden_name)"
    [ -f "$GOLDEN" ] && return 0
    # Two creates at once must not build it twice.
    exec 9> "$IMAGES_DIR/.golden.lock"
    flock 9
    [ -f "$GOLDEN" ] && return 0
    ensure_base_image
    build_golden
    prune_golden
}

# --- the golden mode: run by a deploy, as root -------------------------------------------------------------
if [ "${1:-}" = golden ]; then
    [ "$(id -u)" -eq 0 ] || { echo "Run as root" >&2; exit 1; }
    ensure_host
    ensure_golden
    prune_golden
    echo "ok: $(basename "$GOLDEN")"
    exit 0
fi

# --- a customer's VM ---------------------------------------------------------------------------------------
LANG_CODE="${1:-}"
MACHINE_ID="${2:-}"
VCPU="${3:-}"
MEMORY_GB="${4:-}"
DISK_GB="${5:-}"
GATEWAY_URL="${6:-}"
TOKEN="${KVIST_MACHINE_TOKEN:-}"

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
if [ "$DISK_GB" -lt "$GOLDEN_DISK_GB" ]; then
    say "Диск меньше образа: нужно не меньше $GOLDEN_DISK_GB ГБ" "Disk smaller than the image: at least $GOLDEN_DISK_GB GB" >&2
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

ensure_host
ensure_golden

# From here on, any failure removes what this run created.
seed_dir="$(mktemp -d)"
cleanup_on_error() {
    virsh destroy "$DOMAIN" > /dev/null 2>&1 || true
    virsh undefine "$DOMAIN" > /dev/null 2>&1 || true
    rm -f "$VM_DISK" "$SEED_ISO"
}
trap 'rm -rf "$seed_dir"' EXIT
trap 'cleanup_on_error' ERR

# --- the seed: cloud-init config and runner config travel on one read-only disk ---------------------------
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

[ -d "$SKEL_DIR" ] && cp -r "$SKEL_DIR" "$seed_dir/skel"

# Everything else is already in the image: this only hands over the customer's config and starts the runner.
cat > "$seed_dir/user-data" << 'EOF'
#cloud-config
ssh_pwauth: false
disable_root: true
runcmd:
  - mount -o ro /dev/disk/by-label/cidata /mnt
  - install -m 600 -o kvist -g kvist /mnt/runner.json /etc/kvist-runner/runner.json
  - if [ -d /mnt/skel ]; then cp -r /mnt/skel/. /home/kvist/.kvist/ && chown -R kvist:kvist /home/kvist/.kvist; fi
  - umount /mnt
  - systemctl start kvist-runner
EOF

genisoimage -quiet -output "$SEED_ISO" -volid cidata -joliet -rock "$seed_dir"

# --- disk and domain ------------------------------------------------------------------------------
say "Создаю VM..." "Creating VM..."
qemu-img create -f qcow2 -F qcow2 -b "$GOLDEN" "$VM_DISK" "${DISK_GB}G" > /dev/null
# The seed goes in as a virtio disk, not a cdrom: Debian's cloud kernel has no SATA or CD-ROM drivers, so a cdrom is
# invisible to the guest, cloud-init finds no datasource and switches itself off (no network, no runner).
virt-install --name "$DOMAIN" --import \
    --vcpus "$VCPU" --memory "$((MEMORY_GB * 1024))" \
    --disk "path=$VM_DISK" --disk "path=$SEED_ISO,device=disk,bus=virtio,format=raw,readonly=on" \
    --os-variant debian12 --network network=default \
    --graphics none --noautoconsole
virsh autostart "$DOMAIN" > /dev/null

say "Готово: $DOMAIN. Раннер сам подключится к шлюзу." "Done: $DOMAIN. The runner will dial the gateway by itself."
