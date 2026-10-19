"""vkr-local → ACTIONS → Import item → Local file:用 CDP DOM.setFileInputFiles 給本機路徑(Playwright 經 CDP 不能傳 >50MB)"""
import os, time
from playwright.sync_api import sync_playwright
SH = "E:/9.1/doc-shots/m02-vks/"
D = "E:/9.1/airgap/staging/vkr/ob-25650988-photon-5-amd64-v1.36.2---vmware.2-vkr.3/"
FILES = [os.path.normpath(D + "photon-5-amd64-v1.36.2---vmware.2-vkr.3" + s) for s in (".ovf", "-disk1.vmdk", ".mf", ".cert")]
with sync_playwright() as pw:
    b = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
    p = [x for c in b.contexts for x in c.pages if "vcf-m02-vc01" in x.url][0]
    m = p.locator(".modal-dialog:visible")
    if m.count():
        [x for x in p.get_by_role("button", name="CANCEL").all() if x.is_visible()][-1].click(); time.sleep(3)
    [x for x in p.get_by_role("button", name="ACTIONS").all() if x.is_visible()][0].click(); time.sleep(3)
    p.locator("[role=menuitem]:visible, .dropdown-item:visible").filter(has_text="Import item").first.click(); time.sleep(5)
    m = p.locator(".modal-dialog:visible").last
    m.locator("#radio_local").check(force=True); time.sleep(2)
    cdp = p.context.new_cdp_session(p)
    oid = cdp.send("Runtime.evaluate", {"expression": "document.querySelector('#file-selector')"})["result"]["objectId"]
    print("files:", FILES)
    cdp.send("DOM.setFileInputFiles", {"files": FILES, "objectId": oid}); time.sleep(8)
    print(m.inner_text()[:900]); print("item name:", m.locator("#item-name").input_value())
    p.screenshot(path=SH + "218-cl-import-local-files.png"); print("shot 218")
