"""VKS 前置:VM Storage Policy vks-ftt0(OSA、No data redundancy、Force provisioning)— 逐頁操作 + 截圖
  python s0_policy.py name | next | page | shot <n> | eval <js>"""
import re, sys, time
from playwright.sync_api import sync_playwright
SH = "E:/9.1/doc-shots/m02-vks/"
def pg(b): return [x for c in b.contexts for x in c.pages if "vcf-m02-vc01" in x.url][0]
def modal(p): return p.locator(".modal-dialog:visible").last
def btn(p, t):
    c = [x for x in p.get_by_role("button", name=re.compile(r"^\s*" + re.escape(t) + r"\s*$", re.I)).all() if x.is_visible()]
    if not c: raise SystemExit("no visible button " + t)
    return c[-1]
def dump(p):
    m = modal(p)
    print(m.inner_text()[-1800:])
    for e in m.locator("input:visible, select:visible").all():
        t = e.get_attribute("type")
        v = e.is_checked() if t in ("checkbox", "radio") else (e.input_value()[:40] if e.evaluate("x=>x.tagName")!="SELECT" else e.evaluate("x=>x.options[x.selectedIndex]?.text"))
        print("  FIELD", e.get_attribute("id"), e.get_attribute("formcontrolname"), t, v)
with sync_playwright() as pw:
    b = pw.chromium.connect_over_cdp("http://127.0.0.1:9222"); p = pg(b); p.set_default_timeout(30000)
    cmd, a = sys.argv[1], sys.argv[2:]
    if cmd == "name":
        m = modal(p); n = m.locator("input[formcontrolname=profileName]"); n.fill("vks-ftt0"); n.blur(); time.sleep(1)
        k = m.locator("input[formcontrolname=k8sProfileName]")
        if not k.input_value(): k.fill("vks-ftt0"); k.blur()
        m.locator("[formcontrolname=profileDescription]").fill("VKS / Supervisor on nested vSAN OSA: FTT=0 + force provisioning"); time.sleep(1)
        p.screenshot(path=SH + "011-policy-name.png"); dump(p)
    elif cmd == "next":
        btn(p, a[0] if a else "NEXT").click(); time.sleep(4); dump(p)
    elif cmd == "page": dump(p)
    elif cmd == "shot": p.screenshot(path=SH + a[0] + ".png"); print("shot", a[0])
    elif cmd == "eval": print(p.evaluate(a[0]))
