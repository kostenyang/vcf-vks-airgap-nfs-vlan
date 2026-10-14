# 量某段時間內 VM 的網路與虛擬磁碟流量(vCenter realtime 效能計數器,20 秒一點)
#   用法:.\Measure-VmTraffic.ps1 -Start '2026-10-14 00:30:00' -End '2026-10-14 00:31:00' -VM vcf-m06-vna01a,<worker VM>
#   $env:VCPASS = m06 SSO 密碼
param([datetime]$Start, [datetime]$End, [string[]]$VM)
'Sdk','Core' | ForEach-Object { Import-Module "VMware.VimAutomation.$_" -RequiredVersion '13.5.1.25718932' -ErrorAction SilentlyContinue | Out-Null }
Set-PowerCLIConfiguration -InvalidCertificateAction Ignore -ParticipateInCEIP $false -Confirm:$false -Scope Session | Out-Null
$vc = Connect-VIServer vcf-m06-vc01.home.lab -User 'administrator@vsphere.local' -Password $env:VCPASS -Force -WarningAction SilentlyContinue
# 本機時鐘和 vCenter 不一定同步(lab 的 AD/DNS 主機快約 3 分鐘)→ 換算成 vCenter 時間
$skew = (Get-View ServiceInstance).CurrentTime().ToLocalTime() - (Get-Date)
$Start = $Start + $skew; $End = $End + $skew
'clock skew (vCenter - local): {0:N0} s;window(vCenter time){1:HH:mm:ss} -> {2:HH:mm:ss}' -f $skew.TotalSeconds, $Start, $End
$stats = 'net.received.average', 'net.transmitted.average', 'virtualDisk.write.average'
foreach ($n in $VM) {
  $v = Get-VM -Name $n
  $s = Get-Stat -Entity $v -Stat $stats -Realtime -Start $Start.AddSeconds(-20) -Finish $End.AddSeconds(20) -ErrorAction SilentlyContinue
  # 網路:各 instance 分別加總後取最大的那個(一般 VM 記在 vNIC「4000」,VNA 的資料面記在 vmnic0 / vmnic1,
  #       VM 彙總值不含 VNA 資料面;同一筆流量可能同時出現在 vNIC 和 vmnic instance,所以不能全部相加)
  # 磁碟:用 VM 彙總值(instance 空字串)
  $net = { param($k) $m = 0
           $s | Where-Object { $_.MetricId -eq $k -and $_.Instance -ne '' } | Group-Object Instance | ForEach-Object {
             $t = ($_.Group | Measure-Object Value -Sum).Sum; if ($t -gt $m) { $m = $t } }
           [math]::Round($m * 20 / 1024, 1) }
  $disk = [math]::Round((($s | Where-Object { $_.MetricId -eq 'virtualDisk.write.average' -and $_.Instance -eq '' } | Measure-Object Value -Sum).Sum * 20 / 1024), 1)
  '{0,-45} net rx {1,8} MB   net tx {2,8} MB   vdisk write {3,8} MB' -f $n, (& $net 'net.received.average'), (& $net 'net.transmitted.average'), $disk
}
Disconnect-VIServer $vc -Confirm:$false
