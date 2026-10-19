"""Step 5(UI 截圖):namespace Summary → ADD STORAGE(vks-ftt0)、ADD VM CLASS(best-effort-xsmall/small/medium)
   用法:python s5_ns_config.py vks-lab 43"""
import re, sys, time
from playwright.sync_api import sync_playwright

SH = "E:/9.1/doc-shots/m02-vks/"
U = __import__("os").environ["VCUUID"]
NS, PFX = sys.argv[1], sys.argv[2]
URL = "https://vcf-m02-vc01.home.lab/ui/app/workload-platform/namespace/urn:vapi:com.vmware.wcp.WorkloadModel:" + NS + ":" + U + "/summary"
POLICY = "vks-ftt0"
CLASSES = ["best-effort-xsmall", "best-effort-small", "best-effort-medium"]


def main():
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        p = [x for c in b.contexts for x in c.pages if "vcf-m02-vc01" in x.url][0]
        p.bring_to_front(); p.set_default_timeout(30000)
        shot = lambda n: (time.sleep(1), p.screenshot(path=SH + PFX + n + ".png"), print("shot", PFX + n))
        p.goto(URL, wait_until="domcontentloaded"); time.sleep(14)
        for _ in range(3):
            x = p.locator("button[aria-label*='lose']").first
            if x.count() and x.is_visible():
                x.click(); time.sleep(1)
        shot("0-ns-summary")
        # ---- storage
        p.get_by_role("button", name=re.compile("ADD STORAGE", re.I)).first.click(); time.sleep(4)
        d = p.locator(".modal-dialog:visible").last
        print("--- storage dialog:", d.inner_text()[:600].replace("\n", " | "))
        row = d.locator("clr-dg-row", has_text=POLICY).first
        if row.count():
            row.locator("input[type=checkbox], input[type=radio]").first.check(force=True); time.sleep(1)
        else:
            print("policy row not found")
        shot("1-add-storage")
        for name in ("OK", "ADD", "SAVE"):
            bt = d.get_by_role("button", name=re.compile(rf"^\s*{name}\s*$", re.I)).last
            if bt.count() and bt.is_visible() and bt.is_enabled():
                bt.click(); print("clicked", name); break
        time.sleep(5)
        # ---- VM classes
        p.get_by_role("button", name=re.compile("ADD VM CLASS", re.I)).first.click(); time.sleep(4)
        d = p.locator(".modal-dialog:visible").last
        print("--- vm class dialog:", d.inner_text()[:400].replace("\n", " | "))
        for c in CLASSES:
            row = d.locator("clr-dg-row", has_text=re.compile(rf"\b{re.escape(c)}\b")).first
            if row.count():
                row.locator("input[type=checkbox]").first.check(force=True); time.sleep(0.8)
            else:
                print("class row not found:", c)
        shot("2-add-vm-class")
        for name in ("OK", "ADD", "SAVE"):
            bt = d.get_by_role("button", name=re.compile(rf"^\s*{name}\s*$", re.I)).last
            if bt.count() and bt.is_visible() and bt.is_enabled():
                bt.click(); print("clicked", name); break
        time.sleep(6)
        shot("3-ns-summary-configured")


if __name__ == "__main__":
    main()
