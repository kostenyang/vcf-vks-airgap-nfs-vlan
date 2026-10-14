# VCF 9.1.1 + Air-gap + VPC（VNA）+ VKS 掛 NAS 走獨立 VLAN — 從 bring-up 到驗證（UI step by step）

> 📄 Word 版：[`VCF-VKS-Airgap-NFS-VLAN-Step-by-Step.docx`](VCF-VKS-Airgap-NFS-VLAN-Step-by-Step.docx)

一份從頭到尾的實作文件：

1. **VCF 9.1.1 管理網域，不部署 VCF Management Services（VCFMS）**
2. **VLAN-backed VPC（Distributed Transit Gateway）+ VNA** 啟用 Supervisor、建立 VKS 叢集
3. **Air-gap**：image 只從外部 Harbor 取得，VKr 從本機檔案匯入
4. **VKS 的 Pod 直接掛 NAS（NFS CSI，RWX）**：VKS 節點多一張 **secondary NIC（eth1）接 NAS 所在的 VLAN 7**，NFS 流量**不經過 VNA**

vSphere / NSX / Installer 的設定全部在 UI 操作並截圖；只有中止 bring-up、NAS 主機安裝、一個 NSX API（VLAN 延伸 subnet）、Kubernetes 內的安裝與驗證用 CLI。所有數值都是 lab 實際值，換到客戶環境時對照 [lab 值 ↔ 客戶環境對照表](#lab-值--客戶環境對照表)。

| 重點 | 做法 | 章節 |
|---|---|---|
| 不部署 VCFMS | VCF Installer bring-up 跑完 NSX，在 VCF Management Platform 開始時中止 | Part A |
| NAS | 獨立 VLAN 7，export NFS 給 VKS 節點 eth1 的網段 | Part B |
| VPC + VNA | Distributed VLAN Connection（VLAN 6）+ VNA cluster；Supervisor 用 VPC | C-1 ~ C-6 |
| Air-gap | Local Content Library 匯入 VKr；Supervisor **Container Registries** 下發 Harbor CA；VLAN 6 / 7 都沒有 NAT | C-2、C-4 |
| **VKS eth1 接 VLAN 7** | NSX External Connection **Dedicated to Subnet（Layer 2）** → VPC 的 **VLAN 延伸 subnet**（API 開 static IP）→ 分享給 namespace → 叢集精靈 **ADD ADDITIONAL NETWORK** | C-7 ~ C-11 |
| NFS CSI | csi-driver-nfs（image 在 Harbor）、StorageClass `nas-nfs-rwx`（RWX） | C-12 |
| 驗證 | Harbor 拉得到、docker.io 拉不到；RWX 跨兩台 worker；Pod 寫 1 GiB：**eth1 送出 1080 MB、VNA 0.7 MB** | C-13 |

**目錄**

- [架構](#架構)
- [環境](#環境)（含 [lab 值 ↔ 客戶環境對照表](#lab-值--客戶環境對照表)）
- **Part A：VCF bring-up（不部署 VCFMS）**：A-1 Installer 精靈、A-2 中止 bring-up、A-3 VKS 前置準備
- **Part B：NAS**：B-1 NFS export（VLAN 7）
- **Part C：VPC / VNA + VKS + eth1 掛 NAS**：C-1 TGW + VNA、C-2 VKr、C-3 Supervisor、C-4 Harbor 信任、C-5 library + Namespace、C-6 SubnetSet、C-7 NSX VLAN 7 連線、C-8 IP block、C-9 VLAN 延伸 subnet、C-10 分享給 Namespace、C-11 VKS 叢集 + eth1、C-12 NFS CSI、C-13 驗證
- [時間](#時間)、[遇到的問題](#遇到的問題)、[參考](#參考)

## 架構

![](img/00-architecture.png)

（圖的原始檔：[`img/diagram/architecture.html`](img/diagram/architecture.html)）

| 流量 | 路徑 | 經過 VNA？ |
|---|---|---|
| Pod 對外、拉 Harbor image | 節點 **eth0** → VPC public subnet → Distributed Transit Gateway（在 ESXi 上）→ VLAN 6 | ❌（C-13-5 對照組 C 實測） |
| LoadBalancer VIP | VLAN 6 → **VNA**（LB）→ 節點 | ✅（C-13-5 對照組 B 實測） |
| **Pod 掛的 NFS（RWX PV）** | 節點 **eth1** → VPC 的 VLAN 延伸 subnet（Layer 2）→ VLAN 7 → NAS | ❌（C-13-5 實測） |
| Supervisor / VNA 管理 | 管理網段 | ❌ |

### 為什麼 NFS 不會經過 VNA

1. VKS 節點有兩張網卡：**eth0** 在 VPC 的 public SubnetSet（VLAN 6 那一側），**eth1** 在 `nas-vlan7` subnet，和 NAS 同一段 192.168.17.0/24。節點的路由表裡 192.168.17.0/24 直接走 eth1，不經閘道（C-13-1）。
2. `nas-vlan7` subnet 是 **VLAN 延伸（Layer 2）**：它背後的 External Connection 是 「Dedicated to Subnet」，**不接 Transit Gateway**（connectivity DISCONNECTED），所以既不經過 DTGW 的路由，也不經過 VNA。
3. VCF 9.1 的 VPC：north-south 由每台 ESXi 上的 **Distributed Transit Gateway** 直接轉送（不需要 Edge）；**VNA 只提供 LB、NAT 等有狀態服務**。只有用到這些服務的流量才經過 VNA。
4. NFS CSI 在節點上掛載 NFS，掛載的 `clientaddr` 就是節點的 eth1（C-13-3）；NAS 上看到的連線來源也只有 eth1 的 IP。

> ℹ️ 如果沒有 eth1，Pod 掛 NFS 會從 eth0 走 VPC → VLAN 6 閘道 → 路由器 → VLAN 7，多繞一段，也受 VPC 的對外規則影響。VCF 9.1 讓 VKS 節點與 Pod 加 secondary NIC、對應到 VPC 的 VLAN 或 subnet，用途之一就是直接存取 NFS storage（見[參考](#參考)）。

## 環境

| 項目 | 值 |
|---|---|
| 管理網域 | `vcf-m06`：vCenter `vcf-m06-vc01`（10.0.1.198）、NSX `vcf-m06-nsx01`（10.0.1.199）、SDDC Manager `vcf-m06-sddcm01`、vSAN OSA，3 台 nested ESX 9.1.0（10.0.1.195-197） |
| **沒有部署** | VCF Management Services（VSP）、VCF Operations、Fleet、VCF Automation |
| VPC 外部網路 | **VLAN 6**，192.168.16.0/24，閘道 .254，External IP Block `vks-block`（排除 .240-.254） |
| **NAS 網路** | **VLAN 7**，192.168.17.0/24：NAS .20、VKS 節點 eth1 .64 起（NSX 配，.1-.63 排除），閘道 .254（沒有 NAT） |
| NAS | Ubuntu 22.04（離線安裝 nfs-kernel-server），export `/export/vks-rwx` 給 192.168.17.0/24，`no_root_squash` |
| 外部 Harbor | `harbor01.home.lab`（10.0.0.86），Harbor v2.15.4，自簽 CA，public project `vks-apps`、`csi` |
| VNA | `vks-vna`，1 台 `vcf-m06-vna01a`（10.0.1.209），Large |
| Supervisor 管理 IP | 10.0.1.222-226 |
| DNS / NTP | 10.0.0.200 / 10.0.1.254 |

DNS（A + PTR）要先建好。即使 VCFMS、Ops 等元件不會部署，精靈驗證仍要求它們的 FQDN 能正反解：

| FQDN | IP | 會不會部署 |
|---|---|---|
| `vcf-m06-esx01..03` | 10.0.1.195-197 | ✅ |
| `vcf-m06-vc01` | 10.0.1.198 | ✅ |
| `vcf-m06-nsx01`（VIP）/ `vcf-m06-nsx01a` | 10.0.1.199 / .200 | ✅ |
| `vcf-m06-sddcm01` | 10.0.1.201 | ✅ |
| `vcf-m06-ops01` / `opsc01` / `lic01` / `fleet01` / `vsp01` / `shared01` / `vidb` | 10.0.1.202-208 | ❌ 只為了通過驗證 |
| VCF management services IP pool（12 個） | 10.0.1.210-221 | ❌ 只為了通過驗證 |
| `vcf-m06-vna01a`（VNA） | 10.0.1.209 | ✅（C-2） |
| Supervisor 管理 IP | 10.0.1.222-226 | ✅（C-4） |

### lab 值 ↔ 客戶環境對照表

| 項目 | lab 值 | 客戶環境要準備 / 填寫 |
|---|---|---|
| DNS / NTP | 10.0.0.200 / 10.0.1.254 | 內部 DNS（A + PTR）、NTP |
| 管理網段 | 10.0.0.0/23，gw 10.0.0.1 | ESXi / vCenter / NSX / SDDC Manager 管理網段 |
| ESXi ×3 | 10.0.1.195-197 | 管理網域主機 |
| vCenter / NSX VIP / NSX 節點 / SDDC Manager | 10.0.1.198 / .199 / .200 / .201 | 各 1 個 IP + FQDN |
| VCFMS / Ops 等 FQDN（只為通過驗證，不會部署） | 10.0.1.202-208 + IP pool 10.0.1.210-221 | 仍需 A + PTR（精靈必填） |
| VNA 節點 | 10.0.1.209（管理網段） | 每個 VNA 節點 1 個管理 IP + FQDN |
| Supervisor 管理 IP | 10.0.1.222-226（5 個連續） | 管理網段 5 個連續 IP |
| VPC 外部網路 | **VLAN 6**，192.168.16.0/24，gw .254 | VPC 對外 VLAN + 閘道 |
| External IP Block | `vks-block` = 192.168.16.0/24，排除 .240-.254 | LB VIP / Supervisor API / 節點 eth0 用的 IP 段 |
| **NAS 網路** | **VLAN 7**，192.168.17.0/24，gw .254 | NAS 所在 VLAN、閘道（NSX External Connection 的 Gateway CIDR 要和實體閘道一致） |
| NAS | 192.168.17.20，`/export/vks-rwx`（NFS 4.1） | 客戶 NAS 的 IP / export path；export 給 eth1 網段、`no_root_squash` |
| VKS 節點 eth1 | 192.168.17.64 起（NSX IP block `nas-vlan7-block` 排除 .1-.63） | VLAN 7 裡留一段給 VKS 節點（每個節點 1 個，含 control plane） |
| NSX 物件 | External Connection `nas-vlan7`、subnet `nas-vlan7-sn`（VPC `vks-lab_cw1ha`）、Subnet CR `nas-vlan7` | 依客戶命名規則；VPC 名稱由 namespace 自動產生 |
| Harbor | `harbor01.home.lab`（10.0.0.86），自簽 CA，project `vks-apps`、`csi` | 客戶 registry FQDN、CA 憑證、project |
| VKr | `photon-5-amd64-v1.36.2---vmware.2-vkr.3`（11.87 GB） | 依 VKS 版本挑 VKr，搬進內網 |
| NFS CSI | csi-driver-nfs v4.13.4（6 個 image） | 依 Kubernetes 版本挑版本，image 推進客戶 registry |
| 名稱 | `vcf-m06-*`、`vks-lab`、`vks-cl01`、`nas-nfs-rwx` | 依客戶命名規則 |

## Air-gap 的範圍

| 網段 | 對外 | 說明 |
|---|---|---|
| VLAN 6（Supervisor 工作網路、VKS 節點、LB VIP） | ❌ 本來就出不去 | 上游閘道沒有替這段做 NAT |
| VLAN 7（NAS、VKS 節點 eth1） | ❌ 本來就出不去 | 上游閘道沒有替這段做 NAT |
| Harbor VM | ❌ 用 iptables 擋 | 只放行私有網段 |
| 管理網段（Installer、vCenter、ESX、NSX、SDDC Manager） | ⚠️ 這個 lab 沒有擋 | 所有內容都只從內部來源取得（內部 depot、本機檔案、Harbor）。**正式 air-gap 環境這段也要斷** |

# Part A：VCF bring-up（不部署 VCFMS）

## A-1 VCF Installer 精靈（UI）

Installer 首頁：depot 已連到內部 offline depot，9.1.1 的元件都已下載。按 **DEPLOYMENT WIZARD → VMware Cloud Foundation**。

![](img/0-bringup/01-installer-home.png)
![](img/0-bringup/02-wizard-menu.png)
![](img/0-bringup/03-intro-about.png)

**Deployment Paths** 選 **Deploy a new VCF fleet**。

![](img/0-bringup/04-paths-new-fleet.png)

### Plan

| 步驟 | 設定 |
|---|---|
| 1 Existing Component | 只勾 **I have an existing VCF Automation instance or I will deploy later**（不部署 VCF Automation） |
| 2 Size Options | Deployment model **Simple**、size Small |
| 3 Network Options | **CUSTOMIZE**：VM management network = **Use ESX management network**；VCF management network = Use VM management network；VPC = **VLAN backed VPC**（Gateway Connectivity 只剩 Distributed） |
| 4 Storage | vSAN，architecture **vSAN OSA** |
| 5 Review Prerequisites | 3 hosts / 52 vCPU / 155 GB / 6748 GB / 4 VLANs |

![](img/0-bringup/05-plan-existing-component.png)
![](img/0-bringup/06-plan-size-simple.png)
![](img/0-bringup/07-plan-network-default.png)
![](img/0-bringup/08-plan-network-custom.png)
![](img/0-bringup/09-plan-storage-vsan-osa.png)
![](img/0-bringup/10-plan-prereq-summary.png)

資源需求明細：其中 **VCF management services 就佔 28 vCPU / 58 GB / 2900 GB**，這次不會部署，但精靈仍以完整需求檢查。

![](img/0-bringup/11-plan-prereq-resources.png)
![](img/0-bringup/12-plan-prereq-vlans.png)
![](img/0-bringup/13-plan-prereq-fqdns.png)

### Prepare

| 步驟 | 設定 |
|---|---|
| 1 General Information | 9.1.1.0、instance `vcf-m06-instance`、domain `vcf-m06`；DNS / NTP / suffix；**CEIP 關閉、Autogenerate passwords 關閉** |
| 2 Hosts | 3 台 FQDN + root 密碼 → **CONFIRM ALL FINGERPRINTS** → NEXT 跳出 「Resource Requirements Warning」（資源夠，但沒有 20% 餘裕）→ **YES, PROCEED** |
| 3 Networks | ESX mgmt VLAN 0 / `10.0.0.1/23`；VCF Management Services IP Pool `10.0.1.210-10.0.1.221`；vMotion / vSAN VLAN 0、`192.168.65.1/24` / `192.168.66.1/24`、各 .11-.20 |
| 4 VCF Management | Ops `vcf-m06-ops01`、Cloud Proxy `vcf-m06-opsc01`、License Server `vcf-m06-lic01`、Fleet / Instance / Identity Broker / services runtime FQDN（**全部不會部署**，只是必填） |
| 5 vCenter | `vcf-m06-vc01`、datacenter `vcf-m06-dc01`、cluster `vcf-m06-cl01`、`vsphere.local` |
| 6 Storage | vSAN OSA，**FTT 固定為 1**（UI 不能改） |
| 7 Distributed Switch | **Default** profile（一個 VDS、MTU 9000、vmnic0/1）；VLAN backed VPC **沒有 TEP 欄位** |
| 8 NSX Manager | VIP `vcf-m06-nsx01`、appliance `vcf-m06-nsx01a` |
| 9 SDDC Manager | `vcf-m06-sddcm01`（Installer 不在這三台主機上，所以會另部一台 SDDC Manager） |

![](img/0-bringup/14-prepare-general.png)
![](img/0-bringup/15-prepare-hosts-fingerprints.png)
![](img/0-bringup/16-prepare-hosts-resource-warning.png)
![](img/0-bringup/17-prepare-networks.png)
![](img/0-bringup/18-prepare-vcf-management.png)
![](img/0-bringup/19-prepare-vcenter.png)
![](img/0-bringup/20-prepare-storage.png)
![](img/0-bringup/21-prepare-vds-default.png)
![](img/0-bringup/22-prepare-nsx.png)
![](img/0-bringup/23-prepare-sddcm.png)

> 🔑 選 **VLAN backed VPC** 時，Networks 頁**沒有** Transit Gateway / 外部網路的欄位，Day-0 只會建立預設 Transit Gateway；外部連線（VLAN、閘道、IP block、VNA）在 bring-up 之後才從 vCenter UI 設定（C-2）。

### Deploy

![](img/0-bringup/24-deploy-review-summary.png)
![](img/0-bringup/25-deploy-review-json.png)

驗證 15 項，3 個 Warning，全部按 **ACKNOWLEDGE ALL WARNINGS**：

| Warning | 原因 |
|---|---|
| vMotion / vSAN 閘道 `.1` 沒有回應 | 這兩段 lab 不路由，正常 |
| 「Insufficient Storage. 5400 GB storage is less than the required 6748 GB」 | 6748 GB 含 VCFMS 的 2900 GB；FTT=1 時 3 × 3600 GB 只算一半。VCFMS 不會部署，所以可以忽略 |
| 建議至少 65 cores | nested lab |

![](img/0-bringup/26-deploy-validate-result-1.png)
![](img/0-bringup/28-deploy-acknowledged.png)

**DEPLOY**：

![](img/0-bringup/29-deploy-started.png)

## A-2 在 VCF Management Platform 開始時中止 bring-up（CLI）

9.1.1 greenfield 的 milestone 順序：

1. Deploy vCenter
2. Deploy SDDC Manager
3. Configure vSphere cluster
4. Deploy and configure NSX
5. **Deploy and configure VCF Management Platform** ← 第一個 subtask #271/#272 「Deploy VCF Management Services via SDDC Manager」 就是 VSP bootstrap
6. Deploy and configure the operations appliance
7. Deploy and configure VCF Management Services

在第 5 個 milestone 一開始就中止，前 4 個完成的東西（vCenter、SDDC Manager、vSAN、NSX）都保留。

```bash
# 觸發時（#272 Deploy VCF Management Services via SDDC Manager 開始執行）
systemctl stop domainmanager       # VCF Installer：不再往下跑
systemctl restart domainmanager    # SDDC Manager：中斷剛送出的 VSP_BOOTSTRAP 任務
# 刪掉 inner vCenter 上 VSP 相關的 VM（這次還沒建立就被中斷，沒有東西要刪）
systemctl start domainmanager      # VCF Installer：恢復服務，保留可重用
```

這次用監看程式 [`scripts/abort_watcher.py`](scripts/abort_watcher.py) 自動執行（每 10 秒查 `/v1/sddcs/{id}`；加了兩道保險：NSX milestone 必須已完成、`PREVALIDATION_*` 狀態不算開始）。紀錄見 [`cli/01-abort-watcher.log`](cli/01-abort-watcher.log)：

```text
2026-10-13 14:09:32 >>> TRIGGER: [('Update VCF Management Services Deployment Status', 'POSTVALIDATION_COMPLETED_WITH_SUCCESS'), ('Deploy VCF Management Services via SDDC Manager', 'POSTVALIDATION_COMPLETED_WITH_SUCCESS'), ('Monitor VCF Management Services Deployment Task', 'IN_PROGRESS')]
2026-10-13 14:09:32 step 1: stop domainmanager on installer 10.0.1.135
2026-10-13 14:09:36   systemctl is-active → failed（已停止）
2026-10-13 14:09:36 step 2: restart domainmanager on SDDC Manager 10.0.1.201(砍掉進行中的 VSP 任務)
2026-10-13 14:09:47   systemctl is-active → active
2026-10-13 14:09:47 step 3: 等 60 秒,刪除 inner vCenter 上 VSP 相關 VM
2026-10-13 14:10:47   VSP-looking VMs: none
2026-10-13 14:10:47 step 4: start domainmanager on installer 10.0.1.135
2026-10-13 14:10:55   systemctl is-active → active
2026-10-13 14:10:55 ABORT COMPLETE — 之後 30 分鐘每 2 分鐘確認沒有重送 / 沒有新的 VSP VM
（post-check：之後 30 分鐘每 2 分鐘確認一次）
2026-10-13 14:12:56   post-check installer status=COMPLETED_WITH_FAILURE running=[] vsp_vms=[]
2026-10-13 14:41:13   post-check installer status=COMPLETED_WITH_FAILURE running=[] vsp_vms=[]
2026-10-13 14:41:13 POST-CHECK DONE
```

中止後 Installer 顯示前 4 個 milestone 完成、VCF Management Platform 失敗（4/14）。**不要按 RETRY AND PROCEED WITH DEPLOYMENT**，否則會繼續部署 VSP。

![](img/0-bringup/30-deploy-aborted.png)

inner vCenter 只有 vCenter、SDDC Manager、NSX 三台 VM：

![](img/0-prep/01-after-abort-vms.png)

## A-3 VKS 前置準備（UI）

### A-3-1 vSphere Client 語言改成 English（🔴 一定要做）

右上角使用者 → **My Preferences → Language → Specify a language → English** → SAVE。Namespace → Resources（LCI 外掛）只有 en / es / fr / ja 四種語系，用繁體中文介面會整片空白。

![](img/0-prep/02-pref-language-english.png)

### A-3-2 叢集 HA / DRS

bring-up 完成的叢集已經是 vSphere HA 開啟、DRS **Fully Automated**（Cluster → Configure → Services）。

![](img/0-prep/03-cluster-drs.png)
![](img/0-prep/04-cluster-ha.png)

### A-3-3 vSAN Health 靜音（nested 才需要）

Cluster → Monitor → **vSAN → vSAN Health**：Unhealthy 有 「NVMe device is VMware certified」、「Performance service status」，Info 有 「vSAN Support Insight」。三項都 **SILENCE ALERT → YES**。

![](img/0-prep/05-cluster-issues.png)
![](img/0-prep/06-vsan-health-findings.png)
![](img/0-prep/07-vsan-silence-dialog.png)
![](img/0-prep/08-vsan-silenced.png)

### A-3-4 VM Storage Policy `vks-ftt0`

**Policies and Profiles → VM Storage Policies → CREATE**：

| 精靈頁 | 設定 |
|---|---|
| Name and description | Name `vks-ftt0`、**K8s Compliant Name `vks-ftt0`**（沒有自動帶出時要自己填） |
| Policy structure | 勾 **Enable rules for "vSAN" storage** |
| vSAN → Availability | 先關掉 **vSAN ESA Auto-RAID (9.1 onwards)**（這套是 OSA）→ Site disaster tolerance 「None - standard cluster」、Failures to tolerate **No data redundancy** |
| vSAN → Advanced Policy Rules | **Force provisioning** 打開 |
| Storage compatibility | `vcf-m06-cl01-ds-vsan01` 相容 |

![](img/0-prep/09-policy-list.png)
![](img/0-prep/10-policy-name.png)
![](img/0-prep/11-policy-structure.png)
![](img/0-prep/12-policy-vsan-availability.png)
![](img/0-prep/13-policy-vsan-advanced-force.png)
![](img/0-prep/14-policy-storage-compat.png)
![](img/0-prep/15-policy-review.png)
![](img/0-prep/16-policy-created.png)

### A-3-5 Port group 安全性（nested 限定）

Supervisor control plane VM 接在 `SDDC-DPortGroup-VM-Mgmt`。nested 環境要把 **Promiscuous mode / MAC address changes / Forged transmits** 改成 **Accept**（Networking → port group → Configure → Policies → EDIT... → Security）。

![](img/0-prep/17-pg-policies-before.png)
![](img/0-prep/18-pg-security-accept.png)
![](img/0-prep/19-pg-policies-after.png)

### A-3-6 確認 VLAN 6 到得了閘道、但出不了網際網路

臨時 port group + vmk 測試，測完就刪：

1. VDS `vcf-m06-cl01-vds01` → ACTIONS → **New Distributed Port Group**：`tmp-vlan6-test`、VLAN **6**

   ![](img/0-prep/20-vds-new-pg-menu.png)
   ![](img/0-prep/21-tmp-pg-vlan6.png)

2. `vcf-m06-esx03` → Configure → VMkernel adapters → **ADD NETWORKING** → VMkernel Network Adapter → `tmp-vlan6-test` → 服務都不勾 → Static `192.168.16.240/24`

   ![](img/0-prep/22-vmk-target-tmp-pg.png)
   ![](img/0-prep/23-vmk-ipv4.png)
   ![](img/0-prep/24-vmk-ready.png)
   ![](img/0-prep/25-vmk-added.png)

3. 從新的 vmk ping 閘道（[`cli/00-vlan6-ping.txt`](cli/00-vlan6-ping.txt)）。bring-up 後 nested ESX 的 SSH 是關的，所以用 PowerCLI `Get-EsxCli -V2` 透過 vCenter 執行同一個 esxcli：

   ```text
   esxcli network diag ping -H 192.168.16.254 -I vmk3 -c 3
   Transmitted : 3   Received : 3   PacketLost : 0
   ```

4. 移除 vmk3（VMkernel adapters → ⋮ → **Remove**），再刪掉 `tmp-vlan6-test`（ACTIONS → **Delete**）（只是確認 VLAN 6 可達的臨時測試）

   ![](img/0-prep/26-vmk-remove.png)

VLAN 6 本身出不了網際網路（[`cli/00-vlan6-no-internet.txt`](cli/00-vlan6-no-internet.txt)：閘道以 192.168.16.254 為來源 ping 8.8.8.8 全部逾時；以管理網段位址為來源則通）。

### A-3-7 DNS

VNA 節點 `vcf-m06-vna01a.home.lab` ↔ 10.0.1.209（A + PTR，[`cli/00-dns-vna.txt`](cli/00-dns-vna.txt)）；Harbor `harbor01.home.lab` ↔ 10.0.0.86。

# Part B：NAS

## B-1 NAS（NFS export，VLAN 7）

Lab 用一台 Ubuntu 22.04 VM 模擬 NAS，接在 VLAN 7。**正式環境就是客戶的 NAS 設備**，只要：

- 在 VLAN 7 上 export 一個 NFS share（lab：NFS 4.1，`/export/vks-rwx`），允許 VKS 節點 eth1 的網段（lab：192.168.17.0/24）讀寫
- 開 `no_root_squash`（NFS CSI driver 以 root 建立每個 PVC 的子目錄）
- 網段閘道 = NSX External Connection 的 Gateway CIDR（lab：192.168.17.254）

Lab 的做法（air-gap 離線安裝）：

```bash
# 1. 有網路的 Ubuntu 22.04 上打包 nfs-kernel-server 及相依 deb（scripts/nas/download-nfs-packages.sh）
./download-nfs-packages.sh                 # → nfs-offline-bundle.tar.gz
```

```powershell
# 2. 外層 vCenter：Ubuntu 22.04 cloud image OVA → cloud-init 設靜態 IP 192.168.17.20/24、gw .254（scripts/nas/Deploy-VksNfsVm.ps1）
$env:VIPASS='...'; $env:NFSPASS='...'
.\Deploy-VksNfsVm.ps1
```

```bash
# 3. NFS VM 上（沒有網路）：離線安裝、建 export（scripts/nas/install-vks-nfs.sh）
#    只裝「沒裝或比較新」的 deb，不會把系統的 libc6 / dpkg 降版
tar -xzf nfs-offline-bundle-jammy.tar.gz && sudo ./install-vks-nfs.sh 192.168.17.0/24
```

結果（[`cli/10-nas-server.txt`](cli/10-nas-server.txt)）：

```text
$ sudo exportfs -v
/export/vks-rwx  192.168.17.0/24(sync,wdelay,hide,no_subtree_check,sec=sys,rw,secure,no_root_squash,no_all_squash)
$ timeout 5 bash -c 'echo > /dev/tcp/8.8.8.8/53' || echo ...
no internet（VLAN 7 沒有 NAT）
```

> ℹ️ `install-vks-nfs.sh` 也會建 `/export/vks-ds`（給 ESXi NFS datastore 用的另一種做法），本文不用。

# Part C：VPC / VNA + VKS + eth1 掛 NAS

## C-1 Transit Gateway + VNA（UI）

**Networks → Transit Gateways**：bring-up 已建立 **Default Transit Gateway**，Connection 是 None → **SETUP NETWORK CONNECTIVITY**：

| 頁 | 設定 |
|---|---|
| 1 Transit Gateway Connectivity | VLAN-backed 只有 **Distributed VLAN Connection**；NEXT 時的 「Networking Prerequisites」 勾 Select All → CONTINUE |
| 2 External Network Connectivity | VLAN ID **6**；Gateway CIDR **192.168.16.254/24**（輸入後按 Enter）；External IP Blocks → **Create New** `vks-block` = `192.168.16.0/24`，Excluded `192.168.16.240-192.168.16.254` |
| 3 VPC Services | VNA cluster `vks-vna`、Form Factor **Large**；ADD 節點 `vcf-m06-vna01a.home.lab`、cluster `vcf-m06-cl01`、vSAN、Static `10.0.1.209/23`、gw 10.0.0.1、port group `SDDC-DPortGroup-VM-Mgmt` |
| 4 Review and Deploy | DEPLOY |

![](img/230-transit-gateways.png)
![](img/231-setup-p1-distributed-vlan.png)
![](img/232-setup-prereq.png)
![](img/233-ipblock-vks-block.png)
![](img/234-setup-p2-external.png)
![](img/235-vna-add-node.png)
![](img/236-vna-cluster-large.png)
![](img/237-setup-review.png)
![](img/238-setup-deploying.png)

> 🔑 這裡**只有 VLAN 6**（VPC 對外）。VLAN 7（NAS）在 C-7 另外建成 「Dedicated to Subnet」 的 External Connection，**不加進 Transit Gateway**。

## C-2 VKr — Local Content Library + 匯入本機檔案（UI）

**Content Libraries → CREATE**：`vkr-local` → **Local content library**（不開 publishing）→ 不套 security policy → storage 選 vSAN（library 放 vSAN 即可，NAS 只放 PV）。

![](img/240-cl-1-name.png)
![](img/241-cl-2-local.png)
![](img/242-cl-3-security.png)
![](img/243-cl-4-storage.png)
![](img/244-cl-5-ready.png)

`vkr-local` → **ACTIONS → Import item** → **Local file** → **UPLOAD FILES** 一次選 4 個檔案（`.ovf`、`-disk1.vmdk`、`.mf`、`.cert`）→ Item name 自動帶 `photon-5-amd64-v1.36.2---vmware.2-vkr.3` → IMPORT。

![](img/245-cl-actions-import.png)
![](img/246-cl-import-local-files.png)

上傳約 8 分鐘（和 VNA 部署同時進行；**上傳期間不要重新整理這個瀏覽器分頁**，上傳是瀏覽器在送），完成後 1 個 OVF template，11.87 GB：

![](img/247-cl-vkr-imported.png)

## C-3 啟用 Supervisor（UI）

VNA 變成 UP 後（這次約 15 分鐘，和 VKr 上傳同時），Transit Gateway 狀態 **Success**（Connection = Distributed VLAN、External Connection = `Distributed-vlan-connection-6`）：

![](img/239-tgw-success.png)

**Supervisor Management → GET STARTED**：

| 精靈頁 | 設定 |
|---|---|
| 1 vCenter Server and Network | **VCF Networking with VPC (recommended)** |
| 2 Supervisor location | **Cluster deployment**：`vcf-m06-sv01`、`vcf-m06-cl01`、zone `vz-01` |
| 3 Storage | Control Plane / Ephemeral Disks / Image Cache 都選 `vks-ftt0`（vSAN） |
| 4 Management Network | Static、`SDDC-DPortGroup-VM-Mgmt`、10.0.1.222-10.0.1.226、255.255.254.0、gw 10.0.0.1、DNS 10.0.0.200、search domain home.lab、NTP 10.0.1.254 |
| 5 Workload Network | NSX Project Default、VPC Connectivity Profile Default、External IP Blocks 自動帶出 `vks-block`；DNS 10.0.0.200、NTP 10.0.1.254 |
| 6 Advanced Settings | Control Plane **Small** |

![](img/350-supervisor-mgmt-home.png)
![](img/351-sv-1-network.png)
![](img/352-sv-2-location.png)
![](img/353-sv-3-storage.png)
![](img/354-sv-4-mgmt-network.png)
![](img/355-sv-5-workload.png)
![](img/356-sv-6-advanced.png)
![](img/357-sv-7-ready.png)
![](img/358-sv-configuring.png)

**30 分鐘**（23:24 → 23:54）變成 **Running**，Control Plane Node Address **192.168.16.3**（中途 kubernetes_status 在 ERROR / WARNING / READY 之間跳動屬正常）：

![](img/359-sv-running.png)

## C-4 讓 Supervisor 和 VKS 信任外部 Harbor（UI）

**Supervisor → Configure → Container Registries → ADD REGISTRY**：Registry Name `harbor01`、Registry host URL `harbor01.home.lab`、TLS Certificate 貼 Harbor 的 **CA 憑證**、帳密留空（`vks-apps` 是 public project）。

![](img/460-sv-container-registries.png)
![](img/461-sv-add-registry.png)
![](img/462-sv-registry-added.png)

Supervisor 建立 `ClusterRegistryConfig`（[`cli/03-cluster-registry-config.txt`](cli/03-cluster-registry-config.txt)），Supervisor Services、Pods、VKS 叢集都會套用，**叢集 YAML 不用另外設 `trust`**。

## C-5 Kubernetes Service library + Namespace（UI）

**Supervisor → Configure → General → Kubernetes Service → ADD** → 勾 `vkr-local` → ADD：

![](img/463-sv-k8s-service-library.png)
![](img/464-sv-add-library-local.png)
![](img/465-sv-library-added.png)

從本機匯入的 VKr 自動產生 KubernetesRelease（[`cli/02-kr.txt`](cli/02-kr.txt)）：

```text
NAME                       VERSION                  READY   COMPATIBLE
v1.36.2---vmware.2-vkr.3   v1.36.2+vmware.2-vkr.3   True    True
```

**Namespaces → NEW NAMESPACE**：Supervisor `vcf-m06-sv01` → Name `vks-lab` → zone `vz-01` → FINISH。

![](img/466-ns-1-location.png)
![](img/467-ns-2-config.png)
![](img/468-ns-3-zone.png)
![](img/469-ns-4-review.png)

Namespace Summary：

- **ADD STORAGE**：勾 **vks-ftt0**（節點磁碟，vSAN）。NAS 不是在這裡加：它由 VKS 裡的 NFS CSI 直接掛（C-12）
- **ADD VM CLASS**：`best-effort-xsmall` / `best-effort-small` / `best-effort-medium`

![](img/470-ns-add-storage.png)
![](img/471-ns-add-vmclass.png)
![](img/472-ns-summary-ready.png)

加完 storage policy，Supervisor 自動產生同名的 StorageClass `vks-ftt0` 和 `vks-ftt0-latebinding`。

## C-6 Public SubnetSet（UI）

Namespace → **Resources** → Network → **GO TO SERVICE** → **SubnetSets → NEW SUBNETSET**：Name `public`、Access Mode **Public**、16 IPs、DHCP None → CREATE（[`cli/04-subnetset.txt`](cli/04-subnetset.txt)）。Network service 頁面也看得到這個 namespace 的 VPC 名稱 **vks-lab_cw1ha**，C-9 會用到。

![](img/480-lci-network-service.png)
![](img/481-lci-new-subnetset.png)
![](img/482-lci-subnetset-created.png)

> ℹ️ Resources 第一次開啟時如果停在別的頁面（例如先前刪掉的叢集），按麵包屑的 `vks-lab` 回到 namespace 首頁。

## C-7 NSX External Connection `nas-vlan7`（NSX UI）

NSX Manager → **Networking → External Connections → ADD EXTERNAL CONNECTION**：

| 欄位 | 值 |
|---|---|
| Name | `nas-vlan7` |
| Type | **Distributed VLAN Connection**，打開 **Dedicated to Subnet**（把這個 VLAN 和它的 CIDR 以 Layer 2 延伸給**單一個** VPC subnet） |
| Subnet Extension Connection | **Layer 2** |
| VLAN ID | **7** |
| Gateway CIDR IPv4 Address | `192.168.17.254/24`（上游閘道，要和實體路由器一致；輸入後按 Enter） |
| VLAN/Subnet Block | `nas-vlan7-block`（CIDR 由 Gateway CIDR 推出 192.168.17.0/24） |

![](img/700-nsx-external-connections.png)
![](img/701-nsx-add-dvc-nas-vlan7.png)
![](img/702-nsx-external-connections-two.png)

存檔後清單有兩個 External Connection：VLAN 6（接在 Transit Gateway 上，Transit Gateway = 1）和 `nas-vlan7`（Dedicated to Subnet = Yes，Transit Gateway = 0）。

> ⚠️ **不要**把 `nas-vlan7` 加到 Transit Gateway 的 External Connection：會報 「subnet extension connection mode as ENABLED_L2 cannot be used for attachment」。Dedicated 的連線是直接給一個 subnet 用的——這也正是 NFS 不經過 Transit Gateway / VNA 的原因。

## C-8 IP block 排除既有設備的 IP（NSX UI）

上一步自動建立了 `nas-vlan7-block`（192.168.17.0/24，Reserved for Specific Subnet）。**Networking → IP Address Pools → IP Address Blocks** → `nas-vlan7-block` → ⋮ **Edit** → Excluded IP Ranges `192.168.17.1-192.168.17.63` → SAVE。NAS（.20）等既有設備都在這段；NSX 配給 VKS 節點 eth1 的 IP 從 **.64** 開始。

![](img/710-nsx-ipblock-exclude.png)
![](img/711-nsx-ipblock-excluded.png)

## C-9 VLAN 延伸 Subnet（NSX API）

在 namespace 的 VPC（`vks-lab_cw1ha`，C-6 的 Network service 頁面看得到）建一個 subnet，`vlan_connection` 指到 `nas-vlan7`，並且**打開 static IP allocation**（VKS 節點的 eth1 靠它拿 IP）：

```bash
curl -k -u "admin:$NSX_PASS" -X PATCH -H 'Content-Type: application/json' \
  https://vcf-m06-nsx01.home.lab/policy/api/v1/orgs/default/projects/default/vpcs/vks-lab_cw1ha/subnets/nas-vlan7-sn \
  -d @manifests/nsx-subnet-nas-vlan7.json
```

[`manifests/nsx-subnet-nas-vlan7.json`](manifests/nsx-subnet-nas-vlan7.json)：`access_mode: Public`、`vlan_connection: /infra/distributed-vlan-connections/nas-vlan7`、`ip_addresses: 192.168.17.0/24`、`ip_blocks` 指到 `nas-vlan7-block`（實際 ID 由 NSX 產生，GET `/policy/api/v1/infra/ip-blocks` 查）、`static_ip_allocation.enabled: true`、`connectivity_state: DISCONNECTED`（Layer 2 延伸的 subnet 不接 VPC gateway）、DHCP 關閉。結果（[`cli/20-nsx-subnet-api.txt`](cli/20-nsx-subnet-api.txt)）：

```text
HTTP 200
status: gateway_address 192.168.17.254/24, network_address 192.168.17.0/24, vlan_extension: {vpc_gateway_connection_enable: false, vlan_id: 7}
```

> 為什麼不用 UI：NSX UI 的 VPC → Subnets → ADD SUBNET 可以建 VLAN 延伸 subnet（VLAN Extension = Yes、Access Mode 選 Public 才列得出 `nas-vlan7`），但**沒有 IP 配置選項**，建出來的 subnet 沒開 static IP allocation，VKS 節點的 eth1 只會有 link-local；這個設定建好之後也改不了。

建好後在 NSX UI 看得到（狀態 Success）：

![](img/720-nsx-vpc-subnet-nas-vlan7.png)

## C-10 把 Subnet 分享給 Namespace（vCenter UI）

Namespace `vks-lab` → **Configure → General → Namespace Networks → ADD**：NSX Project Default、VPC `vks-lab_cw1ha`、Subnet `nas-vlan7-subnet`（**要打字搜尋**才會列出）、Subnet CR Name `nas-vlan7`、Usage 兩個都不勾。

![](img/730-ns-configure-namespace-networks.png)
![](img/731-ns-add-network-nas-subnet.png)
![](img/732-ns-namespace-networks-added.png)

Supervisor 在 namespace 裡建立 `Subnet` CR `nas-vlan7`（`vlanConnectionName: nas-vlan7`、static IP、192.168.17.0/24，[`cli/21-subnet-cr.txt`](cli/21-subnet-cr.txt)）；Resources → Network → **Subnets** 也看得到（VLAN Extension、VLAN ID 7）：

![](img/733-lci-subnets-shared-nas-vlan7.png)

> ⚠️ 直接 `kubectl apply` 帶 `vlanConnectionName` 的 Subnet 會被擋：「spec.vlanConnectionName can only be set by NSX Operator」。一定要先在 NSX 建、再從 vCenter 分享進來。

## C-11 VKS 叢集 + eth1（UI，Custom Configuration）

Resources → Kubernetes → **CREATE CLUSTER** → **Custom Configuration**：

| 精靈頁 | 設定 |
|---|---|
| General | Name `vks-cl01`、ClusterClass `builtin-generic-v3.7.0`、Kubernetes release v1.36.2、VM class `best-effort-medium`、Storage Class `vks-ftt0`（節點系統碟） |
| General → Network → Node Network | Primary eth0：**Use custom primary network** → `public (Subnet Set)`；**ADD ADDITIONAL NETWORK** → `Additional &#124; eth1`\| eth1` 的 Network 選 **nas-vlan7 (Subnet)** |
| Control Plane | 1 台、Photon |
| Node Pools | node pool 改名 `vks-cl01-np-1`、**Replicas 2**（驗證跨節點 RWX）、Overrides 的 VM class 改 `best-effort-small`；Node Network 自動繼承 eth0 / eth1 |

![](img/583-vks-wizard-1-custom.png)
![](img/584-vks-wizard-2-general.png)
![](img/585-vks-wizard-2-network-eth1.png)
![](img/586-vks-wizard-3-cp.png)
![](img/587a-vks-nodepool-replicas2.png)
![](img/587-vks-nodepool-vmclass-small.png)
![](img/588-vks-nodepool-review.png)
![](img/589-vks-wizard-4-nodepools.png)
![](img/590-vks-wizard-5-review.png)

精靈產生的 YAML（[`manifests/vks-cl01.yaml`](manifests/vks-cl01.yaml)），和一般叢集只差 `secondary` 這段，**沒有任何 registry、NFS 設定**：

```yaml
      - name: networks
        value:
          interfaces:
            primary:
              network: {name: public, kind: SubnetSet, apiVersion: crd.nsx.vmware.com/v1alpha1}
            secondary:
              - name: eth1
                network: {name: nas-vlan7, kind: Subnet, apiVersion: crd.nsx.vmware.com/v1alpha1}
```

> ⚠️ 填完 Node Network 之後**不要回頭展開 General Settings 改東西**：精靈會把 Node Network（含 eth1）和 Node Pools 的自訂值重設。送出前看右側 YAML，確認 `secondary` 和 `overrides` 都還在。

約 **10 分鐘**（10:56 → 11:06）Available：

![](img/591-vks-cluster-creating.png)
![](img/592-vks-cluster-available.png)
![](img/593-vks-cluster-detail.png)

每個節點兩張網卡，eth1 的 IP 由 NSX 從 `nas-vlan7-block` 配（[`cli/23-vks-cl01-eth1.txt`](cli/23-vks-cl01-eth1.txt)）：

```text
$ kubectl -n vks-lab get subnetports | grep vks-cl01          # Supervisor
vks-cl01-bk2xm-ngf48-nas-vlan7-eth1                       192.168.17.64/24   ← control plane
vks-cl01-bk2xm-ngf48-public-eth0                          192.168.16.50/28
vks-cl01-vks-cl01-np-1-vtj9f-xdf86-gkc8r-nas-vlan7-eth1   192.168.17.65/24   ← worker 1
vks-cl01-vks-cl01-np-1-vtj9f-xdf86-gkc8r-public-eth0      192.168.16.51/28
vks-cl01-vks-cl01-np-1-vtj9f-xdf86-ntkmg-nas-vlan7-eth1   192.168.17.66/24   ← worker 2
vks-cl01-vks-cl01-np-1-vtj9f-xdf86-ntkmg-public-eth0      192.168.16.52/28
```

## C-12 NFS CSI driver（air-gap）

用 [`csi-driver-nfs`](https://github.com/kubernetes-csi/csi-driver-nfs) **v4.13.4**。6 個 image 在 staging 機抓成 tar（[`scripts/pull-image-tar.py`](scripts/pull-image-tar.py)，不需要 docker），搬進內網推到 Harbor 的 public project `csi`（推送紀錄：[`cli/22a-harbor-push-csi.txt`](cli/22a-harbor-push-csi.txt)）：

```bash
# staging 機（有網路）
python pull-image-tar.py registry.k8s.io/sig-storage/nfsplugin:v4.13.4 nfsplugin.tar
#   csi-provisioner:v6.3.0、csi-resizer:v2.2.0、csi-snapshotter:v8.6.0、csi-node-driver-registrar:v2.17.0、livenessprobe:v2.19.0 同上
sha256sum *.tar > SHA256SUMS
# Harbor 主機（內網）
sha256sum -c SHA256SUMS
docker load -i nfsplugin.tar
docker tag registry.k8s.io/sig-storage/nfsplugin:v4.13.4 harbor01.home.lab/csi/nfsplugin:v4.13.4
docker push harbor01.home.lab/csi/nfsplugin:v4.13.4
```

Harbor 上的 6 個 image（[`cli/22-harbor-csi-images.txt`](cli/22-harbor-csi-images.txt)）：

```text
harbor01.home.lab/csi/csi-node-driver-registrar:v2.17.0   29.7 MiB
harbor01.home.lab/csi/csi-provisioner:v6.3.0              98.2 MiB
harbor01.home.lab/csi/csi-resizer:v2.2.0                  91.8 MiB
harbor01.home.lab/csi/csi-snapshotter:v8.6.0              93.2 MiB
harbor01.home.lab/csi/livenessprobe:v2.19.0               29.9 MiB
harbor01.home.lab/csi/nfsplugin:v4.13.4                  146.6 MiB
```

官方 manifest 只改 image 位置（`registry.k8s.io/sig-storage/` → `harbor01.home.lab/csi/`），放在 [`manifests/nfs-csi/`](manifests/nfs-csi)：

```bash
kubectl config use-context vks-cl01
kubectl apply -f rbac-csi-nfs.yaml -f csi-nfs-driverinfo.yaml -f csi-nfs-controller.yaml -f csi-nfs-node.yaml
kubectl apply -f storageclass-nas-nfs-rwx.yaml
```

controller 和三個節點的 node pod 都 Running，image 全部從 Harbor 拉（[`cli/25-nfs-csi-install.txt`](cli/25-nfs-csi-install.txt)）。

[`storageclass-nas-nfs-rwx.yaml`](manifests/nfs-csi/storageclass-nas-nfs-rwx.yaml)：provisioner `nfs.csi.k8s.io`、`server: 192.168.17.20`、`share: /export/vks-rwx`、`mountPermissions: "0777"`（讓 PSA restricted 的非 root Pod 能寫）、`mountOptions: nfsvers=4.1`。每個 PVC 在 share 底下建一個子目錄。

## C-13 驗證（CLI + vCenter 效能圖）

```bash
kubectl vsphere login --server=192.168.16.3 -u administrator@vsphere.local --insecure-skip-tls-verify \
  --tanzu-kubernetes-cluster-namespace vks-lab --tanzu-kubernetes-cluster-name vks-cl01
kubectl config use-context vks-cl01
```

### C-13-1 節點的 eth1 與路由

三個節點 Ready（[`cli/05-guest-nodes-sc.txt`](cli/05-guest-nodes-sc.txt)）。在 worker 上看網卡和路由（[`cli/24-node-eth1-route.txt`](cli/24-node-eth1-route.txt)；`kubectl debug node` 需要 privileged，臨時建一個 `pod-security.kubernetes.io/enforce=privileged` 的 namespace，看完刪掉）：

```text
eth0 192.168.16.51/28            ← public SubnetSet（VLAN 6）
eth1 192.168.17.65/24            ← nas-vlan7 Subnet（VLAN 7）

$ ip route get 192.168.17.20      # NAS
192.168.17.20 dev eth1  src 192.168.17.65            ← 直接從 eth1 出去，不經閘道

$ ip route get 10.0.0.86          # 對照：Harbor（管理網段）走 eth0
10.0.0.86 via 192.168.16.49 dev eth0  src 192.168.16.51
```

### C-13-2 Air-gap：image 只從 Harbor 來

[`manifests/nginx-harbor.yaml`](manifests/nginx-harbor.yaml)（[`cli/06-nginx-from-harbor.txt`](cli/06-nginx-from-harbor.txt)）：

```text
Image:  harbor01.home.lab/vks-apps/nginx-unprivileged:1.30.4-alpine
kubelet: Successfully pulled image "harbor01.home.lab/vks-apps/nginx-unprivileged:1.30.4-alpine"
nginx-lb   LoadBalancer   10.99.56.46   192.168.16.33   80:31277/TCP
$ curl -s http://192.168.16.33/ | grep title      # 從管理網段
<title>Welcome to nginx!</title>
```

反向測試 [`manifests/pull-test.yaml`](manifests/pull-test.yaml)（[`cli/07-pull-test.txt`](cli/07-pull-test.txt)）：同一個 busybox，`docker.io` 拉不到、Harbor 拉得到：

```text
from-dockerhub   0/1   ImagePullBackOff   ← Head "https://registry-1.docker.io/v2/...": dial tcp ...:443: i/o timeout
from-harbor      0/1   Completed          ← hello-from-harbor，並且 wget 到 nginx-lb
```

### C-13-3 RWX：兩台 worker 同時寫同一個檔案

[`manifests/nfs-csi/rwx-demo.yaml`](manifests/nfs-csi/rwx-demo.yaml)：一個 **ReadWriteMany** PVC，兩個 Pod 分散在兩台 worker，每 10 秒往同一個檔案寫一行（[`cli/26-rwx-demo.txt`](cli/26-rwx-demo.txt)）：

```text
shared-data   Bound   pvc-57353ea0-...   5Gi   RWX   nas-nfs-rwx

writer-...-546m5   Running   vks-cl01-vks-cl01-np-1-vtj9f-xdf86-gkc8r
writer-...-n2m2p   Running   vks-cl01-vks-cl01-np-1-vtj9f-xdf86-ntkmg

$ kubectl -n rwx-demo exec writer-...-546m5 -- tail -4 /data/shared.log
03:12:19 writer-...-546m5 on vks-cl01-...-gkc8r
03:12:21 writer-...-n2m2p on vks-cl01-...-ntkmg
03:12:29 writer-...-546m5 on vks-cl01-...-gkc8r
03:12:31 writer-...-n2m2p on vks-cl01-...-ntkmg

$ kubectl -n rwx-demo exec writer-...-546m5 -- mount | grep /data
192.168.17.20:/export/vks-rwx/pvc-57353ea0-... on /data type nfs4 (rw,...,vers=4.1,...,clientaddr=192.168.17.65,...,addr=192.168.17.20)
                                                                                   ↑ clientaddr = 這台 worker 的 eth1
```

### C-13-4 NAS 上的連線來源

```text
$ ss -tn state established '( sport = :2049 )'      # NAS 上
192.168.17.20:2049  192.168.17.66:966      ← worker 2 的 eth1
192.168.17.20:2049  192.168.17.65:702      ← worker 1 的 eth1
（沒有任何 192.168.16.x：NFS 完全沒有走 eth0 / VPC）
```

### C-13-5 NFS 流量沒有經過 VNA（重點）

在 Pod 裡往 RWX PV 寫 1 GiB，同時看 NAS 收到多少、worker 每張 vNIC 和 VNA 的網路計數器；再用兩個對照組確認量法量得到 VNA 的流量。量法：vCenter realtime 效能計數器（[`scripts/Measure-VmTraffic.ps1`](scripts/Measure-VmTraffic.ps1)）+ NAS 的 `eth0 rx_bytes`。完整輸出：[`cli/27-nfs-path-proof.txt`](cli/27-nfs-path-proof.txt)。

| 測試 | NAS 收到 | worker eth0（vNIC 4000）送出 | worker eth1（vNIC 4001）送出 | VNA 網路（收 / 送） |
|---|---|---|---|---|
| **A. Pod 寫 1 GiB 到 RWX PV** | **1025 MiB** | 0.0 MB | **1079.5 MB** | **0.7 / 0.9 MB** |
| B. 對照：1 GiB 經 VPC LoadBalancer 下載 | — | ≈1 GiB（經 LB） | — | **1083 / 1083 MB** |
| C. 對照：worker 從 Harbor 拉 430 MB image | — | （收）430 MB | — | 2 / 2 MB |

- A：1 GiB 全部從 **eth1** 送到 NAS，VNA 幾乎是 0。
- B：LoadBalancer 的流量一定經過 VNA（1 GiB 進、1 GiB 出）——證明 A 的 0 不是量不到。B 是在同一套 VPC / VNA 上，用 busybox `httpd` 加一個 LoadBalancer Service 把 1 GiB 檔案送出去量的。
- C：一般 VPC 對外流量（eth0）由 ESXi 上的 Distributed Transit Gateway 轉送，也不經過 VNA（[`manifests/egress-pull-test.yaml`](manifests/egress-pull-test.yaml)）。

vCenter 效能圖（Real-time）：worker 的 **Transmitted for 4001**（eth1）在寫入時衝到約 55 MB/s；同一時段 VNA 沒有任何峰值（VNA 圖上 10:56-10:59 的小峰值是建叢集時的 LB 流量，最高約 5 MB/s）：

![](img/791-worker-perf-network.png)
![](img/792-vna-perf-network.png)

> ℹ️ 量 VNA 要看所有 instance：VNA 的資料面流量在 vCenter 效能計數器裡記在 `vmnic0` / `vmnic1` 這兩個 instance，VM 彙總值（instance 空字串）不含它。腳本對每個 instance 分別加總、取最大的那個，避免同一筆流量同時記在 vNIC 和 vmnic 上被重複計算。效能計數器用 vCenter 的時間；這個 lab 的跳板機時鐘比 vCenter 快約 160 秒，腳本會先換算。
>
> ℹ️ NSX 的 Transit Gateway attachment statistics 不是即時值（會停在幾分鐘前），不適合拿來比對短時間的流量。

## 時間

| 階段 | 時間 |
|---|---|
| nested ESX ×3 部署 | 3 分鐘 |
| A-1 Installer 精靈 + 驗證 | 約 35 分鐘（驗證 15 項約 5 分鐘） |
| bring-up：vCenter → SDDC Manager → vSphere cluster → NSX | 2 小時 14 分鐘 |
| A-2 中止 + 確認 | 2 分鐘（另外觀察 30 分鐘沒有重送） |
| A-3 前置準備 | 約 20 分鐘 |
| C-1 Transit Gateway + VNA | 15 分鐘（和 VKr 上傳同時） |
| C-2 VKr 上傳 | 8 分鐘 |
| C-3 Supervisor | **30 分鐘** |
| C-4 ~ C-6 Harbor 信任、library、namespace、SubnetSet | 約 15 分鐘 |
| C-7 ~ C-10 NSX VLAN 7 連線、IP block、VLAN 延伸 subnet、分享給 namespace | 約 10 分鐘 |
| C-11 VKS 叢集（CP 1 + worker 2，含 eth1） | **10 分鐘** |
| C-12 NFS CSI 安裝 | 1 分鐘 |

## 遇到的問題

| # | 症狀 | 原因 / 處理 |
|---|---|---|
| 1 | Installer 剛開機登入後跳 「Service Interruption — Multiple services are currently down」 | Installer 服務還在啟動，等幾分鐘再 RELOAD |
| 2 | 驗證出現 「Insufficient Storage. 5400 GB storage is less than the required 6748 GB」 | UI 精靈的 vSAN FTT 固定為 1，而且需求含 VCFMS 的 2900 GB。這次不部署 VCFMS，所以 ACKNOWLEDGE 即可（nested ESX 的 capacity 碟先線上擴到 3600 GB） |
| 3 | 中止後 SDDC Manager 的 `VSP_BOOTSTRAP` 任務從 Pending 變成 Failed | `domainmanager` 重啟後曾接手，但 「Bootstrap VCF Services Platform」 失敗，沒有建立任何 VM；之後 30 分鐘沒有重送。**不要**在 Installer 按 RETRY |
| 4 | Storage policy 的 K8s Compliant Name 沒有自動帶出 | 自己填 `vks-ftt0`（Supervisor 精靈會用到這個名稱） |
| 5 | 要從 vmk ping VLAN 6 閘道，但 nested ESX 的 SSH 連不上 | bring-up 後 SSH 是關的；用 PowerCLI `Get-EsxCli -V2` 透過 vCenter 執行 `esxcli network diag ping`，不必開 SSH |
| 6 | `kubectl -n vks-lab get clusterclass` 只看到 v3.1.0-v3.3.0 | v3.4.0-v3.7.0 在 `vmware-system-vks-public` namespace；叢集 YAML 的 `classRef.namespace` 本來就指向那裡 |
| 7 | NSX UI 建的 VLAN 延伸 subnet，VKS 節點的 eth1 只有 link-local、沒有 IPv4 | UI 沒有 static IP allocation 選項，建好之後也改不了 → 用 API 建（C-9） |
| 8 | 把 `nas-vlan7` 加到 Transit Gateway 報 「ENABLED_L2 cannot be used for attachment」 | Dedicated to Subnet 的連線本來就不接 Transit Gateway，不要加 |
| 9 | `kubectl apply` 建帶 `vlanConnectionName` 的 Subnet 被擋 | 只有 NSX Operator 能設；從 vCenter **Namespace Networks → ADD** 分享（C-10） |
| 10 | VKS 精靈回頭改 General Settings 後，Node Network（含 eth1）和 Node Pools 的自訂值不見 | 精靈的行為；改完要重新設定，送出前看 YAML |
| 11 | 建叢集按 FINISH 被擋：`variables[networks] ... variable is not defined` | Supervisor 剛 Running 時 VKS webhook 還沒同步 ClusterClass 變數；`kubectl apply --dry-run=server -f vks-cl01.yaml` 通過後再按 FINISH |
| 12 | `kubectl debug node` 被 PodSecurity `restricted` 擋 | 臨時建一個 label `pod-security.kubernetes.io/enforce=privileged` 的 namespace 跑 debug pod，用完刪掉 |
| 13 | NSX 的 Transit Gateway attachment statistics 不會即時更新 | 改用 vCenter realtime 效能計數器量 VNA / worker 的流量 |

## 參考

- Broadcom VCF Blog：[Simplify Workload Connectivity and Enhance Network Scale and Performance with VCF 9.1](https://blogs.vmware.com/cloud-foundation/2026/05/05/simplify-workload-connectivity-and-enhance-network-scale-and-performance-with-vcf-9-1/)——Distributed Transit Gateway 不需要 NSX Edge；VNA cluster 為 DTGW 提供 NAT、LB 等有狀態服務；VKS 節點與 Pod 的 secondary NIC 可對應到 VPC 的 VLAN 或 subnet，用於直接存取 NFS storage 等實體系統
- [kubernetes-csi/csi-driver-nfs](https://github.com/kubernetes-csi/csi-driver-nfs)

## 檔案

| 路徑 | 內容 |
|---|---|
| `img/` | 截圖：`0-bringup/`（Installer 精靈）、`0-prep/`（前置準備）、`23x` TGW/VNA、`24x` VKr、`35x` Supervisor、`46x-48x` Harbor/namespace/SubnetSet、`58x-59x` VKS 叢集、`70x-73x` NSX VLAN 7 連線 / IP block / subnet / 分享、`79x` 驗證；`diagram/` 架構圖原始檔 |
| `cli/` | 各步驟的指令輸出 |
| `manifests/` | VKS 叢集 YAML（精靈產生）、NSX subnet JSON、`nfs-csi/`（driver、StorageClass、RWX demo）、驗證用的 nginx / pull-test / egress-pull-test |
| `scripts/abort_watcher.py` | 監看 bring-up，在 VCF Management Platform 開始時自動中止（+ `installer_api.py`、`installer_root.py`） |
| `scripts/nas/` | NAS 離線打包、部署、安裝 |
| `scripts/pull-image-tar.py` | 不用 docker 把 image 抓成 tar（staging 機） |
| `scripts/Measure-VmTraffic.ps1` | 量 VM 在一段時間內的網路 / 虛擬磁碟流量（PowerCLI，經 vCenter） |

腳本不含密碼，執行前用環境變數提供（`LAB_PASSWORD`、`VCPASS`、`VIPASS`、`NFSPASS`、`NSX_PASS`）。IP / FQDN 是 lab 值，請依環境修改。
