# VKS on VLAN-backed VPC + VNA，worker 第二張網卡走 NFS 專用 VLAN — Step by Step

> 📄 Word 版：`VKS-VLANVPC-VNA-NFS-VLAN-Step-by-Step.docx`

VCF 9.1.1 管理網域（不部署 VCF Management Services）上，用 **VLAN-backed VPC + Distributed Transit Gateway + VNA（Virtual Network Appliance）** 啟用 Supervisor 與 VKS。
VKS 節點有兩張網卡：

- **eth0**：VPC 的 Public SubnetSet（外部 VLAN 8 的 /20 切出來的 /24），走 Transit Gateway；LoadBalancer VIP 由 VNA 提供
- **eth1**：NSX「Dedicated to Subnet」的 VLAN 延伸 subnet（VLAN 7），**直接 Layer 2 接到 NAS**，NFS 流量不經 Transit Gateway、不經 VNA

image 一律從內部 Harbor 拉（air-gap）。bring-up 用 API 完成，**VKS 段全程 UI 截圖**；最後附「部署一個服務」的 kubectl 指令 demo。

## 0. 架構與規劃值

```text
                     physical L3 (lab: RouterOS)
          VLAN 8  gw 192.168.47.254/20          VLAN 7  gw 192.168.17.254/24
                 |                                     |
  +--------------+-----------------+                   |          +---------------+
  | Distributed Transit Gateway    |                   +----------| NAS (NFS)     |
  | (on ESXi, no Edge)             |                   |          | 192.168.17.20 |
  | + VNA cluster vks-vna (LB/NAT) |                   |          +---------------+
  +--------------+-----------------+                   |
                 | VPC vks-lab                         | Dedicated to Subnet (L2 extension)
     SubnetSet nodes 192.168.33.0/24                   | Subnet nas-vlan7 192.168.17.0/24
                 |                                     |
          +------+-------------------------------------+------+
          | VKS node   eth0 192.168.33.x    eth1 192.168.17.64+ |
          +---------------------------------------------------+
```

| 項目 | lab 值 | 客戶環境換成 |
|---|---|---|
| VCF | 9.1.1，管理網域 `vcf-m02`，3 台 ESXi（vSAN OSA） | — |
| vCenter / NSX / SDDC Manager | `vcf-m02-vc01` 10.0.1.19 / `vcf-m02-nsx01` 10.0.1.21 / `vcf-m02-sddcm01` 10.0.1.18 | — |
| 外部 VLAN（VPC External） | **VLAN 8，192.168.32.0/20，gw 192.168.47.254** | X.Y.0.0/20，gw X.Y.15.254 |
| /20 切法 | 32.0/27 系統（Supervisor 實測用 .0–.31）、33.0/24 起給 SubnetSet（一個 namespace 一段）、35–39 保留、**40.0/22 給 LoadBalancer VIP**、44–47 不給 NSX | 同比例 |
| VNA | `vks-vna`，Large ×1，`vcf-m02-vna01a` 10.0.1.28 | 正式建議 2 節點 |
| Supervisor | `vcf-m02-sv01`，管理 IP 10.0.1.222–226，API VIP 192.168.32.5，Service CIDR 172.29.0.0/16 | — |
| NFS VLAN | **VLAN 7，192.168.17.0/24，gw 192.168.17.254**，NAS 192.168.17.20 `/export/vks-rwx` | 客戶 NAS VLAN |
| VKS 叢集 | `vks-cl01`：CP 1 + worker 2，Pods 100.112.0.0/18，Services 100.80.0.0/13 | CP 3 |
| Registry | `harbor01.home.lab`（project `vks-apps`、`csi`） | 客戶 Harbor |

## 1. bring-up（API，不部署 VCF Management Services）

VPC、VNA、Supervisor 和 VKS 都不依賴 VCF Management Services（VCFMS），所以 bring-up 跑到 NSX 完成就中止。這樣可以省掉 VSP bootstrap 那一段，它在 nested 環境最常卡住，也最吃 CPU。這一段沒有截圖。

1. 部署 nested ESXi ×3：`vcf-m02-esx01..03`，20 vCPU / 80 GB，vSAN cache 60 GB、capacity 3600 GB。
2. 準備部署規格 `m02-spec.json`。做法是用 VCF Installer 的 `GET /v1/sddcs/{id}/spec` 取得上一套的 spec，再改名、改 IP。
   - vSAN FTT 改成 0。
   - 設定 `nsxtSpec.overlayVtepSpec.vtepType = NO_IP`，主機就不會有 TEP，這是 VLAN-backed VPC 要用的設定。
3. 驗證：`POST /v1/sddcs/validations`，結果是 COMPLETED / WARNING。
   - vMotion 與 vSAN 閘道不回應：隔離網段，屬預期。
   - CPU 建議 65 核：nested 環境才有的提醒。
4. 部署：`POST /v1/sddcs`，依序完成 vCenter、SDDC Manager、vSphere cluster、NSX，共 **1 小時 46 分**。
5. 監看腳本在「Deploy VCF Management Services via SDDC Manager」一開始就中止 bring-up：
   1. installer `systemctl stop domainmanager`
   2. SDDC Manager `systemctl restart domainmanager`
   3. 確認沒有 VSP VM
   4. installer `systemctl start domainmanager`
   5. 之後觀察 30 分鐘，確認沒有重送

> ⚠️ ESXi 剛開機時，憑證可能還會重新產生一次。第一次驗證若報「SSL thumbprint ... is not matching」，重新抓 thumbprint 再送一次就會過。

## 2. VKS 前置準備

### 2-1 vSAN Health 靜音（nested 限定，🔴 要在啟用 Supervisor 之前做）

Supervisor 會用 vLCM 的「Apply Solution」把 spherelet 裝到 ESXi 上。vLCM 動手前會先檢查 vSAN health，而 **nested 的虛擬 NVMe 永遠通不過「NVMe device is VMware certified」**，所以每分鐘失敗一次。

task 上只寫「A general system error occurred」。真正的原因在 vCenter 的 `/var/log/vmware/vmware-updatemgr/vum-server/vmware-vum-server.log`：

```text
overall_health_not_green: vSAN health test 'NVMe device is VMware certified'
```

沒處理的話會出現三個症狀：
- ESXi 不會變成 Supervisor node。
- Consumption Interface（LCI）的 vSphere Pod 一直 Pending。
- namespace 的 Resources 分頁只顯示「安裝 Consumption Interface」。

修法：進 Cluster → Monitor → vSAN → Skyline Health，對 **NVMe device is VMware certified** 和 **Performance service status** 兩項按 SILENCE ALERT。lab 是用 `scripts/vsan_silence_nvme_hcl.py` 做的。實體環境、硬體在 HCL 上的話，不需要這一步。

### 2-2 VM 管理 port group 安全性（nested 限定）

Supervisor control plane VM 接在 `SDDC-DPortGroup-VM-Mgmt` 上。把這個 port group 的 Promiscuous mode、MAC address changes、Forged transmits 都改成 **Accept**。

### 2-3 VM Storage Policy `vks-ftt0`

到 **Policies and Profiles → VM Storage Policies → CREATE**：

| 精靈頁 | 設定 |
|---|---|
| Name and description | Name `vks-ftt0`、K8s Compliant Name `vks-ftt0` |
| Policy structure | 勾 **Enable rules for "vSAN" storage** |
| vSAN → Availability | 先關掉 **vSAN ESA Auto-RAID (9.1 onwards)**，因為這套是 OSA。Site disaster tolerance 選 **None - standard cluster**，Failures to tolerate 選 **No data redundancy** |
| vSAN → Advanced Policy Rules | 打開 **Force provisioning** |
| Storage compatibility | 確認 `vcf-m02-cl01-ds-vsan01` 相容 |

![](img/010-policy-list.png)
![](img/011-policy-name.png)
![](img/012-policy-structure.png)
![](img/013-policy-vsan-availability.png)
![](img/014-policy-vsan-advanced-force.png)
![](img/015-policy-storage-compat.png)
![](img/016-policy-review.png)
![](img/017-policy-created.png)

## 3. Transit Gateway + VNA（外部 VLAN /20）

到 vCenter → **Networks → Transit Gateways → SETUP NETWORK CONNECTIVITY**：

| # | 頁面 | 設定 |
|---|---|---|
| 1 | Transit Gateway Connectivity | VLAN-backed 只有 **Distributed VLAN Connection** 一個選項。按 NEXT，前置條件勾 Select All，再按 CONTINUE |
| 2 | External Network Connectivity | VLAN ID **8**，Gateway CIDR **192.168.47.254/20** |
| 2a | Add IP Address Block（⋮ → Create New） | Name `vks-ext-block`、Visibility External、CIDR **192.168.32.0/20**。Excluded（lab 實際填的）：**192.168.32.35–192.168.32.255**、**192.168.35.0–192.168.39.255**、**192.168.44.0–192.168.47.255**。建議值見下方 🔑 |
| 3 | VPC Services | VNA cluster `vks-vna`，Form Factor **Large**。ADD 一個節點：`vcf-m02-vna01a.home.lab`、cluster、vSAN datastore、Static 10.0.1.28/23、gw 10.0.0.1、port group `SDDC-DPortGroup-VM-Mgmt` |
| 4 | Review and Deploy | 按 DEPLOY，VNA 約 15 分鐘後 UP |

> 🔑 **VIP 是從 IP block 裡「最小的可用單一 IP」配出來的**，NSX 沒有「VIP 專區」這種設定。要讓 LoadBalancer VIP 落在 40.0/22，**比它小的可用 IP 都要先排除**：
>
> - 系統 /24 剩下的部分：Supervisor 實測只用掉 32.0–32.31，**建議排除 32.32–32.255**。lab 一開始只排除 .35 起，結果叢集 API VIP 拿到 32.32、第一個 Service 拿到 32.33。
> - **預留給之後 namespace、但還沒用到的 SubnetSet /24**：lab 只有一個 namespace，33.0/24 被 SubnetSet 用掉後，Service 就拿到 34.0。把 34.0/24 也排除後才拿到 **40.0**。
> - 開新 namespace 前，先把它那段 /24 從 Excluded 移除，SubnetSet 才切得到。
>
> Excluded IP 事後可以改（下面的 PATCH），已配出去的 IP 不受影響、刪掉 Service 重建才會換 VIP。
>
> ```bash
> # 一定要帶 cidrs + visibility，少帶會回 520051「visibility and CIDR cannot be updated」（訊息誤導）
> curl -k -u "admin:$NSX_PASS" -X PATCH -H 'Content-Type: application/json' \
>   https://vcf-m02-nsx01.home.lab/policy/api/v1/infra/ip-blocks/vks-ext-block \
>   -d '{"cidrs":["192.168.32.0/20"],"visibility":"EXTERNAL","excluded_ips":[
>        {"start":"192.168.32.32","end":"192.168.32.255"},{"start":"192.168.34.0","end":"192.168.34.255"},
>        {"start":"192.168.35.0","end":"192.168.39.255"},{"start":"192.168.44.0","end":"192.168.47.255"}]}'
> ```
>
> 🔑 **主機必須沒有 TEP。** bring-up 時設了 `vtepType: NO_IP`，這個精靈才能一路 DEPLOY。主機有 TEP 時，精靈建出來的 VNA cluster 跟 VLAN-backed TGW 不相容，DEPLOY 會失敗。

![](img/110-transit-gateways.png)
![](img/111-setup-p1-distributed-vlan.png)
![](img/112-setup-prereq.png)
![](img/113-setup-p2-vlan-gateway.png)
![](img/114-ipblock-vks-ext-block.png)
![](img/115-setup-p2-external.png)
![](img/116-vna-add-node.png)
![](img/117-vna-cluster-large.png)
![](img/118-setup-review.png)
![](img/119-setup-deploying.png)
![](img/120-tgw-vna-success.png)

## 4. VKr 本機匯入（Local Content Library）

到 **Content Libraries → CREATE**，依序設定：
- 名稱 `vkr-local`
- 類型 **Local content library**，不開 publishing
- 不套 security policy
- storage 選 vSAN

![](img/210-content-libraries.png)
![](img/211-cl-1-name.png)
![](img/212-cl-2-local.png)
![](img/213-cl-3-security.png)
![](img/214-cl-4-storage.png)
![](img/215-cl-5-ready.png)
![](img/216-cl-created.png)

接著匯入 VKr 檔案：
1. `vkr-local` → **ACTIONS → Import item** → **Local file** → **UPLOAD FILES**。
2. 一次選 4 個檔：`.ovf`、`-disk1.vmdk`、`.mf`、`.cert`。
3. Item name 會自動帶出 `photon-5-amd64-v1.36.2---vmware.2-vkr.3`，按 IMPORT。

上傳約 8 分鐘，可以和 VNA 部署同時進行。**上傳期間不要重新整理這個瀏覽器分頁。**

![](img/217-cl-actions-import.png)
![](img/218-cl-import-local-files.png)
![](img/219-cl-importing.png)
![](img/220-cl-vkr-imported.png)

## 5. 啟用 Supervisor

到 **Supervisor Management → GET STARTED**：

| # | 頁面 | 設定 |
|---|---|---|
| 1 | vCenter Server and Network | **VCF Networking with VPC (recommended)** |
| 2 | Supervisor location | CLUSTER DEPLOYMENT：`vcf-m02-sv01`、cluster `vcf-m02-cl01`、zone `vz-01` |
| 3 | Storage | 三個 policy 都選 `vks-ftt0` |
| 4 | Management Network | Static、`SDDC-DPortGroup-VM-Mgmt`、10.0.1.222–10.0.1.226、255.255.254.0、gw 10.0.0.1、DNS 10.0.0.200、search domain home.lab、NTP 10.0.1.254 |
| 5 | Workload Network | Project Default、Default VPC Connectivity Profile、External IP Block **vks-ext-block**（自動帶出）、Service CIDR 172.29.0.0/16、DNS / NTP |
| 6 | Advanced | Control plane size 選 Small |
| 7 | Ready to complete | 按 FINISH |

約 20 分鐘後變成 RUNNING，API VIP 是 **192.168.32.5**，落在系統 /24。Kubernetes status 會顯示 WARNING「No node is accepting vSphere Pods」，要等 2-1 的 spherelet 裝完才會消失。

![](img/310-supervisor-mgmt-home.png)
![](img/311-sv-1-network.png)
![](img/312-sv-2-location.png)
![](img/313-sv-3-storage.png)
![](img/314-sv-4-mgmt-network.png)
![](img/315-sv-5-workload.png)
![](img/316-sv-6-advanced.png)
![](img/317-sv-7-ready.png)
![](img/318-sv-configuring.png)

## 6. Harbor 信任 + Kubernetes Service library

到 Supervisor → **Configure → Container Registries → ADD REGISTRY**：Name `harbor01`、Host `harbor01.home.lab`，貼上 Harbor CA。Supervisor 會建一個 `ClusterRegistryConfig`，CA 會自動下發到之後建立的 VKS 節點，所以叢集 YAML 不需要 trust 變數。

再到 Supervisor → **Configure → General → Kubernetes Service → ADD**，勾 `vkr-local`。十幾秒後，`kubectl get kr` 會出現 `v1.36.2---vmware.2-vkr.3`，狀態是 READY / COMPATIBLE。

![](img/410-container-registries.png)
![](img/411-add-registry-harbor01.png)
![](img/412-registry-added.png)
![](img/413-kubernetes-service.png)
![](img/414-add-library-vkr-local.png)
![](img/415-library-added.png)

## 7. Namespace、Storage / VM Class、SubnetSet

到 **Supervisor Management → Namespaces → NEW NAMESPACE**：

| 頁面 | 設定 |
|---|---|
| Location | Supervisor `vcf-m02-sv01` |
| Configuration | Name `vks-lab`，勾 override network settings |
| Network Settings | **Create a VPC**，**Default Subnet Size 256** |
| Networks | 不用加 |
| Zones | `vz-01`，然後按 FINISH |

NSX 會建出 VPC `vks-lab_1na6e`。

![](img/510-ns-1-location.png)
![](img/511-ns-2-configuration.png)
![](img/512-ns-3-network-settings.png)
![](img/513-ns-4-networks.png)
![](img/515-ns-5-zones.png)
![](img/516-ns-6-review.png)
![](img/517-ns-created.png)

在 Namespace → Summary 加上 storage 與 VM class：**ADD STORAGE** 選 `vks-ftt0`；**ADD VM CLASS** 選 best-effort-xsmall / small / medium。

![](img/520-ns-summary.png)
![](img/521-add-storage.png)
![](img/522-add-vm-class.png)
![](img/523-ns-summary-configured.png)

再建節點用的 SubnetSet：Namespace → **Resources**（LCI 外掛）→ Network card 的 **GO TO SERVICE** → **SubnetSets → NEW SUBNETSET**。Name `nodes`、Access Mode **Public**、Size **256 IPs (/24 network)**，按 CREATE。建好時還不會切子網，第一個叢集建立時，NSX 才會切出 192.168.33.0/24。

> ⚠️ 如果 Resources 分頁只顯示「Download and install the Consumption Interface Supervisor Service」，表示 LCI 的 vSphere Pod 還沒跑起來，請回頭看 2-1。

![](img/530-lci-home.png)
![](img/531-lci-subnetsets.png)
![](img/532-new-subnetset.png)
![](img/533-subnetset-created.png)

## 8. NFS 專用 VLAN

### 8-1 NSX External Connection `nas-vlan7`

到 NSX Manager → **Networking → VPC Connectivity → External Connections → ADD EXTERNAL CONNECTION**：

| 欄位 | 值 |
|---|---|
| Name | `nas-vlan7` |
| Type | **Distributed VLAN Connection**，並打開 **Dedicated to Subnet** |
| Subnet Extension Connection | **Layer 2** |
| VLAN ID | **7** |
| Gateway CIDR IPv4 Address | `192.168.17.254/24`，即實體閘道。打完要按 Enter |
| VLAN/Subnet Block | `nas-vlan7-block`，會自動推出 192.168.17.0/24 |

存好後清單有兩條：
- VLAN 8 那條：Transit Gateway = 1。
- `nas-vlan7`：Dedicated to Subnet = Yes、Transit Gateway = 0。

**不要**把 `nas-vlan7` 接到 Transit Gateway。它不接 TGW，正是 NFS 流量不經過 TGW 與 VNA 的原因。

![](img/610-nsx-external-connections.png)
![](img/611-nsx-add-dvc-nas-vlan7.png)
![](img/612-nsx-external-connections-two.png)

### 8-2 IP block 排除既有設備

到 **IP Management → IP Address Pools → IP Address Blocks** → `nas-vlan7-block` → ⋮ Edit，Excluded IP Ranges 填 `192.168.17.1-192.168.17.63`，按 SAVE。NAS（.20）等既有設備都在這一段，VKS 節點的 eth1 會從 **.64** 開始配。

![](img/613-nsx-ipblock-exclude.png)
![](img/614-nsx-ipblock-excluded.png)

### 8-3 VLAN 延伸 Subnet（NSX API）

在 namespace 的 VPC 裡建一個 subnet，`vlan_connection` 指向 `nas-vlan7`，並且**打開 static IP allocation**。eth1 要靠 static IP allocation 拿 IP。用 UI 建的 VLAN 延伸 subnet 沒有這個選項，事後也改不了，所以這一步用 API：

```bash
curl -k -u "admin:$NSX_PASS" -X PATCH -H 'Content-Type: application/json' \
  https://vcf-m02-nsx01.home.lab/policy/api/v1/orgs/default/projects/default/vpcs/vks-lab_1na6e/subnets/nas-vlan7-sn \
  -d @manifests/nsx-subnet-nas-vlan7.json
```

[`manifests/nsx-subnet-nas-vlan7.json`](manifests/nsx-subnet-nas-vlan7.json) 的重點欄位：

| 欄位 | 值 |
|---|---|
| `access_mode` | `Public` |
| `vlan_connection` | `/infra/distributed-vlan-connections/nas-vlan7` |
| `ip_addresses` | `192.168.17.0/24` |
| `ip_blocks` | 指向 `nas-vlan7-block`，實際 ID 用 `GET /policy/api/v1/infra/ip-blocks` 查 |
| `static_ip_allocation.enabled` | `true` |
| `connectivity_state` | `DISCONNECTED` |
| DHCP | 關閉 |

結果在 [`cli/20-nsx-subnet-api.txt`](cli/20-nsx-subnet-api.txt)：

```text
HTTP 200
{"gateway_address":"192.168.17.254/24","network_address":"192.168.17.0/24","vlan_extension":{"vpc_gateway_connection_enable":false,"vlan_id":7}}
```

![](img/620-nsx-vpc-subnet-nas-vlan7.png)

### 8-4 分享給 Namespace

到 Namespace `vks-lab` → **Configure → General → Namespace Networks → ADD**：

| 欄位 | 值 |
|---|---|
| NSX Project | Default |
| VPC | `vks-lab_1na6e` |
| Subnet | `nas-vlan7-subnet`。**要打字搜尋 `nas`** 才會列出 |
| Subnet CR Name | `nas-vlan7` |
| Usage | 兩個都不勾 |

按 ADD 後，namespace 裡會出現 `Subnet` CR `nas-vlan7`，帶 `vlanConnectionName: nas-vlan7` 與 static IP，見 [`cli/21-subnet-cr.txt`](cli/21-subnet-cr.txt)。

> ⚠️ 不能直接 `kubectl apply` 一個帶 `vlanConnectionName` 的 Subnet，webhook 會擋下來，這個欄位只能由 NSX Operator 設定。一定要先在 NSX 建好，再從 vCenter 分享。

![](img/630-ns-configure-namespace-networks.png)
![](img/631-ns-add-network-nas-subnet.png)
![](img/632-ns-namespace-networks-added.png)

## 9. 建立 VKS 叢集（eth0 + eth1）

到 Resources → Kubernetes → **CREATE CLUSTER** → **Custom Configuration**：

| 精靈頁 | 設定 |
|---|---|
| General | Name `vks-cl01`、ClusterClass `builtin-generic-v3.7.0`、Kubernetes release v1.36.2、VM class `best-effort-small`、Storage Class `vks-ftt0` |
| General → Network | 見下方步驟 |
| Control plane | Replicas 1（正式環境用 3）、Photon 5 |
| Node pools | 預設 pool 按 ⋮ Edit：Name `vks-cl01-np-1`、**Replicas 2**，用來驗證跨節點 RWX |
| Review | 看右側 YAML，確認有 `secondary: eth1 → nas-vlan7 (Subnet)`，再按 FINISH |

General → Network 的設定步驟：
1. Primary eth0：勾 **Use custom primary network**，選 `nodes (Subnet Set)`。
2. 按 **ADD ADDITIONAL NETWORK**，在 `Additional | eth1` 的 Network 選 `nas-vlan7 (Subnet)`。
3. CNI 選 Antrea。
4. **Pods CIDR** 填 `100.112.0.0/18`，**Services CIDR** 填 `100.80.0.0/13`。

> ⚠️ 填完 Network 之後，**不要回頭展開 General Settings**。精靈會把 Node Network（含 eth1）和 Node Pools 的自訂值重設。按 FINISH 前先看右側 YAML。

![](img/700-lci-home.png)
![](img/701-wizard-1-custom.png)
![](img/702-wizard-2-general.png)
![](img/703-wizard-2-network-eth1.png)
![](img/704-wizard-2-pod-service-cidr.png)
![](img/705-wizard-3-cp.png)
![](img/706-nodepool-replicas2.png)
![](img/707-nodepool-review.png)
![](img/708-wizard-4-nodepools.png)
![](img/709-wizard-5-review.png)
![](img/710-cluster-creating.png)

精靈產生的 YAML 在 [`manifests/vks-cl01-ui-generated.yaml`](manifests/vks-cl01-ui-generated.yaml)。它和一般叢集只差 `secondary` 這一段：

```yaml
      - name: networks
        value:
          interfaces:
            primary:
              network: {name: nodes, kind: SubnetSet, apiVersion: crd.nsx.vmware.com/v1alpha1}
            secondary:
              - name: eth1
                network: {name: nas-vlan7, kind: Subnet, apiVersion: crd.nsx.vmware.com/v1alpha1}
```

叢集約 **9 分鐘** Available。每個節點兩張網卡：eth0 由 NSX 從 SubnetSet `nodes`（192.168.33.0/24）配，eth1 從 `nas-vlan7` 配，從 .64 開始。結果見 [`cli/23-vks-cl01-subnetports.txt`](cli/23-vks-cl01-subnetports.txt)：

```text
$ kubectl -n vks-lab get subnetports          # Supervisor
vks-cl01-c6nmq-hvfwt-nas-vlan7-eth1                       192.168.17.64/24   ← control plane
vks-cl01-c6nmq-hvfwt-nodes-eth0                           192.168.33.2/24
vks-cl01-vks-cl01-np-1-n6ktt-dgsfm-rhd45-nas-vlan7-eth1   192.168.17.66/24   ← worker 1
vks-cl01-vks-cl01-np-1-n6ktt-dgsfm-rhd45-nodes-eth0       192.168.33.4/24
vks-cl01-vks-cl01-np-1-n6ktt-dgsfm-zlmj7-nas-vlan7-eth1   192.168.17.65/24   ← worker 2
vks-cl01-vks-cl01-np-1-n6ktt-dgsfm-zlmj7-nodes-eth0       192.168.33.3/24
```

![](img/711-cluster-available.png)

## 10. NFS CSI driver（air-gap）

使用 [`csi-driver-nfs`](https://github.com/kubernetes-csi/csi-driver-nfs) **v4.13.4**。6 個 image 都已推到 Harbor 的 public project `csi`：nfsplugin、csi-provisioner、csi-resizer、csi-snapshotter、csi-node-driver-registrar、livenessprobe。官方 manifest 只改 image 位置，從 `registry.k8s.io/sig-storage/` 改成 `harbor01.home.lab/csi/`。

```bash
kubectl apply -f rbac-csi-nfs.yaml -f csi-nfs-driverinfo.yaml -f csi-nfs-controller.yaml -f csi-nfs-node.yaml
kubectl apply -f storageclass-nas-nfs-rwx.yaml
```

[`storageclass-nas-nfs-rwx.yaml`](manifests/nfs-csi/storageclass-nas-nfs-rwx.yaml) 的設定：

| 參數 | 值 | 用途 |
|---|---|---|
| provisioner | `nfs.csi.k8s.io` | |
| server | `192.168.17.20` | NAS |
| share | `/export/vks-rwx` | 每個 PVC 在 share 底下建一個子目錄 |
| mountPermissions | `"0777"` | 讓 PSA restricted 的非 root Pod 也能寫 |
| mountOptions | `nfsvers=4.1` | |

1 分鐘內 controller 與三個 node pod 都 Running，見 [`cli/25-nfs-csi-install.txt`](cli/25-nfs-csi-install.txt)。

> ⚠️ NAS 的 export 要放行 eth1 的網段（192.168.17.0/24）。如果改成走別的節點網段，也要記得改 export；ONTAP 對應的是 export policy rule。

## 11. 驗證

以下用 guest cluster 的 kubeconfig 執行。lab 用的是 Supervisor 上的 `vks-cl01-kubeconfig` secret；一般使用者用 `kubectl vsphere login ... --tanzu-kubernetes-cluster-name vks-cl01` 登入。

### 11-1 節點的 eth1 與路由

用 [`cli/26-node-eth1-route.txt`](cli/26-node-eth1-route.txt) 驗證。`kubectl debug node` 需要 privileged，所以臨時建一個 `pod-security.kubernetes.io/enforce=privileged` 的 namespace，看完就刪掉。

```text
eth0 192.168.33.3/24
eth1 192.168.17.65/24

$ ip route get 192.168.17.20      # NAS
192.168.17.20 dev eth1 src 192.168.17.65           ← 直接從 eth1 出去，不經過任何閘道

$ ip route get 10.0.0.86          # Harbor（管理網段）
10.0.0.86 via 192.168.33.1 dev eth0 src 192.168.33.3 ← 其他流量都走 eth0 → VPC → Transit Gateway
```

### 11-2 RWX：兩台 worker 同時寫同一個檔

[`manifests/nfs-csi/rwx-demo.yaml`](manifests/nfs-csi/rwx-demo.yaml) 建一個 RWX PVC，兩個 Pod 用 anti-affinity 分到兩台 worker，每 10 秒往同一個檔案寫一行。結果見 [`cli/27-rwx-demo.txt`](cli/27-rwx-demo.txt)：

```text
shared-data   Bound   pvc-43fe5d2f-...   5Gi   RWX   nas-nfs-rwx
writer-...-5xg6b   Running   100.112.2.6   vks-cl01-...-rhd45
writer-...-q9pv6   Running   100.112.1.3   vks-cl01-...-zlmj7

$ tail -4 /data/shared.log
16:50:02 writer-...-q9pv6 on vks-cl01-...-zlmj7
16:50:05 writer-...-5xg6b on vks-cl01-...-rhd45
16:50:12 writer-...-q9pv6 on vks-cl01-...-zlmj7
16:50:15 writer-...-5xg6b on vks-cl01-...-rhd45

$ mount | grep /data
192.168.17.20:/export/vks-rwx/pvc-43fe5d2f-... on /data type nfs4 (rw,...,vers=4.1,...,clientaddr=192.168.17.66,...)
                                                                         ↑ clientaddr = 這台 worker 的 eth1
```

Pod IP 是 100.112.x.x，落在叢集精靈填的 Pod CIDR 100.112.0.0/18 裡。

### 11-3 NAS 看到的連線來源

結果見 [`cli/28-nas-connections.txt`](cli/28-nas-connections.txt)：

```text
$ ss -tn state established '( sport = :2049 )'      # NAS
192.168.17.20:2049  192.168.17.65:778    ← worker 2 eth1
192.168.17.20:2049  192.168.17.66:706    ← worker 1 eth1
```

NAS 上沒有任何 192.168.33.x 的連線，代表 NFS 完全沒有走 eth0、VPC 或 VNA。CSI controller 建 PVC 時也是走 eth1：建一個測試 PVC，在節點 conntrack 只看到 192.168.17.65 → 192.168.17.20:2049，見 [`cli/29-provision-source-probe.txt`](cli/29-provision-source-probe.txt)。

> ⚠️ 判讀時要小心殘留 session。lab 第一次看到 NAS 上還有 `192.168.33.3`、`17.66:966`、`17.65:702`，其實是之前硬關機刪掉的另一套叢集留下來的。那套叢集用了同樣的 IP，閒置 3.5 到 77 小時，節點的 conntrack 裡也沒有。在 NAS 用 `ss -tni` 看 `lastrcv`，再用 `sudo ss -K dst <IP> dport = <port>` 清掉。

## 12. 部署服務 demo（kubectl 指令）

範例 [`manifests/web-demo.yaml`](manifests/web-demo.yaml) 是一個對外的網頁服務，由四個部分組成：

| 物件 | 內容 |
|---|---|
| PVC | 網頁內容放在 NFS 的 **RWX PVC**，經由 eth1 掛 NAS |
| Job | 第一次把首頁寫進 PVC |
| Deployment | 3 個 nginx 共用這份內容，image 從 Harbor 拉 |
| Service | type **LoadBalancer**，VIP 由 VNA 從 External IP block 配 |

叢集預設的 PSA 是 restricted，所以 Pod 都要帶完整的 securityContext：nginx 用 `nginx-unprivileged`（uid 101、listen 8080），busybox 用 `runAsUser: 1000`。整份指令見 [`manifests/web-demo-commands.md`](manifests/web-demo-commands.md)，實際輸出見 [`cli/30-web-demo.txt`](cli/30-web-demo.txt)。

```bash
# 0. 登入叢集
kubectl vsphere login --server=192.168.32.5 -u administrator@vsphere.local --insecure-skip-tls-verify \
  --tanzu-kubernetes-cluster-namespace vks-lab --tanzu-kubernetes-cluster-name vks-cl01
kubectl config use-context vks-cl01

# 1. 部署
kubectl apply -f web-demo.yaml
kubectl -n web-demo wait --for=condition=complete job/seed-content --timeout=180s
kubectl -n web-demo rollout status deploy/web

# 2. 取得 VIP，從外面連
kubectl -n web-demo get svc web-lb
VIP=$(kubectl -n web-demo get svc web-lb -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
curl -s http://$VIP/
```

```text
NAME     TYPE           CLUSTER-IP      EXTERNAL-IP    PORT(S)
web-lb   LoadBalancer   100.85.74.196   192.168.40.0   80:31184/TCP     ← Service CIDR 100.80.0.0/13、VIP 在 40.0/22

$ curl -s http://192.168.40.0/
<h1>web-demo v1</h1><p>content on NFS (RWX PV)</p>
```

```bash
# 3. 改內容：只寫一次 PV，三個 Pod 同時生效（RWX）
kubectl -n web-demo run edit --rm -i --restart=Never --image=harbor01.home.lab/vks-apps/busybox:1.37.0 \
  --overrides='{"spec":{...,"containers":[{"name":"edit",...,"command":["sh","-c","echo \"<h1>web-demo v2</h1>...\" > /content/index.html"],...}],"volumes":[{"name":"c","persistentVolumeClaim":{"claimName":"web-content"}}]}}'
for i in 1 2 3 4 5 6; do curl -s http://$VIP/; done      # 六次都回 v2，不論打到哪個 Pod

# 4. 擴縮與滾動更新
kubectl -n web-demo scale deploy/web --replicas=5
kubectl -n web-demo rollout restart deploy/web && kubectl -n web-demo rollout status deploy/web
kubectl -n web-demo rollout history deploy/web

# 5. 清掉
kubectl delete ns web-demo
```

部署完，Resources → Network → **VM Load Balancers** 會出現兩個 LB：`vks-cl01` 是叢集 API，`vks-cl01-<hash>` 是 `web-lb`。**Subnets** 會出現分享進來的 `nas-vlan7`，標示 VLAN Extension、VLAN ID 7。

![](img/810-lci-subnets-nas-vlan7.png)
![](img/811-lci-vm-load-balancers.png)

## 13. 遇到的問題與時間

| 問題 | 原因 / 解法 |
|---|---|
| Supervisor RUNNING，但 Resources 分頁只有「安裝 Consumption Interface」，`cci-ns-controller-manager` 一直 Pending | vLCM Apply Solution 裝 spherelet 前的 vSAN health 檢查，被 nested NVMe 的 HCL 項目擋下。task 只寫 general system error，原因在 `vmware-vum-server.log`。靜音 vSAN health 後下一輪就成功（2-1） |
| Service VIP 沒落在 40.0/22 | VIP 從 block 裡最小的可用 IP 開始配，必須把系統段剩餘 IP 和還沒用到的 SubnetSet /24 先排除（3 的 🔑） |
| IP block PATCH 回 520051「visibility and CIDR cannot be updated」 | body 只帶了 `cidrs`。要把 `cidrs` 和 `visibility` 一起帶 |
| NSX VLAN 延伸 subnet 要 static IP | UI 沒有這個選項，用 NSX API 建（8-3） |
| Namespace Networks 的 Subnet 下拉清單是空的 | 要在欄位裡打字搜尋才會列出 |
| 叢集精靈 Node Network 的設定不見 | 填完後回頭展開了 General Settings，精靈會重設。送出前看右側 YAML |
| VKr 上傳（自動化時） | Playwright 經 CDP 不能傳超過 50 MB 的檔案，改用 CDP `DOM.setFileInputFiles` 給本機路徑。手動操作沒有這個問題 |

| 階段 | 時間 |
|---|---|
| nested ESXi ×3 | 3 分 |
| bring-up：vCenter → SDDC Manager → cluster → NSX | 1 小時 46 分（中止與觀察另計 30 分） |
| Transit Gateway + VNA（Large） | 15 分 |
| VKr 上傳 | 約 8 分（和 VNA 同時） |
| Supervisor | 約 20 分，再加 spherelet 約 10 分 |
| VKS 叢集（CP 1 + worker 2） | 9 分 |

## 14. 檔案

| 路徑 | 內容 |
|---|---|
| `m02-spec.json` | bring-up spec，密碼已拿掉 |
| `scripts/abort_watcher_m02.py` | NSX 完成後中止 bring-up |
| `scripts/vsan_silence_nvme_hcl.py` | 靜音 vSAN health（nested 限定） |
| `scripts/s0_policy.py` … `s7_cluster.py` | UI 操作與截圖，Playwright over CDP |
| `scripts/s2_vkr_upload.py` | VKr 匯入，用 CDP 傳大檔 |
| `manifests/nsx-subnet-nas-vlan7.json` | VLAN 延伸 subnet 的 API body |
| `manifests/vks-cl01-ui-generated.yaml` | 叢集精靈產生的 YAML |
| `manifests/nfs-csi/` | NFS CSI driver、StorageClass、RWX demo |
| `manifests/web-demo.yaml`、`web-demo-commands.md` | 部署服務 demo |
| `cli/*.txt` | 各步驟的指令輸出 |
