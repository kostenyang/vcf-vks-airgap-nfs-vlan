"""Step 6(UI 截圖):LCI → Network service → SubnetSets → NEW SUBNETSET(nodes、Public、256 IPs = 整段 /24)
   用法:python s6_subnetset.py vks-lab 46   (外層頁面會先導到該 namespace 的 Resources 深連結)"""
import re, sys, time
from playwright.sync_api import sync_playwright

SH = "E:/9.1/doc-shots/m02-vks/"
U = __import__("os").environ["VCUUID"]
NS, PFX = sys.argv[1], sys.argv[2]
URL = ("https://vcf-m02-vc01.home.lab/ui/app/workload-platform/namespace/urn:vapi:com.vmware.wcp.WorkloadModel:" + NS + ":" + U
       + "/resources/WCP~com.vmware.cci-ns~9.1.2.0~SUPERVISOR_NAMESPACE_MODEL~RESOURCES_VIEW~~0")
RX_NEW = re.compile(r"NEW SUBNETSET", re.I)


def plugin_frame(p):
    fs = [x for x in p.frames if "plugins/com.vmware.cci-ns" in x.url and "modals" not in x.url]
    return fs[-1] if fs else None


def main():
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        p = [x for c in b.contexts for x in c.pages if "vcf-m02-vc01" in x.url][0]
        p.bring_to_front(); p.set_default_timeout(30000)
        shot = lambda n: (time.sleep(1), p.screenshot(path=SH + PFX + n + ".png"), print("shot", PFX + n))
        if NS not in p.url or "RESOURCES_VIEW" not in p.url:
            p.goto(URL, wait_until="domcontentloaded"); time.sleep(20)
        for _ in range(3):
            x = p.locator("button[aria-label*='lose']").first
            if x.count() and x.is_visible():
                x.click(); time.sleep(1)
        f = None
        for i in range(40):
            f = plugin_frame(p)
            try:
                t = f.evaluate("() => document.body.innerText") if f else ""
            except Exception:
                t = ""
            if t.strip():
                break
            time.sleep(3)
        if not f:
            raise SystemExit("no plugin frame")
        # 外掛會記住上次路由:確認是這個 namespace,否則回首頁
        if not t.startswith(NS):
            f.evaluate("ns => { location.hash = '#/namespaces/' + ns; location.reload(); }", NS); time.sleep(10)
        for i in range(20):
            f = plugin_frame(p)
            try:
                t = f.evaluate("() => document.body.innerText")
            except Exception:
                time.sleep(3); continue
            if f.get_by_role("button", name=RX_NEW).count():
                break
            tab = f.get_by_role("button", name=re.compile(r"^\s*SubnetSets\s*$"))
            if tab.count() and tab.first.is_visible():
                tab.first.click(); time.sleep(6); continue
            btns = [x for x in f.get_by_role("button", name=re.compile(r"^\s*GO TO SERVICE\s*$", re.I)).all() if x.is_visible()]
            ns_txt = f.get_by_text("Network Service", exact=True)
            if btns and ns_txt.count():
                if i == 0:
                    shot("0-lci-home")
                tb = ns_txt.first.bounding_box()
                best = min(btns, key=lambda x: abs((x.bounding_box() or {'y': 0})['y'] - tb['y']) + abs((x.bounding_box() or {'x': 0})['x'] - tb['x']))
                best.click(); time.sleep(8); continue
            time.sleep(3)
        f = plugin_frame(p)
        assert f.evaluate("() => document.body.innerText").startswith(NS), "wrong namespace in LCI"
        shot("1-lci-subnetsets")
        f.get_by_role("button", name=RX_NEW).first.click()
        m = None
        for i in range(12):
            time.sleep(3); mf = [x for x in p.frames if "modals" in x.url]
            try:
                if mf and mf[-1].evaluate("() => document.body.innerText").strip():
                    m = mf[-1]; break
            except Exception:
                pass
        if m is None:
            raise SystemExit("no modal")
        m.locator("input[placeholder='Enter Name']").fill("nodes")
        sels = m.locator("select").all()
        sels[0].select_option(label=" Public "); time.sleep(1)
        sels[1].select_option(label=" 256 IPs (/24 network) "); time.sleep(1)
        print("dialog:", m.evaluate("() => document.body.innerText")[:160].replace("\n", " | "))
        shot("2-new-subnetset")
        print("create:", m.evaluate("() => { const b=[...document.querySelectorAll('button')].find(x=>/^\s*CREATE\s*$/i.test(x.innerText)); if(!b) return 'nobtn'; b.click(); return 'clicked'; }")); time.sleep(8)
        shot("3-subnetset-created")
        f = plugin_frame(p)
        print(f.evaluate("() => document.body.innerText")[-250:].replace("\n", " | "))


if __name__ == "__main__":
    main()
