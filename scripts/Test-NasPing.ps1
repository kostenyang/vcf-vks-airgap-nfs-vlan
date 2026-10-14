# 各 ESXi 用 vmk3(VLAN 7)ping NAS 192.168.17.20(nested ESX SSH 關著 → Get-EsxCli -V2)
#   $env:VCPASS = m06 SSO 密碼
'Sdk','Core' | ForEach-Object { Import-Module "VMware.VimAutomation.$_" -RequiredVersion '13.5.1.25718932' -ErrorAction SilentlyContinue | Out-Null }
Set-PowerCLIConfiguration -InvalidCertificateAction Ignore -ParticipateInCEIP $false -Confirm:$false -Scope Session | Out-Null
$vc = Connect-VIServer vcf-m06-vc01.home.lab -User 'administrator@vsphere.local' -Password $env:VCPASS -Force -WarningAction SilentlyContinue
foreach ($h in Get-VMHost | Sort-Object Name) {
  $e = Get-EsxCli -VMHost $h -V2
  $a = $e.network.diag.ping.CreateArgs(); $a.host = '192.168.17.20'; $a.interface = 'vmk3'; $a.count = 3
  $r = $e.network.diag.ping.Invoke($a)
  '{0}  vmk3 -> 192.168.17.20  sent {1} / recv {2}  rtt avg {3} us' -f $h.Name, $r.Summary.Transmitted, $r.Summary.Received, $r.Summary.RoundtripAvg
}
Disconnect-VIServer $vc -Confirm:$false
