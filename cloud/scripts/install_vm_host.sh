#!/usr/bin/env bash
# Makes this server able to host customers' VMs (MACHINE_PROVIDER=libvirt). Debian 12 or 13, as root, from a release:
#
#   scripts/install_vm_host.sh
#
# Installs KVM/libvirt, copies the two scripts that run as root into a root-owned folder (the server's user can
# only ask them over /run/kvist-vm.sock, never edit them), loads the network isolation rules and starts the socket.
# activate_release.sh runs it on every deploy when MACHINE_PROVIDER=libvirt, so it is safe to repeat.
set -Eeuo pipefail

CLOUD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB=/usr/local/lib/kvist-vm
WHEELS=/opt/kvist-cloud/wheels

die() { echo "error: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root"
[[ -c /dev/kvm ]] || die "/dev/kvm is missing: this server cannot run VMs (a VPS needs nested virtualization)"
id kvist-cloud >/dev/null 2>&1 || die "the user kvist-cloud does not exist (see docs/DEPLOY.md)"

if ! command -v virt-install >/dev/null || ! command -v nft >/dev/null || ! command -v genisoimage >/dev/null; then
    echo "installing KVM, libvirt and nftables"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq qemu-system-x86 qemu-utils libvirt-daemon-system virtinst genisoimage nftables dnsmasq-base
fi

install -d -m 755 "$LIB"
install -m 755 "$CLOUD/backend/kvist_cloud/scripts/vm_ctl.py" "$CLOUD/backend/kvist_cloud/scripts/setup_vm.sh" "$LIB/"
# Root-owned, because setup_vm.sh puts these onto every customer's guest.
install -d -m 755 "$WHEELS"

install -d -m 755 /etc/kvist-cloud
install -m 644 "$CLOUD/deploy/vm-firewall.nft" /etc/kvist-cloud/vm-firewall.nft
nft -c -f /etc/kvist-cloud/vm-firewall.nft || die "the firewall rules do not parse"
install -m 644 "$CLOUD/deploy/systemd/kvist-vm.socket" "$CLOUD/deploy/systemd/kvist-vm@.service" \
    "$CLOUD/deploy/systemd/kvist-vm-firewall.service" /etc/systemd/system/
systemctl daemon-reload

systemctl enable --now libvirtd >/dev/null 2>&1
# The firewall first: no VM may ever be up for a moment without its isolation.
systemctl enable kvist-vm-firewall.service >/dev/null 2>&1
systemctl restart kvist-vm-firewall.service
# The default network without DNS (deploy/vm-network.xml). Redefined only when it is not already so: a running
# network with VMs on it must not be torn down on every deploy.
# (Captured into variables: `| grep -q` quits early, virsh gets SIGPIPE and pipefail reports a failure.)
network_xml="$(virsh net-dumpxml default 2>/dev/null || true)"
if [[ "$network_xml" != *"<dns enable='no'/>"* ]]; then
    [[ -z "$(virsh list --all --name)" ]] || die "VMs exist on the old network; move them before changing it"
    virsh net-destroy default >/dev/null 2>&1 || true
    virsh net-undefine default >/dev/null 2>&1 || true
    virsh net-define "$CLOUD/deploy/vm-network.xml" >/dev/null
fi
[[ "$(virsh net-info default | awk '/^Active:/ {print $2}')" == yes ]] || virsh net-start default >/dev/null
virsh net-autostart default >/dev/null
systemctl enable --now kvist-vm.socket >/dev/null 2>&1
# The guest image every customer's VM starts from: built only when the recipe or the wheels changed (a few
# minutes then), so a customer's first boot takes seconds and not the minutes of installing packages.
"$LIB/setup_vm.sh" golden
# Running VMs were made from an older image; bring them up to the same recipe (a stopped one waits for the next deploy).
"$LIB/setup_vm.sh" sync
echo "ok: VM host ready (socket /run/kvist-vm.sock, isolation rules loaded)"
