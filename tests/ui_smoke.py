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
import time

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


def motore(pg):
    """Regole, Ricerca pareri, Automatico, import pareri, dettaglio (pesi/automatici/errori), calibrazione.
    Serve un DB con pareri di 3 creator su 12+ carte (criteri 'scatto' accettati dalla ricerca), la carta
    'Chloe Kelly' e qualche parere automatico. Modifica i dati del DB di prova (accetta/rifiuta, annulla automatici)."""
    import json
    import urllib.request

    # --- Regole
    pg.goto(BASE + "#/regole")
    pg.wait_for_selector(".rule", timeout=5000)
    ok("regole: elenco con stato e spiegazione", pg.locator(".rule").count() >= 10 and pg.locator(".rule-fx").count() >= 10 and pg.locator(".rule-d").count() >= 10)
    pg.click("#bLearn")
    pg.wait_for_selector(".learn-res", timeout=8000)
    ok("regole: 'Impara dai pareri' mostra un esito", pg.locator(".learn-res .res-h b").inner_text().strip() != "")
    prop = pg.locator(".rule.st-proposed")
    if prop.count():
        ok("regole: la proposta non e' attiva e ha Approva/Rifiuta", prop.first.locator('[data-decide="active"]').count() == 1 and "non" in prop.first.locator(".rule-act p").inner_text().lower())
        n0 = prop.count()
        prop.first.locator('[data-decide="active"]').click()
        pg.wait_for_timeout(900)
        ok("regole: approvazione", pg.locator(".rule.st-proposed").count() == n0 - 1)
        if pg.locator(".rule.st-proposed").count():
            pg.locator('.rule.st-proposed [data-decide="rejected"]').first.click()
            pg.wait_for_timeout(900)
            ok("regole: rifiuto", pg.locator(".rule.st-rejected").count() >= 1)

    # --- Ricerca pareri
    pg.goto(BASE + "#/ricerca")
    pg.wait_for_selector("#pForm", timeout=5000)
    pg.click('[data-tab-b="paste"]')
    pg.fill("#pText", "Chloe Kelly è fortissima: scatto e finalizzazione da vertice. Da prendere assolutamente, voto 90.")
    pg.fill("#pCr", "Creator Prova")
    pg.fill("#pUrl", "ftp://sbagliato")
    pg.click("#pGo")
    ok("ricerca: link non valido segnalato in italiano", "http" in pg.inner_text("#pErr"))
    pg.fill("#pUrl", f"https://example.com/prova-ui-{int(time.time())}")
    pg.click("#pGo")
    pg.wait_for_selector("#pOut .pr", timeout=8000)
    ok("ricerca: proposta con carta, scelta, confidenza e criteri", pg.locator("#pOut .pr .badge").count() >= 1 and pg.locator("#pOut .conf").count() >= 1)
    pg.locator("#pOut [data-edit-toggle]").first.click()
    pg.click('#pOut [data-st="yes"]')
    pg.fill("#pOut input[type=number]", "40")
    pg.locator("#pOut [data-acc]").first.click()
    pg.wait_for_selector("#pOut .form-err:not([hidden])", timeout=5000)
    ok("ricerca: correzione incoerente rifiutata con messaggio", "70" in pg.inner_text("#pOut .form-err"))
    pg.fill("#pOut input[type=number]", "88")
    pg.locator("#pOut [data-acc]").first.click()
    pg.wait_for_selector("#pOut .pr-done", timeout=5000)
    ok("ricerca: accettata dopo la correzione", "Accettata" in pg.inner_text("#pOut .pr-done"), pg.inner_text("#pOut").replace("\n", " ")[:600])
    pg.click('[data-tab-b="queue"]')
    pg.wait_for_timeout(500)
    ok("ricerca: coda con proposte in attesa o stato vuoto", pg.locator("#qBox .pr, #qBox .empty").count() >= 1)
    if pg.locator("#qBox [data-rej]").count():
        n1 = pg.locator("#qBox .pr").count()
        pg.locator("#qBox [data-rej]").first.click()
        pg.wait_for_timeout(800)
        ok("ricerca: rifiuto dalla coda", pg.locator("#qBox .pr").count() == n1 - 1)
    pg.click('[data-tab-b="sources"]')
    pg.wait_for_selector(".srcs li", timeout=5000)
    ok("ricerca: fonti (TikTok/Instagram solo a mano)", pg.locator(".srcs li").count() == 5 and "Solo a mano" in pg.inner_text(".srcs"))

    # --- Automatico (senza rete: stato, soglie, annulla; l'esecuzione e' simulata con route)
    pg.goto(BASE + "#/automatico")
    pg.wait_for_selector(".au-top", timeout=5000)
    ok("automatico: interruttore, cautele, contatori", pg.locator("#swOn").count() == 1 and "zona grigia" in pg.inner_text(".au-warn").lower() and pg.locator(".kpis.k5 .kpi").count() == 5)
    pg.fill("#cf_max_videos_per_run", "20")
    pg.click("#cfgSave")
    pg.wait_for_selector(".toast", timeout=5000)
    pg.reload()
    pg.wait_for_selector("#cf_max_videos_per_run", timeout=5000)
    ok("automatico: soglia salvata", pg.input_value("#cf_max_videos_per_run") == "20")
    pg.fill("#cf_max_videos_per_run", "500")
    pg.click("#cfgSave")
    ok("automatico: valore fuori limite segnalato", not pg.locator("#cfgErr").is_hidden())
    pg.fill("#cf_max_videos_per_run", "25")
    pg.click("#cfgSave")
    pg.wait_for_timeout(600)
    base = json.load(urllib.request.urlopen(BASE + "api/v1/auto/status"))
    calls = {"n": 0}

    def fake_status(route):
        calls["n"] += 1
        route.fulfill(json=dict(base, running=calls["n"] < 3))
    pg.route("**/api/v1/auto/run", lambda r: r.fulfill(status=202, json={"started": True}))
    pg.route("**/api/v1/auto/status", fake_status)
    pg.click("#bRun")
    pg.wait_for_selector(".au-prog", timeout=5000)
    ok("automatico: avanzamento visibile durante l'esecuzione", True)
    pg.wait_for_selector("#bRun", timeout=20000)
    ok("automatico: l'avanzamento finisce da solo", pg.locator(".au-prog").count() == 0)
    pg.unroute("**/api/v1/auto/status")
    pg.unroute("**/api/v1/auto/run")
    pg.route("**/api/v1/auto/run", lambda r: r.fulfill(status=409, json={"detail": "una raccolta automatica e gia in corso"}))
    pg.route("**/api/v1/auto/status", lambda r: r.fulfill(json=dict(base, running=False)))
    pg.click("#bRun")
    pg.wait_for_selector(".toast", timeout=5000)
    ok("automatico: 409 gestito senza errori", True)
    pg.unroute("**/api/v1/auto/run")
    pg.unroute("**/api/v1/auto/status")
    pg.reload()
    pg.wait_for_selector(".au-top", timeout=5000)
    if pg.locator("#bUndo:not([disabled])").count():
        pg.click("#bUndo")
        ok("automatico: annulla chiede conferma", pg.locator("dialog[open] .dlg-t").count() == 1)
        pg.click(".dlg-act .btn.danger")
        pg.wait_for_timeout(1200)
        ok("automatico: pareri automatici annullati", pg.locator(".aops li").count() == 0)

    # --- Importa pareri
    pg.goto(BASE + "#/importa")
    pg.click("#tOpinions")
    ok("import pareri: esempio copiabile", pg.locator("#opExT").inner_text().startswith("carta;creator;scelta"))
    pg.click("#bExUse")
    pg.click("#bPrev")
    pg.wait_for_selector("#impRes:not([hidden])", timeout=5000)
    ok("import pareri: anteprima con conteggi", pg.locator("#impRes .rpill").count() >= 1)
    pg.fill("#impText", "carta;creator;scelta;voto;motivo;link\nCarta Che Non Esiste;Exeed;sì;80;boh;")
    pg.click("#bPrev")
    pg.wait_for_selector("#impRes .rlist .err", timeout=5000)
    ok("import pareri: errore per riga in italiano", "Riga 2" in pg.inner_text("#impRes .rlist"))

    # --- Dettaglio: come pesano, errore voto incoerente
    pg.goto(BASE + "#/carte")
    pg.wait_for_selector(".cw-open", timeout=8000)
    pg.evaluate("document.querySelector('[data-layout=table]').click()")
    pg.locator(".tbl tbody tr .tname", has_text="Chloe Kelly").first.click()
    pg.wait_for_selector(".weigh", timeout=5000)
    ok("dettaglio: 'come pesano i pareri' con percentuale", "pesano" in pg.inner_text(".weigh-t") and pg.locator(".wl li").count() >= 1)
    ok("dettaglio: formula con quota pareri dinamica", "pareri" in pg.inner_text(".formula"))
    pg.fill("#oC", "Creator Prova")
    pg.click('[data-stance="yes"]')
    pg.fill("#oV", "40")
    pg.click("#bOp")
    ok("dettaglio: voto incoerente spiegato in italiano", "70" in pg.inner_text("#opErr") and not pg.locator("#opErr").is_hidden())
    pg.keyboard.press("Escape")
    pg.evaluate("document.querySelector('[data-layout=grid]').click()")

    # --- Calibra: soglie null e ritocchi alle regole (risposta simulata)
    def cal(route):
        d = route.fetch().json()
        if d.get("ready"):
            d["suggested"]["thresholds"] = None
            d["suggested"]["thresholds_message"] = "Messaggio di prova: soglie non proposte."
            d["rules"] = {"min_cards_rule": 5, "changes": [{"id": "scatto-vertice", "n_hit": 6, "n_other": 7, "diff": 6.2, "delta": {"old": 0, "new": 0.25}, "threshold": {"field": "gte", "old": 88, "new": 86}}]}
        route.fulfill(json=d)
    pg.route("**/api/v1/calibration", cal)
    pg.goto(BASE + "#/calibra")
    pg.wait_for_selector(".cal", timeout=5000)
    pg.wait_for_timeout(700)
    if pg.locator("#cApply").count() == 1:
        ok("calibra: soglie assenti gestite col messaggio", "Messaggio di prova" in pg.inner_text(".cal") and pg.locator("#cT[disabled]").count() == 1)
        ok("calibra: ritocchi alle regole mostrati", pg.locator(".cal-rules li").count() == 1 and pg.locator("#cR:not([disabled])").count() == 1)
    pg.unroute("**/api/v1/calibration")


def main():
    with sync_playwright() as p:
        kw = {"executable_path": exe()} if exe() else {}
        b = p.chromium.launch(args=["--no-sandbox"], **kw)
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark")
        pg = ctx.new_page()
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "status of 4" not in m.text else None)
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
        for sec, sel in (("importa", "#impText"), ("calibra", ".cal"), ("regole", ".rules"), ("ricerca", ".rs"), ("automatico", ".au")):
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

        motore(pg)

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
            for sec in ("carte", "confronta", "importa", "calibra", "regole", "ricerca", "automatico"):
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
