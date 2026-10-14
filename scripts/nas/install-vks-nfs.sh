#!/usr/bin/env bash
# =============================================================================
# install-vks-nfs.sh — 在 air-gap 的 NFS VM（Ubuntu 22.04 jammy，VLAN 7）上執行
#   1. 從 ./debs 離線安裝 nfs-kernel-server
#      🔑 只裝「沒裝或比現有版本新」的 deb：bundle 裡含 libc6 / dpkg 等基底套件，
#         新版 cloud image 已經比 bundle 新，全裝會把系統套件降版
#   2. 建兩個 export（只開放 VLAN 7）：
#        /export/vks-ds   → ESXi NFS datastore（做法 1：vSphere CSI，PV = NAS 上的 VMDK）
#        /export/vks-rwx  → VKS 節點 eth1 直接掛（做法 2：NFS CSI driver，RWX）
#      no_root_squash：ESXi NFS v3 與 NFS CSI driver 都以 root 建立子目錄
# 用法（root）：./install-vks-nfs.sh [ALLOWED_CIDR]   預設 192.168.17.0/24
# =============================================================================
set -euo pipefail
ALLOWED_CIDR="${1:-192.168.17.0/24}"
OPTIONS="rw,sync,no_subtree_check,no_root_squash"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEBDIR="${SCRIPT_DIR}/debs"
[ "$(id -u)" -eq 0 ] || { echo "!! run as root" >&2; exit 1; }

echo "==> [1/4] offline install nfs-kernel-server (only missing / newer debs)"
if dpkg -l nfs-kernel-server 2>/dev/null | grep -q '^ii'; then
  echo "    already installed"
else
  NEED=()
  for d in "${DEBDIR}"/*.deb; do
    p=$(dpkg-deb -f "$d" Package); v=$(dpkg-deb -f "$d" Version)
    iv=$(dpkg-query -W -f='${Version}' "$p" 2>/dev/null || true)
    if [ -z "$iv" ] || dpkg --compare-versions "$iv" lt "$v"; then NEED+=("$d"); fi
  done
  echo "    ${#NEED[@]} of $(ls "${DEBDIR}"/*.deb | wc -l) debs needed:"
  printf '      %s\n' "${NEED[@]##*/}"
  dpkg -i "${NEED[@]}" >/dev/null 2>&1 || true
  dpkg --configure -a >/dev/null 2>&1 || true
  dpkg -l nfs-kernel-server | grep -q '^ii' || { echo "!! nfs-kernel-server not installed" >&2; dpkg -i "${NEED[@]}"; exit 1; }
fi
echo "    nfs-kernel-server $(dpkg-query -W -f='${Version}' nfs-kernel-server)"

echo "==> [2/4] export dirs"
for d in /export/vks-ds /export/vks-rwx; do mkdir -p "$d"; chown root:root "$d"; chmod 0777 "$d"; done

echo "==> [3/4] /etc/exports"
touch /etc/exports
sed -i '\#^/export/vks-#d' /etc/exports
echo "/export/vks-ds  ${ALLOWED_CIDR}(${OPTIONS})"  >> /etc/exports
echo "/export/vks-rwx ${ALLOWED_CIDR}(${OPTIONS})" >> /etc/exports
cat /etc/exports | grep -v '^#'

echo "==> [4/4] enable + verify"
systemctl enable --now rpcbind >/dev/null 2>&1 || true
systemctl enable --now nfs-server
exportfs -ra
exportfs -v
showmount -e localhost
echo "-- internet check (VLAN 7 has no NAT; this should fail) --"
timeout 5 bash -c 'echo > /dev/tcp/8.8.8.8/53' && echo "!! internet reachable" || echo "no internet (expected)"
