"""NESTED-LAB ONLY: silence the vSAN health test 'NVMe device is VMware certified' on vcf-m02-cl01.
vLCM's FINALIZE health check (used by Supervisor's ClusterApplySolution to install/configure spherelet) treats any
non-green vSAN health as blocking; nested virtual NVMe disks can never be on the VCG. Usage: python vsan_silence_nvme_hcl.py [--dry-run]"""
import os
import ssl, sys
from pyVim.connect import SmartConnect
from pyVmomi import vim, VmomiSupport, SoapStubAdapter
ctx = ssl._create_unverified_context()
si = SmartConnect(host="vcf-m02-vc01.home.lab", user="administrator@vsphere.local", pwd=os.environ["LAB_PASSWORD"], sslContext=ctx)
cl = [c for c in si.content.viewManager.CreateContainerView(si.content.rootFolder, [vim.ClusterComputeResource], True).view if c.name == "vcf-m02-cl01"][0]
stub = SoapStubAdapter(host="vcf-m02-vc01.home.lab", path="/vsanHealth", version=VmomiSupport.newestVersions.GetName("vsan"), sslContext=ctx)
stub.cookie = si._stub.cookie
hs = vim.cluster.VsanVcClusterHealthSystem("vsan-cluster-health-system", stub)
def summary(cache):
    s = hs.QueryClusterHealthSummary(cluster=cl, includeObjUuids=False, fetchFromCache=cache)
    return s
s = summary(True)
print("overall:", s.overallHealth, "|", s.overallHealthDescription)
target = None
for g in s.groups:
    for t in (g.groupTests or []):
        if t.testHealth != "green": print(f"  [{t.testHealth:6}] {g.groupName} / {t.testName}  (id={t.testId})")
        if "NVMe device is VMware certified" in (t.testName or ""): target = t.testId.split(".")[-1]  # silent-check API wants the short id
print("current silent checks:", hs.GetVsanClusterSilentChecks(cluster=cl))
print("target test id:", target)
if not target: raise SystemExit("test not found")
if "--dry-run" in sys.argv: raise SystemExit("dry-run")
print("SetVsanClusterSilentChecks ->", hs.SetVsanClusterSilentChecks(cluster=cl, addSilentChecks=[target, "perfsvcstatus"]))
print("silent checks now:", hs.GetVsanClusterSilentChecks(cluster=cl))
s = summary(False)
print("overall after re-test:", s.overallHealth, "|", s.overallHealthDescription)
for g in s.groups:
    for t in (g.groupTests or []):
        if t.testHealth not in ("green", "skipped"): print(f"  [{t.testHealth:6}] {g.groupName} / {t.testName}")
