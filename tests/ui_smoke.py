"""Prova di fumo dell'interfaccia con Playwright (NON eseguita da pytest di default).

Uso:
    pip install playwright && playwright install chromium
    EAFCMETA_DB=/tmp/ui.db python -m eafcmeta.importer data/esempio.csv
    EAFCMETA_DB=/tmp/ui.db uvicorn eafcmeta.api:app --port 8780 &
    python tests/ui_smoke.py [http://127.0.0.1:8780/]

Controlla: caricamento senza errori JS, navigazione, ricerca con "/", filtri, viste griglia/elenco/tabella,
drawer di dettaglio, parere, confronto, importazione (anteprima), calibrazione, tema, nessun overflow orizzontale.
"""
import glob
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8780/"
errors, checks = [], []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))


def exe():
    found = glob.glob("/opt/pw-browsers/chromium*/chrome-linux*/chrome")
    return found[0] if found else None


def no_hscroll(pg, name):
    w = pg.evaluate("[document.documentElement.scrollWidth, innerWidth]")
    ok(f"nessun overflow orizzontale ({name})", w[0] <= w[1] + 1, str(w))


def main():
    with sync_playwright() as p:
        kw = {"executable_path": exe()} if exe() else {}
        b = p.chromium.launch(args=["--no-sandbox"], **kw)
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark")
        pg = ctx.new_page()
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(BASE + "#/carte")
        pg.wait_for_selector(".cw-open", timeout=10000)
        n = pg.locator(".results .cw").count()
        ok("griglia con carte", n > 0, str(n))
        ok("hero con contatori", pg.locator(".kpi").count() == 4)

        # ricerca con "/"
        pg.keyboard.press("/")
        ok("'/' mette il focus sulla ricerca", pg.evaluate("document.activeElement.id") == "q")
        pg.keyboard.type("Demo 1")
        pg.wait_for_timeout(300)
        m = pg.locator(".results .cw").count()
        ok("la ricerca filtra", 0 < m < n, f"{m}/{n}")
        pg.keyboard.press("Escape")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(300)
        ok("Esc azzera la ricerca", pg.locator(".results .cw").count() == n)

        # filtri a chip
        pg.click('[data-pos="ST"]')
        pg.wait_for_timeout(200)
        ok("filtro posizione", 0 < pg.locator(".results .cw").count() < n)
        pg.click('[data-pos=""]')
        pg.click('[data-verdict="MUST_DO"]')
        pg.wait_for_timeout(200)
        ok("filtro verdetto", 0 < pg.locator(".results .cw").count() < n)
        pg.click("#reset")

        # viste
        pg.click('[data-layout="list"]')
        ok("vista elenco", pg.locator(".lrow").count() == n)
        pg.click('[data-layout="table"]')
        ok("vista tabella", pg.locator(".tbl tbody tr").count() == n)
        first = pg.locator(".tbl tbody tr .tname b").first.inner_text()
        pg.click('[data-sort="name"]')
        pg.wait_for_timeout(150)
        ok("tabella ordinabile", pg.locator(".tbl tbody tr .tname b").first.inner_text() != first)
        pg.click('[data-layout="grid"]')

        # dettaglio + parere
        pg.locator(".results .cw-open").nth(3).click()
        pg.wait_for_selector("#dtBody", timeout=5000)
        ok("drawer di dettaglio aperto", pg.locator("#dDetail[open]").count() == 1)
        ok("radar e barre presenti", pg.locator("#dDetail .radar").count() == 1 and pg.locator("#dDetail .sbar").count() > 3)
        title = pg.inner_text("#dtTitle")
        pg.keyboard.press("ArrowRight")
        pg.wait_for_timeout(600)
        ok("frecce sfogliano le carte", pg.inner_text("#dtTitle") != title)
        pg.fill("#oC", "Creator Prova")
        pg.click('[data-stance="no"]')
        pg.fill("#oR", "Parere di prova dalla prova di fumo")
        pg.click("#bOp")
        pg.wait_for_selector("text=Creator Prova", timeout=5000)
        ok("parere salvato", pg.locator(".oprow", has_text="Creator Prova").count() == 1)
        pg.locator('.oprow:has-text("Creator Prova") [data-del]').click()
        pg.click(".dlg-act .btn.danger")
        pg.wait_for_timeout(800)
        ok("parere eliminato", pg.locator(".oprow", has_text="Creator Prova").count() == 0)
        pg.click("#bCmp")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(300)
        ok("Esc chiude il drawer", pg.locator("#dDetail[open]").count() == 0)

        # confronto
        pg.evaluate("document.querySelectorAll('.results .cw-sel')[5].click()")
        pg.wait_for_timeout(200)
        ok("vassoio confronto visibile", not pg.locator("#tray").is_hidden())
        pg.click("#trayGo")
        pg.wait_for_selector(".cmp-grid", timeout=8000)
        ok("confronto con radar sovrapposto", pg.locator(".cmp-col").count() == 2 and pg.locator("#main .radar .rd-s").count() == 2,
           f"{pg.locator('.cmp-col').count()} colonne, {pg.locator('#main .radar .rd-s').count()} serie")

        # altre pagine
        for sec, sel in (("importa", "#impText"), ("calibra", ".cal"), ("regole", ".soon"), ("ricerca", ".soon")):
            pg.goto(BASE + "#/" + sec)
            pg.wait_for_selector(sel, timeout=5000)
            ok(f"pagina {sec}", True)
        pg.goto(BASE + "#/importa")
        pg.fill("#impText", "name;version;position;price;acceleration;sprint_speed\nProva UI;Test;ST;12k;80;80")
        pg.click("#bPrev")
        pg.wait_for_selector("#impRes:not([hidden])", timeout=5000)
        ok("anteprima importazione", pg.locator("#impRes .rpill").count() >= 1)
        pg.click("#tPages")
        ok("scheda pagine salvate", pg.locator("#dz").is_visible())

        # modulo nuova carta
        pg.keyboard.press("n")
        pg.wait_for_selector("#dForm[open]", timeout=3000)
        ok("form nuova carta (tasto N)", pg.locator("#fStats .sf").count() > 3)
        pg.keyboard.press("Escape")

        # tema persistente
        pg.goto(BASE + "#/carte")
        pg.click('.themesw.full [data-theme="light"]')
        pg.reload()
        pg.wait_for_selector(".kpi, .empty, .cw-open", timeout=8000)
        ok("tema chiaro persistente", pg.evaluate("document.documentElement.dataset.theme") == "light")
        pg.click('.themesw.full [data-theme="auto"]')

        # responsive: nessun overflow orizzontale
        for w in (360, 390, 820, 1440):
            pg.set_viewport_size({"width": w, "height": 900})
            for sec in ("carte", "confronta", "importa", "calibra", "regole"):
                pg.goto(BASE + "#/" + sec)
                pg.wait_for_timeout(500)
                no_hscroll(pg, f"{sec} @{w}")
        b.close()
    ok("nessun errore JS in console", not errors, "; ".join(errors[:3]))
    bad = [n for n, c in checks if not c]
    print(f"\n{len(checks) - len(bad)}/{len(checks)} controlli superati")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
