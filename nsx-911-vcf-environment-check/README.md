# NSX 4.2.x → 9.1.1 升級預檢 `vcfEnvironmentCheck`（31944）：檢查什麼、怎麼查、lab 怎麼驗證

> ⚠️ **Lab only / 官方不支援。** 本文的 watermark 寫入是為了在 lab 釐清預檢邏輯。
> 正式環境的獨立（standalone）NSX 要升 9.x，官方路徑是**先 import / converge 進 VCF**（KB440630），再用 VCF 的 lifecycle 升級。
> 不要在正式環境偽造 watermark。

**目錄**
1. 現象
2. 預檢在檢查什麼（拆 Upgrade Coordinator 9.1.1 實證）
3. 對照實驗：Cloud Builder 部 vs 手動部
4. curl 做法
5. 還原
6. 腳本

## 1. 現象

獨立部署的 NSX Manager（沒有經過 VCF 部署）上傳 9.1.1 升級套件、Upgrade Coordinator（UC）升到 9.1.1 之後跑 Pre-Checks，MP 會出現：

```
[FAILURE] 31944  needs_ack=False
Upgrade of standalone NSX deployment to VCF 9.X is not supported. Please import your NSX deployment
into VCF before proceeding with the upgrade. Minimum version for importing into VCF is NSX 4.1.0.2+.
```

`needs_ack=False` → UI 不能 acknowledge，MP 升級直接被擋。

## 2. 預檢在檢查什麼

- 實作：`/opt/vmware/upgrade-coordinator-tomcat/webapps/upgrade-coordinator/WEB-INF/lib/libnsx-upgrade-plugins.jar`
  → `com.vmware.nsx.management.upgrade.plugin.mp.task.VcfEnvironmentInspectionTask`
- `execute()` 只做一件事：`UpgradePersistenceService.getNsxManagerDeploymentInfo()`
  讀 NSX 資料庫裡的 `NsxManagerDeploymentInfo`；讀不到 → log「Upgrade to 9.x is blocked for non vCF environments」
  → `FAILURE NOT_VCF_ENVIRONMENT`（31944）。沒有讀其他欄位。
- 這筆資料對應的 NSX API = **`/api/v1/watermark`**
  （schema `NSXManagerDeploymentInfo`，API 文件描述「Deployment Details of NSX Manager deployed via VCF」；
  必填 `instance_id`、`managed_by`、`log_token`、`deployed_by`；支援 GET / POST / PUT / DELETE）
- 由 Cloud Builder / SDDC Manager 在部署 NSX 時寫入。只看 NSX 自己的資料庫，**SDDC Manager 有沒有開機不影響**。

## 3. 對照實驗（同一個 NSX build 4.2.1.0.0.24304122，目標 9.1.1.0.0.25691509）

| 項目 | A：Cloud Builder 5.2.1 bring-up 部的 | B：手動部（vcsa-deploy + ovftool，從未進 VCF） |
|---|---|---|
| `GET /api/v1/watermark` | 200（見下） | **404** error 600「Deployment Info Details could not be found」 |
| 9.1.1.0 Pre-Check | **0 FAILURE**、4 WARNING | **FAILURE 31944** + 同樣 4 WARNING |
| POST watermark 後重跑 | — | **0 FAILURE**、同樣 4 WARNING（31944 消失）|

A 的 watermark（Cloud Builder 寫的）：

```json
{
  "deployed_by": "VMware Cloud Foundation - VSRN",
  "version": "5.2.1.0-24307856",
  "managed_by": "<SDDC Manager FQDN>",
  "instance_id": "<uuid>",
  "log_token": "VCF",
  "resource_type": "NSXManagerDeploymentInfo"
}
```

兩邊都有的 4 個 WARNING（needs_ack=True，可 acknowledge）：

| code | 內容 |
|---|---|
| 31911 | Context Profile `/infra/context-profiles/FPROT` 含不支援 APP ID（KB410572） |
| 36014 | VCF 9.0 起不支援 NSX-V → NSX 遷移 |
| 31935 | MP API（`/api`）棄用 |
| 31954 | 升級會停用預設 DFW 規則「Malicious IP at Destination Rule」 |

## 4. curl 做法

```bash
NSX=<nsx-manager-ip-or-vip>
read -s -p "NSX admin password: " P; echo

# 查（404 = 獨立部署，會被 31944 擋）
curl -sk -u "admin:$P" https://$NSX/api/v1/watermark

# 寫一筆（欄位照 Cloud Builder 寫的格式；UC 只檢查有沒有這筆）
curl -sk -u "admin:$P" -X POST -H 'Content-Type: application/json' \
  https://$NSX/api/v1/watermark -d '{
    "deployed_by": "VMware Cloud Foundation - VSRN",
    "version": "5.2.1.0-24307856",
    "managed_by": "sddcm.example.local",
    "instance_id": "'"$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid)"'",
    "log_token": "VCF"
  }'

# 重跑預檢（套件已上傳、UC 已是 9.1.1 時）
curl -sk -u "admin:$P" -X POST https://$NSX/api/v1/upgrade?action=execute_pre_upgrade_checks
curl -sk -u "admin:$P" https://$NSX/api/v1/upgrade/status-summary
curl -sk -u "admin:$P" https://$NSX/api/v1/upgrade/pre-upgrade-checks/failures
```

## 5. 還原

```bash
curl -sk -u "admin:$P" -X DELETE https://$NSX/api/v1/watermark -w 'HTTP %{http_code}\n'
curl -sk -u "admin:$P" https://$NSX/api/v1/watermark     # 應回 404
```

## 6. 腳本

| 檔案 | 用途 |
|---|---|
| `scripts/watermark-curl.sh` | `get` / `post` / `precheck` / `status` / `failures` / `delete`（`NSX_PASS`、`NSX` 環境變數） |
| `scripts/Run-NsxPrecheck.ps1` | 走跟 UI 一樣的 UC 流程：上傳 .mub（URL）→ EULA → upgrade UC → pre-checks → 存 JSON；**到 pre-check 就停，不升 MP/host**（`$env:NSX_PASS`） |

注意：
- 上傳 .mub 會把 UC 升到 9.1.1（manager 服務重啟約 5 分鐘，期間 API 回 500）。
- nested lab 的 NSX 放在慢碟的 vSAN 上，`Extracting Bundle Signature` 可能 IO 卡很久（iowait 70%+），不是當機。
- 上傳中 NSX 服務重啟時，重送上傳會回 30902「Another upgrade bundle upload is in progress」→ 用原 bundle id 繼續追（`-ResumeBundleId`）。
