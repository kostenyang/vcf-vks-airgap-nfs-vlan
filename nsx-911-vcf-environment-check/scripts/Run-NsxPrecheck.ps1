# NSX 4.2.1 -> 9.1.1.0 : upload .mub -> accept EULA -> upgrade UC -> run pre-checks -> dump results. STOPS before any MP/host upgrade.
# 2026-10-22: m06 (Cloud Builder deployed, SDDC Manager powered off) vs m07 (manual, never in VCF), same NSX build 4.2.1.0.0.24304122
#   pwsh Run-NsxPrecheck.ps1 -Nsx 10.0.1.199 -Tag m06
#   pwsh Run-NsxPrecheck.ps1 -Nsx 10.0.1.165 -Tag m07
param([Parameter(Mandatory)][string]$Nsx, [Parameter(Mandatory)][string]$Tag, [int]$WaitStableMin = 0, [string]$ResumeBundleId)
$ErrorActionPreference = 'Stop'
$Mub = 'http://10.0.0.61:8888/PROD/COMP/NSX_T_MANAGER/VMware-NSX-upgrade-bundle-9.1.1.0.0.25691509.mub'
$Out = Join-Path $PSScriptRoot "..\out\$Tag"; New-Item -ItemType Directory -Force $Out | Out-Null
if (-not $env:NSX_PASS) { throw 'set $env:NSX_PASS (NSX admin password)' }
$cred = New-Object PSCredential('admin', (ConvertTo-SecureString $env:NSX_PASS -AsPlainText -Force))
function L($m){ $l = "[{0}] [{1}] {2}" -f (Get-Date -Format 'HH:mm:ss'), $Tag, $m; $l; $l | Out-File -Append "$Out\run.log" }
function Api($method, $path, $body) {
  $p = @{ Uri = "https://$Nsx$path"; Method = $method; Credential = $cred; Authentication = 'Basic'; SkipCertificateCheck = $true; ContentType = 'application/json'; TimeoutSec = 120 }
  if ($body) { $p.Body = ($body | ConvertTo-Json -Depth 8) }
  Invoke-RestMethod @p
}
function Save($name, $obj) { $obj | ConvertTo-Json -Depth 15 | Out-File "$Out\$name.json" -Encoding utf8 }
function Wait-Api($path, $okExpr, $minutes, $label) {
  $d = (Get-Date).AddMinutes($minutes); $last = ''
  while ((Get-Date) -lt $d) {
    Start-Sleep 30
    try { $r = Api GET $path } catch { L "$label poll: $($_.Exception.Message)"; continue }
    $s = & $okExpr $r
    if ($s.text -ne $last) { L "$label $($s.text)"; $last = $s.text }
    if ($s.done) { return $r }
  }
  throw "$label timeout after $minutes min"
}

if ($WaitStableMin) {
  $d = (Get-Date).AddMinutes($WaitStableMin)
  while ((Get-Date) -lt $d) { try { $r = Api GET '/api/v1/cluster/status'; L "cluster overall=$($r.detailed_cluster_status.overall_status)"; if ($r.detailed_cluster_status.overall_status -eq 'STABLE') { break } } catch { L "cluster status: $($_.Exception.Message)" }; Start-Sleep 60 }
}
L "NSX version before: $((Api GET '/api/v1/node/version').product_version)"
Save '00-summary-before' (Api GET '/api/v1/upgrade/summary')

# 1. upload .mub from URL
if ($ResumeBundleId) { $b = @{ bundle_id = $ResumeBundleId }; L "resuming existing bundle upload id=$ResumeBundleId" }
else { $b = Api POST '/api/v1/upgrade/bundles' @{ url = $Mub }; L "bundle upload started id=$($b.bundle_id)" }
$u = Wait-Api "/api/v1/upgrade/bundles/$($b.bundle_id)/upload-status" { param($r) @{ text = "upload status=$($r.status) $($r.percent)% $($r.detailed_status)"; done = $r.status -in 'SUCCESS','FAILED' } } 90 'mub'
Save '01-upload-status' $u
if ($u.status -ne 'SUCCESS') { L 'mub upload FAILED'; exit 2 }

# 2. EULA
if (-not (Api GET '/api/v1/upgrade/eula/acceptance').acceptance) { Api POST '/api/v1/upgrade/eula/accept' | Out-Null; L 'EULA accepted' } else { L 'EULA already accepted' }

# 3. upgrade UC
$sum = Api GET '/api/v1/upgrade/summary'; Save '02-summary-after-upload' $sum
L "summary: UC=$($sum.upgrade_coordinator_version) target=$($sum.target_version) uc_updated=$($sum.upgrade_coordinator_updated)"
if (-not $sum.upgrade_coordinator_updated) {
  Api POST '/api/v1/upgrade?action=upgrade_uc' | Out-Null; L 'UC upgrade started'
  Start-Sleep 60   # UC restarts; API may drop for a while
  $uc = Wait-Api '/api/v1/upgrade/uc-upgrade-status' { param($r) @{ text = "UC upgrade state=$($r.state) $($r.details)"; done = $r.state -in 'SUCCESS','FAILED' } } 45 'uc'
  Save '03-uc-upgrade-status' $uc
  if ($uc.state -ne 'SUCCESS') { L 'UC upgrade FAILED'; exit 3 }
}
$sum = Api GET '/api/v1/upgrade/summary'; Save '04-summary-after-uc' $sum
L "summary: UC=$($sum.upgrade_coordinator_version) target=$($sum.target_version) system=$($sum.system_version)"

# 4. pre-checks
Api POST '/api/v1/upgrade?action=execute_pre_upgrade_checks' | Out-Null; L 'pre-upgrade checks started'
Start-Sleep 30
$st = Wait-Api '/api/v1/upgrade/status-summary' { param($r)
  $pc = $r.component_status | ForEach-Object { "$($_.component_type):$($_.pre_upgrade_status.status)" }
  @{ text = "pre-check " + ($pc -join ' '); done = -not ($r.component_status | Where-Object { $_.pre_upgrade_status.status -in 'IN_PROGRESS','NOT_STARTED' -and $_.component_type -ne 'FINALIZE_UPGRADE' }) } } 60 'precheck'
Save '05-status-summary' $st
$f = Api GET '/api/v1/upgrade/pre-upgrade-checks/failures'; Save '06-precheck-failures' $f
L "pre-check failures/warnings: $($f.result_count)"
foreach ($x in $f.results) { L ("  [{0}] {1} {2} ({3}) needs_ack={4} : {5}" -f $x.type, $x.source_type, $x.source_id, $x.message.error_code, $x.needs_ack, ($x.message.message -replace '\s+',' ').Substring(0,[Math]::Min(220,($x.message.message -replace '\s+',' ').Length))) }
L 'DONE (stopped after pre-checks; no MP/host upgrade started)'
