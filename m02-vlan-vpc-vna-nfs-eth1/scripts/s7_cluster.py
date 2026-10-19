"""Step 7(UI 截圖):LCI → Kubernetes → CREATE CLUSTER(Custom):名稱、VM class、Node Network=SubnetSet nodes、Pods/Services CIDR
   用法:python s7_cluster.py vks-lab vks-cl01 100.112.0.0/18 100.80.0.0/13 50 [--finish]"""
import re, sys, time
from playwright.sync_api import sync_playwright

SH = "E:/9.1/doc-shots/m02-vks/"
NS, NAME, PODS, SVCS, PFX = sys.argv[1:6]
U = __import__("os").environ["VCUUID"]
URL = ("https://vcf-m02-vc01.home.lab/ui/app/workload-platform/namespace/urn:vapi:com.vmware.wcp.WorkloadModel:" + NS + ":" + U
       + "/resources/WCP~com.vmware.cci-ns~9.1.2.0~SUPERVISOR_NAMESPACE_MODEL~RESOURCES_VIEW~~0")
VMCLASS = "best-effort-small"


def pf(p, wait=150):
    for i in range(wait // 3):
        fs = [x for x in p.frames if "plugins/com.vmware.cci-ns" in x.url and "modals" not in x.url]
        if fs:
            try:
                if fs[-1].evaluate("() => document.body.innerText").strip():
                    return fs[-1]
            except Exception:
                pass
        time.sleep(3)
    raise SystemExit("no plugin frame")


def main():
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        p = [x for c in b.contexts for x in c.pages if "vcf-m02-vc01" in x.url][0]
        p.bring_to_front(); p.set_default_timeout(30000)
        shot = lambda n: (time.sleep(1), p.screenshot(path=SH + PFX + n + ".png"), print("shot", PFX + n))

        def click_btn(f, text):
            r = f.evaluate("t => { const b=[...document.querySelectorAll('button')].find(x=>x.offsetParent && x.innerText.trim()===t); if(b){b.click(); return 'ok'} return 'nobtn'; }", text)
            if r != "ok":
                raise SystemExit("button not found: " + text)

        if NS not in p.url or "RESOURCES_VIEW" not in p.url:
            p.goto(URL, wait_until="domcontentloaded"); time.sleep(20)
        for _ in range(3):
            x = p.locator("button[aria-label*='lose']").first
            if x.count() and x.is_visible():
                x.click(); time.sleep(1)
        f = pf(p)
        if "CREATE CLUSTER" not in f.evaluate("() => document.body.innerText"):
            f.evaluate("ns => { location.hash = '#/namespaces/' + ns; location.reload(); }", NS); time.sleep(12); f = pf(p)
        assert f.evaluate("() => document.body.innerText").startswith(NS), "wrong namespace"
        shot("0-lci-home")
        click_btn(f, "CREATE CLUSTER"); time.sleep(12)
        f = pf(p)
        f.get_by_label("Custom Configuration").check(force=True); time.sleep(3)
        shot("1-wizard-1-custom")
        click_btn(f, "NEXT"); time.sleep(6)
        f = pf(p)
        nm = f.get_by_placeholder("Enter a name for this cluster"); nm.fill(NAME); nm.blur(); time.sleep(2)
        for i in range(10):
            r = f.evaluate("c => { const rows=[...document.querySelectorAll('clr-dg-row')].filter(x=>x.innerText.includes(c)); if(!rows.length) return 'norow'; const i=rows[0].querySelector('input[type=radio]'); if(!i) return 'noradio'; i.click(); return 'ok'; }", VMCLASS)
            if r == "ok":
                break
            time.sleep(3)
        print("vmclass radio:", r); time.sleep(1)
        shot("2-wizard-2-general")
        sec = f.get_by_text("Configure Node Network and Pod Network").first
        sec.scroll_into_view_if_needed(); sec.click(); time.sleep(3)
        f.get_by_text("Primary | eth0").first.click(); time.sleep(3)
        cb = f.get_by_label("Use custom primary network")
        if cb.count():
            cb.check(force=True); time.sleep(3)
        sel = f.locator("select").filter(has=f.locator("option", has_text="nodes (Subnet Set)")).first
        sel.select_option(label="nodes (Subnet Set)"); time.sleep(2)
        f.get_by_placeholder("192.168.0.0/16").fill(PODS)
        f.get_by_placeholder("10.96.0.0/12").fill(SVCS); time.sleep(1)
        if "--stop-at-network" in sys.argv:
            print("BUTTONS:", f.evaluate("() => [...document.querySelectorAll('button')].filter(x=>x.offsetParent).map(x=>x.innerText.trim()).filter(Boolean)"))
            return
        f.get_by_text("Pod Network", exact=True).first.scroll_into_view_if_needed(); time.sleep(1)
        shot("3-wizard-2-network")
        click_btn(f, "NEXT"); time.sleep(6)
        shot("4-wizard-3-cp")
        click_btn(f, "NEXT"); time.sleep(6)
        f = pf(p)
        row = f.locator("clr-dg-row", has_text=re.compile(NAME + r"-np")).first
        row.locator("button.datagrid-action-toggle").first.click(); time.sleep(2)
        f.locator("[role=menuitem]:visible, .datagrid-action-overflow button:visible").filter(has_text="Edit").first.click(); time.sleep(4)
        d = f.locator("[role=dialog]:visible, .modal-dialog:visible").last
        n = d.locator("input").first; n.fill(NAME + "-np-1"); n.blur()
        d.locator("#override-node-pool").check(force=True); time.sleep(3)
        d.get_by_role("button", name="NEXT").click(); time.sleep(4)
        print("np vmclass:", f.evaluate("c => { const ds=[...document.querySelectorAll('.modal-dialog,[role=dialog]')].filter(x=>x.offsetParent); const d=ds[ds.length-1]; const rows=[...d.querySelectorAll('clr-dg-row')].filter(x=>x.innerText.includes(c)); if(!rows.length) return 'norow'; const i=rows[0].querySelector('input[type=radio]'); if(!i) return 'noradio'; i.click(); return 'ok'; }", VMCLASS)); time.sleep(1.5)
        shot("5-nodepool-vmclass")
        for _ in range(4):
            d.get_by_role("button", name="NEXT").click(); time.sleep(3)
        d.get_by_role("button", name="FINISH").click(); time.sleep(4)
        shot("6-wizard-4-nodepools")
        click_btn(f, "NEXT"); time.sleep(8)
        y = f.evaluate("() => { try { const NL=String.fromCharCode(10); return window.monaco.editor.getModels().map(m=>m.getValue()).join(NL+'---'+NL); } catch(e) { return 'ERR '+e; } }")
        open("E:/9.1/m02/manifests/" + NAME + "-ui-generated.yaml", "w", encoding="utf-8").write(y)
        print(y[:1800])
        shot("7-wizard-5-review")
        if "--finish" in sys.argv:
            click_btn(f, "FINISH"); time.sleep(15)
            shot("8-cluster-creating")
            print(pf(p).evaluate("() => document.body.innerText")[:500].replace("\n", " | "))
        else:
            print("not finishing")


if __name__ == "__main__":
    main()
