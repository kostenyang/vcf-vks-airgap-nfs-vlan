"""VKS 文件用:接有頭 Chrome(CDP 9222)操作 m06 vSphere Client,邊做邊截圖
  python pw_vc.py open                    # 開 https://vcf-m02-vc01.home.lab/ui 並登入(SSO)
  python pw_vc.py go "<ui 路徑或完整 URL>"
  python pw_vc.py text [n]                # 可見文字(含 modal / wizard)
  python pw_vc.py shot <name>             # E:/9.1/doc-shots/m02-vks/<name>.png
  python pw_vc.py click "<可見文字>" [秒] # button 優先,其次任何元素(exact)
  python pw_vc.py eval "<js>"
"""
import os, re, sys, time
from playwright.sync_api import sync_playwright

VC = "https://vcf-m02-vc01.home.lab"
SHOT = os.environ.get("SHOTDIR", "E:/9.1/doc-shots/m02-vks/")
os.makedirs(SHOT, exist_ok=True)
PW = os.environ["LABPW"]


def page(b):
    for c in b.contexts:
        for p in c.pages:
            if "vcf-m02-vc01" in p.url:
                return p
    p = b.contexts[0].new_page()
    p.goto(VC + "/ui/", wait_until="domcontentloaded")
    return p


def login(p):
    time.sleep(4)
    if p.locator("#username").count():
        p.fill("#username", "administrator@vsphere.local")
        p.fill("#password", PW)
        p.locator("#submit, button[type=submit]").first.click()
        time.sleep(15)


def vis_text(p):
    return p.evaluate("""() => {
      const m=[...document.querySelectorAll('.modal-dialog,[role=dialog],clr-wizard')].filter(x=>x.offsetParent);
      const t=(m.length? m.map(x=>x.innerText).join(String.fromCharCode(10)+'=== MODAL ==='+String.fromCharCode(10)) + String.fromCharCode(10)+'=== PAGE ==='+String.fromCharCode(10) : '') + document.body.innerText;
      return t.split(String.fromCharCode(10)).map(s=>s.trim()).filter(Boolean).join(String.fromCharCode(10));
    }""")


def main():
    cmd, args = sys.argv[1], sys.argv[2:]
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        p = page(b); p.bring_to_front()
        p.set_default_timeout(45000)
        if cmd == "open":
            p.bring_to_front(); login(p); print(p.url)
        elif cmd == "go":
            u = args[0] if args[0].startswith("http") else VC + args[0]
            p.goto(u, wait_until="domcontentloaded"); time.sleep(float(args[1]) if len(args) > 1 else 12)
            login(p); print(p.url)
        elif cmd == "text":
            print(p.url)
            n = int(args[0]) if args else 6000
            for f in p.frames:
                try:
                    t = vis_text(f) if f != p.main_frame else vis_text(p)
                except Exception:
                    continue
                if t.strip():
                    print("##### FRAME", (f.url or "main")[:110]); print(t[:n])
        elif cmd == "shot":
            p.screenshot(path=SHOT + args[0] + ".png"); print("saved", SHOT + args[0] + ".png")
        elif cmd == "click":
            t = args[0]
            done = False
            for f in [p.main_frame] + [x for x in p.frames if x != p.main_frame]:
                rx = re.compile(r"^\s*" + re.escape(t) + r"\s*$", re.I)
                for loc in (f.get_by_role("button", name=t, exact=True), f.get_by_role("link", name=t, exact=True), f.get_by_text(t, exact=True),
                            f.get_by_role("button", name=rx), f.get_by_role("link", name=rx), f.get_by_text(rx)):
                    try:
                        if loc.count() and loc.first.is_visible():
                            loc.first.click(); done = True; break
                    except Exception:
                        pass
                if done: break
            if not done: raise SystemExit("not found: " + t)
            time.sleep(float(args[1]) if len(args) > 1 else 4)
            print("clicked", t)
        elif cmd == "eval":
            print(p.evaluate(args[0]))


if __name__ == "__main__":
    main()
