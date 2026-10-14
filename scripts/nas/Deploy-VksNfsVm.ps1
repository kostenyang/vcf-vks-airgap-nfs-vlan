<#
  Deploy-VksNfsVm.ps1
  -------------------------------------------------------------------------
  Deploys the Ubuntu 22.04 (jammy) cloud-image OVA to the OUTER vCenter as the
  VKS NAS stand-in on VLAN 7 (lab only; production = your NAS appliance), injecting cloud-init (hostname + root/ubuntu pw +
  static IP) through the OVA's native OVF 'user-data' vApp property.

  Runs on the jumpbox (this machine). Requires: govc.exe.
#>
[CmdletBinding()]
param(
  [string]$Govc      = "E:\9.1\tools\govc.exe",
  [string]$Ova       = "E:\9.1\nfs-offline\vks-nfs\dl\jammy-server-cloudimg-amd64.ova",
  [string]$VmName    = "nfs-vks01",
  [string]$Ip        = "192.168.17.20",
  [string]$Prefix    = "24",
  [string]$Gateway   = "192.168.17.254",
  [string]$Dns       = "10.0.0.200",
  [string]$Domain    = "home.lab",
  [string]$Network   = "VLAN7",
  [string]$Datastore = "ForNFS",
  [string]$Folder    = "VCF",
  [string]$Cluster   = "Cluster",
  [string]$RootPw    = $env:NFSPASS,
  [int]   $DiskGB    = 500,
  [int]   $Cpu       = 2,
  [int]   $MemMB     = 4096
)
$ErrorActionPreference = "Stop"
if (-not $RootPw) { throw "set $env:NFSPASS" }; if (-not $env:VIPASS) { throw "set $env:VIPASS" }
$env:GOVC_URL="https://10.0.0.101"; $env:GOVC_USERNAME="administrator@vsphere.local"
$env:GOVC_PASSWORD=$env:VIPASS; $env:GOVC_INSECURE="1"

function G { & $Govc @args }

if (-not (Test-Path $Ova)) { throw "OVA not found: $Ova" }

# ---- 1. cloud-init user-data (base64) ------------------------------------
$fqdn = "$VmName.$Domain"
$userData = @"
#cloud-config
hostname: $VmName
fqdn: $fqdn
manage_etc_hosts: true
ssh_pwauth: true
disable_root: false
chpasswd:
  expire: false
  users:
    - {name: root,   password: '$RootPw', type: text}
    - {name: ubuntu, password: '$RootPw', type: text}
write_files:
  - path: /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg
    content: |
      network: {config: disabled}
  - path: /etc/netplan/99-static.yaml
    permissions: '0600'
    content: |
      network:
        version: 2
        ethernets:
          id0:
            match: {name: "e*"}
            set-name: eth0
            dhcp4: false
            addresses: [$Ip/$Prefix]
            routes:
              - {to: default, via: $Gateway}
            nameservers:
              addresses: [$Dns]
              search: [$Domain]
runcmd:
  - rm -f /etc/netplan/50-cloud-init.yaml
  - netplan generate
  - netplan apply
"@
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($userData))

# ---- 2. build import spec from the OVA ------------------------------------
Write-Host "==> building import spec"
$spec = & $Govc import.spec $Ova | ConvertFrom-Json
$spec.Name             = $VmName
$spec.DiskProvisioning = "thin"
$spec.PowerOn          = $false
$spec.WaitForIP        = $false
$spec.InjectOvfEnv     = $true
if ($spec.NetworkMapping) { $spec.NetworkMapping[0].Network = $Network }

# fill known OVF properties if the OVA exposes them
foreach ($p in $spec.PropertyMapping) {
  switch ($p.Key) {
    "hostname"     { $p.Value = $VmName }
    "instance-id"  { $p.Value = "$VmName-001" }
    "user-data"    { $p.Value = $b64 }
    "password"     { $p.Value = $RootPw }
    "seedfrom"     { $p.Value = "" }
    "public-keys"  { $p.Value = "" }
  }
}
$specFile = "E:\9.1\nfs-offline\vks-nfs\_import-spec.json"
$spec | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 $specFile
Write-Host "    spec -> $specFile"

# ---- 3. import ------------------------------------------------------------
Write-Host "==> importing OVA as '$VmName' (ds=$Datastore folder=$Folder net=$Network)"
& $Govc import.ova "-options=$specFile" "-ds=$Datastore" "-folder=$Folder" `
    "-pool=/Datacenter/host/$Cluster/Resources" "-name=$VmName" $Ova
if ($LASTEXITCODE -ne 0) { throw "govc import.ova failed ($LASTEXITCODE)" }

# ---- 4. size cpu/mem/disk before power-on ---------------------------------
Write-Host "==> setting $Cpu vCPU / $MemMB MB / ${DiskGB}GB disk"
& $Govc vm.change -vm $VmName -c $Cpu -m $MemMB | Out-Null
# grow the root disk (single-disk import => disk-1000-0). Quote flag=value or
# PowerShell mangles the dotted flag. cloud-init growpart/resizefs runs at first
# boot IF the disk is enlarged before power-on; if enlarged after boot, grow
# online:  echo 1|sudo tee /sys/block/sda/device/rescan; sudo growpart /dev/sda 1; sudo resize2fs /dev/sda1
& $Govc vm.disk.change -vm $VmName "-disk.name=disk-1000-0" "-size=${DiskGB}G" 2>&1 | Out-Null

# ---- 5. power on ----------------------------------------------------------
Write-Host "==> power on"
& $Govc vm.power -on $VmName | Out-Null

Write-Host "==> waiting for $Ip to respond (cloud-init applies static IP on first boot)"
$ok=$false
for ($i=0; $i -lt 60; $i++) {
  if (Test-Connection -TargetName $Ip -Count 1 -Quiet -TimeoutSeconds 1) { $ok=$true; break }
  Start-Sleep -Seconds 10
}
if ($ok) { Write-Host "OK: $VmName is up at $Ip ($fqdn)" -ForegroundColor Green }
else     { Write-Warning "$Ip not responding yet after ~10min. Check VM console via vCenter." }
