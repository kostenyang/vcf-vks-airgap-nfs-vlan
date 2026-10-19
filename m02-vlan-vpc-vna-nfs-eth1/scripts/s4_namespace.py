"""Step 4(UI 截圖):NEW NAMESPACE 精靈 — Create a VPC + Default Subnet Size 256(VLAN-backed 不能手動指定 Public subnet CIDR)
   用法:python s4_namespace.py vks-lab [--finish] [--shot-prefix 41]"""
import re, sys, time
from playwright.sync_api import sync_playwright

SH = "E:/9.1/doc-shots/m02-vks/"
NS = sys.argv[1]
PFX = sys.argv[sys.argv.index("--shot-prefix") + 1] if "--shot-prefix" in sys.argv else "41"


def main():
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        p = [x for c in b.contexts for x in c.pages if "vcf-m02-vc01" in x.url][0]
        p.bring_to_front(); p.set_default_timeout(30000)
        shot = lambda n: (time.sleep(1), p.screenshot(path=SH + PFX + n + ".png"), print("shot", PFX + n))
        p.goto("https://vcf-m02-vc01.home.lab/ui/app/workload-platform/home/namespaces", wait_until="domcontentloaded"); time.sleep(12)
        for _ in range(3):
            x = p.locator("button[aria-label*='lose']").first
            if x.count() and x.is_visible():
                x.click(); time.sleep(1)
        p.get_by_role("button", name=re.compile("NEW NAMESPACE", re.I)).first.click(); time.sleep(5)
        d = p.locator(".modal-dialog:visible").last
        nxt = lambda w=5: (d.get_by_role("button", name=re.compile(r"^\s*NEXT\s*$", re.I)).last.click(), time.sleep(w))
        d.get_by_text("vcf-m02-sv01", exact=True).first.click(); time.sleep(2)
        shot("0-ns-1-location")
        nxt()
        d.locator("#namespaceNameInput").fill(NS); time.sleep(1)
        d.locator("input[type=checkbox]:visible").first.check(force=True); time.sleep(3)
        shot("1-ns-2-configuration")
        nxt()
        # Network Settings:Create a VPC + Default Subnet Size 256(整段 /24 給節點)
        d.get_by_text("Create a VPC", exact=True).first.click(); time.sleep(2)
        d.locator("#nsx-vpc-subnet-size").fill("256"); time.sleep(1)
        print("--- network settings:", d.inner_text()[:700].replace("\n", " | "))
        shot("2-ns-3-network-settings")
        nxt()
        print("--- networks (none to add in VLAN-backed):", d.inner_text()[:300].replace("\n", " | "))
        shot("3-ns-4-networks")
        nxt()
        print("--- zones:", d.inner_text()[:800].replace("\n", " | "))
        z = d.locator("clr-dg-row", has_text="vz-01").first
        if z.count():
            z.locator("input[type=radio], input[type=checkbox]").first.check(force=True); time.sleep(1)
        shot("5-ns-5-zones")
        nxt()
        print("--- review:", d.inner_text()[:1500].replace("\n", " | "))
        shot("6-ns-6-review")
        if "--finish" in sys.argv:
            for name in ("FINISH", "CREATE"):
                bt = d.get_by_role("button", name=re.compile(rf"^\s*{name}\s*$", re.I)).last
                if bt.count() and bt.is_visible():
                    bt.click(); print("clicked", name); break
            time.sleep(15)
            shot("7-ns-created")
        else:
            print("not finishing")


if __name__ == "__main__":
    main()
