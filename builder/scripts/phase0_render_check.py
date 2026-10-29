import sys, time
from playwright.sync_api import sync_playwright
src, out = sys.argv[1], sys.argv[2]
t=time.time()
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.goto("file://" + src, wait_until="networkidle")
    pg.pdf(path=out, prefer_css_page_size=True, print_background=True)
    # overflow per panel
    res = pg.evaluate("""() => [...document.querySelectorAll('.panel')].map((e,i)=>({i, cls:e.className, over:e.scrollHeight-e.clientHeight}))""")
    print(res)
    b.close()
print("secs", round(time.time()-t,2))
