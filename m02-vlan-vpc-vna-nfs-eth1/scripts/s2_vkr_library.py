"""VKS 段 2xx:Content Libraries → CREATE vkr-local(Local)→ Import item(本機 4 檔)
  python s2_vkr_library.py page | create | import"""
import re, sys, time
from playwright.sync_api import sync_playwright
SH = "E:/9.1/doc-shots/m02-vks/"
D = "E:/9.1/airgap/staging/vkr/ob-25650988-photon-5-amd64-v1.36.2---vmware.2-vkr.3/"
FILES = [D + "photon-5-amd64-v1.36.2---vmware.2-vkr.3" + s for s in (".ovf", "-disk1.vmdk", ".mf", ".cert")]
def pg(b): return [x for c in b.contexts for x in c.pages if "vcf-m02-vc01" in x.url][0]
def modal(p): return p.locator(".modal-dialog:visible").last
def btn(p, t):
    c = [x for x in p.get_by_role("button", name=re.compile(r"^\s*" + re.escape(t) + r"\s*$", re.I)).all() if x.is_visible()]
    if not c: raise SystemExit("no visible button " + t)
    return c[-1]
def dump(p):
    m = modal(p); print(m.inner_text()[-1200:])
    for e in m.locator("input:visible, select:visible, textarea:visible").all():
        t = e.get_attribute("type")
        v = e.is_checked() if t in ("checkbox", "radio") else e.input_value()[:50]
        print("  FIELD", e.get_attribute("id"), e.get_attribute("name"), e.get_attribute("formcontrolname"), t, v)
shot = lambda p, n: (time.sleep(1), p.screenshot(path=SH + n + ".png"), print("shot", n))
with sync_playwright() as pw:
    b = pw.chromium.connect_over_cdp("http://127.0.0.1:9222"); p = pg(b); p.set_default_timeout(30000)
    cmd, a = sys.argv[1], sys.argv[2:]
    if cmd == "page": dump(p)
    elif cmd == "click": btn(p, a[0]).click(); time.sleep(float(a[1]) if len(a) > 1 else 4); dump(p)
    elif cmd == "shot": shot(p, a[0])
    elif cmd == "label": modal(p).get_by_label(a[0], exact=True).check(force=True); time.sleep(1.5); dump(p)
    elif cmd == "fill": e = modal(p).locator(a[0]).first; e.fill(a[1]); e.blur(); time.sleep(1); dump(p)
    elif cmd == "row":   # 勾 datagrid 中含文字的那一列
        r = modal(p).locator("clr-dg-row", has_text=a[0]).first
        r.locator("input[type=radio], input[type=checkbox]").first.check(force=True); time.sleep(1.5); dump(p)
    elif cmd == "files":
        inp = modal(p).locator("input[type=file]")
        print("file inputs:", inp.count()); inp.first.set_input_files(FILES); time.sleep(6); dump(p)
