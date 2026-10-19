"""m06 bring-up 精靈用:接既有有頭 Chrome(CDP 9222)操作 vcf-m02-nsx01 的 Installer UI(改自 m04/pw_inst.py)
  python pw_inst6.py open                    # 開分頁 + 登入(admin@local,密碼從 LABPW)
  python pw_inst6.py text [n]                # 印目前頁面可見文字(含 modal)
  python pw_inst6.py shot <name>             # 存 SHOTDIR(預設 E:/9.1/doc-shots/m02-vks/)<name>.png
  python pw_inst6.py click "<文字>" [秒]      # 點 button / 連結 / label(依可見文字,不分大小寫,完全符合)
  python pw_inst6.py fields                  # 列出可見 input/select(id / name / formcontrolname / label / value)
  python pw_inst6.py fill "<css>" "<值>"      # 填欄位(fill + blur)
  python pw_inst6.py check "<label 文字>"     # 勾 checkbox / radio(依 label)
  python pw_inst6.py select "<css>" "<label>"
  python pw_inst6.py eval "<js>"
"""
import os, re, sys, time
from playwright.sync_api import sync_playwright

URL = "https://vcf-m02-nsx01.home.lab/"
SHOT = os.environ.get("SHOTDIR", "E:/9.1/doc-shots/m02-vks/")
os.makedirs(SHOT, exist_ok=True)
RX = lambda t: re.compile(r"^\s*" + re.escape(t) + r"\s*$", re.I)


def page(b):
    for c in b.contexts:
        for p in c.pages:
            if "vcf-m02-nsx01" in p.url:
                return p
    p = b.contexts[0].new_page()
    p.goto(URL, wait_until="domcontentloaded")
    return p


def visible_text(p):
    return p.evaluate("""() => {
      const m=[...document.querySelectorAll('.modal-dialog,[role=dialog]')].filter(x=>x.offsetParent);
      const t=(m.length? m.map(x=>x.innerText).join('\\n=== MODAL ===\\n') + '\\n=== PAGE ===\\n' : '') + document.body.innerText;
      return t.split('\\n').map(s=>s.trim()).filter(Boolean).join('\\n');
    }""")


FIELDS_JS = """() => [...document.querySelectorAll('input,select,textarea')]
  .filter(x => x.offsetParent || x.type=='radio' || x.type=='checkbox')
  .map(x => x.tagName + ' id=' + x.id + ' name=' + (x.getAttribute('formcontrolname')||x.name||'') + ' type=' + x.type
       + ' val=' + (x.type=='password' ? (x.value ? '***' : '') : x.value) + ' chk=' + x.checked + ' dis=' + x.disabled
       + ' lbl=' + (x.labels && x.labels[0] ? x.labels[0].innerText.trim().slice(0,60) : (x.getAttribute('aria-label')||x.placeholder||'')))"""


def main():
    cmd, args = sys.argv[1], sys.argv[2:]
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        p = page(b); p.bring_to_front()
        p.set_default_timeout(30000)
        if cmd == "open":
            p.bring_to_front(); time.sleep(5)
            if p.locator("input[type=password]").count():
                p.locator("input[type=text],input[type=email]").first.fill(os.environ.get("NSXUSER", "admin"))
                p.locator("input[type=password]").first.fill(os.environ["LABPW"])
                p.locator("input[type=password]").first.press("Enter")
                time.sleep(8)
            print(p.url); print(visible_text(p)[:3000])
        elif cmd == "text":
            print(p.url); print(visible_text(p)[: int(args[0]) if args else 6000])
        elif cmd == "shot":
            time.sleep(1); p.screenshot(path=SHOT + args[0] + ".png"); print("saved", SHOT + args[0] + ".png")
        elif cmd == "click":
            t = args[0]
            for loc in (p.get_by_role("button", name=RX(t)), p.get_by_role("link", name=RX(t)),
                        p.get_by_role("tab", name=RX(t)), p.get_by_label(RX(t)), p.get_by_text(RX(t))):
                vis = [loc.nth(i) for i in range(loc.count()) if loc.nth(i).is_visible()]
                if vis:
                    vis[-1].click(); time.sleep(float(args[1]) if len(args) > 1 else 3); print("clicked", t); return
            raise SystemExit("not found: " + t)
        elif cmd == "fields":
            print("\n".join(p.evaluate(FIELDS_JS)))
        elif cmd == "fill":
            el = p.locator(args[0]).first; el.fill(args[1]); el.blur(); time.sleep(0.6)
            print("value:", "***" if el.get_attribute("type") == "password" else el.input_value())
        elif cmd == "check":
            p.get_by_label(args[0], exact=False).first.check(force=True); time.sleep(1.5); print("checked", args[0])
        elif cmd == "select":
            p.locator(args[0]).first.select_option(label=args[1]); time.sleep(1.5); print("selected", args[1])
        elif cmd == "eval":
            print(p.evaluate(args[0]))


if __name__ == "__main__":
    main()
