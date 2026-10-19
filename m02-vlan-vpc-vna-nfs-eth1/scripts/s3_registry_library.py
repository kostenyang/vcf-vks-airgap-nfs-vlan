"""Step 3(UI 截圖):Supervisor → Configure → Container Registries(Harbor CA)+ General → Kubernetes Service(VKr library)"""
import re, time, sys
from playwright.sync_api import sync_playwright

SH = "E:/9.1/doc-shots/m02-vks/"
SV = ("https://vcf-m02-vc01.home.lab/ui/app/workload-platform/supervisor/urn:vapi:com.vmware.wcp.SupervisorModel:" +
      __import__("os").environ["SVID"] + ":" + __import__("os").environ["VCUUID"] + "/supervisor/configure/")
CA = open("E:/9.1/airgap/harbor01-ca.crt").read().strip()


def main():
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        p = [x for c in b.contexts for x in c.pages if "vcf-m02-vc01" in x.url][0]
        p.bring_to_front(); p.set_default_timeout(30000)
        shot = lambda n: (time.sleep(1), p.screenshot(path=SH + n + ".png"), print("shot", n))
        p.goto(SV + "network", wait_until="domcontentloaded"); time.sleep(10)
        for _ in range(3):
            x = p.locator("button[aria-label*='lose']").first
            if x.count() and x.is_visible():
                x.click(); time.sleep(1)
        # ---- Container Registries
        if "--skip-registry" not in sys.argv:
            p.get_by_text("Container Registries", exact=True).first.click(); time.sleep(5)
            shot("410-container-registries")
            p.get_by_role("button", name=re.compile("ADD REGISTRY", re.I)).first.click(); time.sleep(4)
            d = p.locator(".modal-dialog:visible").last
            d.locator("#wcp-supervisor-registry-name").fill("harbor01")
            d.locator("#wcp-supervisor-registry-host-url").fill("harbor01.home.lab")
            d.locator("#wcp-supervisor-registry-tls-certificate").fill(CA); time.sleep(1)
            shot("411-add-registry-harbor01")
            d.get_by_role("button", name=re.compile(r"^\s*ADD\s*$", re.I)).last.click(); time.sleep(8)
            shot("412-registry-added")
        # ---- Kubernetes Service
        p.get_by_text("General", exact=True).first.click(); time.sleep(4)
        ks = p.get_by_text("Kubernetes Service", exact=True).first
        ks.click(); time.sleep(5)
        print("URL:", p.url)
        shot("413-kubernetes-service")
        txt = p.locator("body").inner_text(); i = txt.find("Kubernetes Service"); print(txt[i:i + 600].replace("\n", " | "))
        cand = [bt for bt in p.locator("button:visible").all() if re.fullmatch(r"\s*(ADD|EDIT|MANAGE)\s*", bt.inner_text(), re.I)]
        print("buttons:", [c.inner_text().strip() for c in cand])
        if not cand:
            print("no library button found"); return
        cand[0].click(); time.sleep(5)
        d = p.locator(".modal-dialog:visible").last
        print("--- dialog:", d.inner_text()[:500].replace("\n", " | "))
        row = d.locator("clr-dg-row", has_text="vkr-local").first
        if row.count():
            inp = row.locator("input[type=radio], input[type=checkbox]").first
            inp.check(force=True); time.sleep(1)
        else:
            print("vkr-local row not found; dialog inputs:")
            for el in d.locator("input:visible, select:visible").all():
                print("  FIELD", el.get_attribute("id"), el.get_attribute("type"))
        shot("414-add-library-vkr-local")
        for name in ("OK", "ADD", "SAVE", "FINISH"):
            bt = d.get_by_role("button", name=re.compile(rf"^\s*{name}\s*$", re.I)).last
            if bt.count() and bt.is_visible() and bt.is_enabled():
                bt.click(); print("clicked", name); break
        time.sleep(8)
        shot("415-library-added")


if __name__ == "__main__":
    main()
