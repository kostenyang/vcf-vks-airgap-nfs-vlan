"""m02(改自 m06):監看 VCF Installer bring-up,VCF Management Services 一開始就中止(改自 vks-airgap-novcfms/scripts/abort_watcher.py)
觸發:subtask 'Deploy VCF Management Services via SDDC Manager'(或其後的 Monitor 任務)離開閒置狀態
中止:installer `systemctl stop domainmanager` → SDDC Manager `systemctl restart domainmanager`(砍掉進行中的 VSP 任務)
      → 刪掉 inner vC 上 VSP 相關 VM → installer `systemctl start domainmanager` → 再盯 30 分鐘確認沒有重送
觸發前 bring-up 就失敗:只回報,不做中止動作
用法:LAB_PASSWORD=... python abort_watcher_m06.py <sddc-id> [--dry-run]
"""
import os, sys, time, json, datetime, ssl
sys.path.insert(0, r"E:\9.1\vks-airgap-novcfms\scripts")
os.environ.setdefault("LAB_PASSWORD", "")
from installer_api import Api, flatten_errors
from installer_root import run_root

INST, SDDCM, VC = "10.0.1.135", "10.0.1.18", "10.0.1.19"
SID = sys.argv[1]; DRY = "--dry-run" in sys.argv
TRIGGERS = ("Deploy VCF Management Services via SDDC Manager", "Monitor VCF Management Services Deployment Task")
IDLE = {None, "", "NOT_STARTED", "PENDING", "SCHEDULED", "INITIALIZED"}
VM_PREFIX = ("vcf-m02-vsp01", "bootstrap-vm", "vcf-services-runtime-template")
LOG = open(r"E:\9.1\m02\abort-watcher.log", "a", encoding="utf-8")


def log(*a):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} " + " ".join(str(x) for x in a)
    print(line, flush=True); LOG.write(line + "\n"); LOG.flush()


def vsp_vms(destroy=False):
    from pyVim.connect import SmartConnect, Disconnect
    from pyVmomi import vim
    si = SmartConnect(host=VC, user="administrator@vsphere.local", pwd=os.environ["LAB_PASSWORD"],
                      sslContext=ssl._create_unverified_context())
    c = si.content
    vms = [v for v in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view
           if v.name.startswith(VM_PREFIX)]
    names = [v.name for v in vms]
    if destroy:
        for v in vms:
            if DRY: log("  [dry-run] would destroy", v.name); continue
            try:
                if v.runtime.powerState == "poweredOn":
                    t = v.PowerOffVM_Task()
                    while t.info.state in ("queued", "running"): time.sleep(2)
                t = v.Destroy_Task()
                while t.info.state in ("queued", "running"): time.sleep(2)
                log("  destroyed", v.name, t.info.state)
            except Exception as ex:
                log("  destroy failed", v.name, ex)
    Disconnect(si)
    return names


api = Api(base=f"https://{INST}")
last = {}; last_run = None
log(f"watching sddc {SID} on installer {INST} dry_run={DRY}")
while True:
    try:
        s, d = api.req(f"/v1/sddcs/{SID}")
    except Exception as ex:
        log("GET error", ex); time.sleep(20); continue
    if s != 200:
        log("GET failed", s, str(d)[:200]); time.sleep(20); continue
    ms = {m["name"]: m.get("status") for m in d.get("milestones", [])}
    if ms != last:
        log("milestones:", json.dumps(ms, ensure_ascii=False)); last = ms
    subs = d.get("sddcSubTasks", [])
    running = [x["name"] for x in subs if x.get("status") == "IN_PROGRESS"]
    if running and running != last_run:
        log("  running:", running[:3]); last_run = running
    # 雙保險:NSX milestone 已完成才算數;PREVALIDATION_* 是部署前的預檢,不是真的開始跑
    nsx_done = ms.get("Deploy and configure NSX") == "COMPLETED_WITH_SUCCESS"
    hit = [x for x in subs if (x["name"] in TRIGGERS or "VCF Management Services" in x["name"])
           and x.get("status") not in IDLE and not str(x.get("status")).startswith("PREVALIDATION")]
    if hit and not nsx_done:
        log("  (VCFMS subtask 狀態變了但 NSX milestone 還沒完成,不觸發):", [(x["name"], x["status"]) for x in hit][:2])
        hit = []
    if hit:
        log(">>> TRIGGER:", [(x["name"], x["status"]) for x in hit]); break
    failed = [x for x in subs if "FAIL" in (x.get("status") or "") and not str(x.get("status")).startswith("PREVALIDATION")]
    if d.get("status") in ("COMPLETED_WITH_FAILURE", "FAILED") or failed:
        log("!!! bring-up FAILED before trigger — NOT aborting. status:", d.get("status"))
        for x in failed[:5]:
            log("   failed subtask:", x["name"], x.get("status"), flatten_errors(x.get("errorResponse") or {})[:3])
        sys.exit(2)
    time.sleep(10)

# ---- 中止
log(f"step 1: stop domainmanager on installer {INST}")
if not DRY: log(run_root(INST, "systemctl stop domainmanager; systemctl is-active domainmanager"))
log(f"step 2: restart domainmanager on SDDC Manager {SDDCM}(砍掉進行中的 VSP 任務)")
if not DRY: log(run_root(SDDCM, "systemctl restart domainmanager; sleep 5; systemctl is-active domainmanager"))
log("step 3: 等 60 秒,刪除 inner vCenter 上 VSP 相關 VM")
time.sleep(60)
log("  VSP-looking VMs:", vsp_vms(destroy=True) or "none")
log(f"step 4: start domainmanager on installer {INST}")
if not DRY: log(run_root(INST, "systemctl start domainmanager; sleep 5; systemctl is-active domainmanager"))
log("ABORT COMPLETE — 之後 30 分鐘每 2 分鐘確認沒有重送 / 沒有新的 VSP VM")
t0 = time.time()
while time.time() - t0 < 1800:
    time.sleep(120)
    try:
        api = Api(base=f"https://{INST}")
        s, d = api.req(f"/v1/sddcs/{SID}")
        st = d.get("status") if s == 200 else s
        run = [x["name"] for x in d.get("sddcSubTasks", []) if x.get("status") == "IN_PROGRESS"] if s == 200 else []
    except Exception as ex:
        st, run = f"err {ex}", []
    try:
        left = vsp_vms()
    except Exception as ex:
        left = f"err {ex}"
    log(f"  post-check installer status={st} running={run[:2]} vsp_vms={left}")
log("POST-CHECK DONE")
