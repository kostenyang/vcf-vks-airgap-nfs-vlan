"""Step 1(UI 截圖):vCenter → NSX plugin → Transit Gateway → Setup Network Connectivity
   Distributed VLAN 8 / gw 192.168.47.254/20、IP block vks-ext-block 192.168.32.0/20(excluded 32.35–32.255、35–39、44–47)、VNA vks-vna Large"""
import re, time, os
from playwright.sync_api import sync_playwright

SH = "E:/9.1/doc-shots/m02-vks/"
U = __import__("os").environ["VCUUID"]
VC = "https://vcf-m02-vc01.home.lab"
URL = (VC + "/ui/app/folder;nav=n/urn:vmomi:Folder:group-d1:" + U +
       "/networks/plugin/com.vmware.nsx.management.nsxt.networking/"
       "VC~com.vmware.nsx.management.nsxt.networking~9.1.1.0.25691509~navigable~plugin.network.connectivity.tgws")
VLAN, GW = "8", "192.168.47.254/20"
BLOCK, CIDR = "vks-ext-block", "192.168.32.0/20"
EXCL = ["192.168.32.35-192.168.32.255", "192.168.35.0-192.168.39.255", "192.168.44.0-192.168.47.255"]
VNA_NAME, VNA_NODE, VNA_IP = "vks-vna", "vcf-m02-vna01a.home.lab", "10.0.1.28/23"


def main():
    os.makedirs(SH, exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        p = [x for c in b.contexts for x in c.pages if "vcf-m02-vc01" in x.url][0]
        p.bring_to_front(); p.set_default_timeout(30000)
        p.goto(URL, wait_until="domcontentloaded"); time.sleep(18)
        for _ in range(4):
            x = p.locator("button[aria-label*='lose']").first
            if x.count() and x.is_visible():
                x.click(); time.sleep(1)
        shot = lambda n: (time.sleep(1), p.screenshot(path=SH + n + ".png"), print("shot", n))
        f = [x for x in p.frames if "/plugins/com.vmware.nsx" in x.url][0]
        shot("110-transit-gateways")

        def btn(text):
            f.evaluate("t => { const ds=[...document.querySelectorAll('.modal-dialog')].filter(x=>x.offsetParent); const d=ds[ds.length-1]; [...d.querySelectorAll('button')].find(b=>b.offsetParent && b.innerText.trim()===t).click(); }", text)

        f.get_by_role("button", name=re.compile("SETUP NETWORK CONNECTIVITY", re.I)).first.click(); time.sleep(6)
        shot("111-setup-p1-distributed-vlan")
        btn("NEXT"); time.sleep(4)
        d = f.locator(".modal-dialog:visible").last
        d.locator("#select-all").check(force=True); time.sleep(1)
        shot("112-setup-prereq")
        btn("CONTINUE"); time.sleep(5)
        d = f.locator(".modal-dialog:visible").last
        d.locator("#vlan_id-value").fill(VLAN); d.locator("#vlan_id-value").blur()
        g = d.locator("#gateway_addresses-value"); g.click(); g.type(GW, delay=30); g.press("Enter"); time.sleep(1)
        shot("113-setup-p2-vlan-gateway")
        d.get_by_role("button", name="Available actions").click(); time.sleep(2)
        f.get_by_text("Create New", exact=True).first.click(); time.sleep(4)
        d2 = f.locator(".modal-dialog:visible").last
        d2.locator("#display_name-value").fill(BLOCK)
        c = d2.locator("#cidrs-value"); c.click(); c.type(CIDR, delay=30); c.press("Enter"); time.sleep(1)
        e = d2.locator("#excluded_ips-value")
        for x in EXCL:
            e.click(); e.type(x, delay=30); e.press("Enter"); time.sleep(1.5)
        shot("114-ipblock-vks-ext-block")
        d2.get_by_role("button", name="SAVE").click(); time.sleep(6)
        shot("115-setup-p2-external")
        btn("NEXT"); time.sleep(5)
        d = f.locator(".modal-dialog:visible").last
        d.locator("#edge_cluster_name-value").fill(VNA_NAME); d.locator("#edge_cluster_name-value").blur()
        d.locator("#form_factor-value").click(); time.sleep(2)
        f.locator("[role=option]:visible").filter(has_text="Large").first.click(); time.sleep(2)
        btn("ADD"); time.sleep(4)
        d3 = f.locator(".modal-dialog:visible").last
        n = d3.locator("input[aria-label='Node Name (FQDN)']"); n.fill(VNA_NODE); n.blur(); time.sleep(1)

        def pick(css, txt):
            d3.locator(css).first.click(); time.sleep(2)
            f.locator("[role=option]:visible").filter(has_text=txt).first.click(); time.sleep(2)
        pick("#cluster-value", "vcf-m02-cl01")
        pick("#data_store-value", "vcf-m02-cl01-ds-vsan01")
        d3.get_by_label("Static", exact=True).check(force=True); time.sleep(2)
        m = d3.locator("#management_ip-value"); m.click(); m.type(VNA_IP, delay=30); m.press("Enter"); time.sleep(1)
        gw = d3.locator("#default_gateway-value"); gw.click(); gw.type("10.0.0.1", delay=30); gw.press("Enter"); time.sleep(1)
        pick("#port_group-value", "SDDC-DPortGroup-VM-Mgmt")
        shot("116-vna-add-node")
        btn("APPLY"); time.sleep(4)
        shot("117-vna-cluster-large")
        btn("NEXT"); time.sleep(6)
        shot("118-setup-review")
        btn("DEPLOY"); time.sleep(15)
        shot("119-setup-deploying")
        print(f.evaluate("() => document.body.innerText")[:600])


if __name__ == "__main__":
    main()
