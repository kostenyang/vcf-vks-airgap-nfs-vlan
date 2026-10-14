# 各 ESXi:NFS datastore 掛載狀態 + 連到 NAS:2049 的 TCP 連線用哪個來源 IP(證明走 vmk3 / VLAN 7)
#   $env:VCPASS = m06 SSO 密碼
'Sdk','Core' | ForEach-Object { Import-Module "VMware.VimAutomation.$_" -RequiredVersion '13.5.1.25718932' -ErrorAction SilentlyContinue | Out-Null }
Set-PowerCLIConfiguration -InvalidCertificateAction Ignore -ParticipateInCEIP $false -Confirm:$false -Scope Session | Out-Null
$vc = Connect-VIServer vcf-m06-vc01.home.lab -User 'administrator@vsphere.local' -Password $env:VCPASS -Force -WarningAction SilentlyContinue
foreach ($h in Get-VMHost | Sort-Object Name) {
  $e = Get-EsxCli -VMHost $h -V2
  "### $($h.Name)"
  $e.storage.nfs.list.Invoke() | ForEach-Object { 'nfs  {0}  {1}:{2}  accessible={3} mounted={4}' -f $_.VolumeName, $_.Host, $_.Share, $_.Accessible, $_.Mounted }
  $e.network.ip.interface.ipv4.get.Invoke() | Where-Object Name -eq 'vmk3' | ForEach-Object { 'vmk3 {0}/{1}' -f $_.IPv4Address, $_.IPv4Netmask }
  $e.network.ip.connection.list.Invoke() | Where-Object { $_.ForeignAddress -like '*:2049' } |
    ForEach-Object { 'tcp  {0} -> {1}  {2}' -f $_.LocalAddress, $_.ForeignAddress, $_.State }
  ''
}
Disconnect-VIServer $vc -Confirm:$false
