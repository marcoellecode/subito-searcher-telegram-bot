'''Interfaccia web per la rete locale: stato dei controlli, aggiunta, modifica ed eliminazione delle ricerche.

Gira come thread dentro lo stesso processo di app.py e lavora sullo stesso dizionario `queries`
(protetto da `dataLock`), quindi bot Telegram e interfaccia web non si sovrascrivono mai il file.

Avvio da app.py:
    import webui
    webui.start(sys.modules[__name__])

Variabili d'ambiente:
    WEBUI_PORT       porta HTTP (default 8080)
    WEBUI_PASSWORD   se impostata, chiede questa password (utente qualsiasi)
    WEBUI_ENABLED    "false" per disattivare l'interfaccia
'''

import base64
import hmac
import ipaddress
import json
import os
import re
import threading
from datetime import datetime
from html import unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote_plus

app = None  # modulo di app.py, impostato da start()


#-------------------------------------
# helpers
def is_local_address(address):
    '''Accetta solo indirizzi di rete locale (privati, loopback, link-local)'''
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local

def strip_html(text):
    '''I messaggi di app.py sono in HTML per Telegram'''
    return unescape(re.sub(r"<[^>]+>", "", text))

def fmt_time(value):
    return value.strftime("%d/%m %H:%M") if value else None

def resolve_query(text):
    '''Link di subito.it oppure parole chiave -> (url, nome suggerito)'''
    text = text.strip()
    if app.is_subito_url(text):
        return text, app.name_from_url(text)
    if text.startswith("http"):
        raise ValueError("Il link deve essere di subito.it.")
    if not text:
        raise ValueError("Scrivi cosa cercare o incolla un link di subito.it.")
    return app.searchBaseUrl + quote_plus(text), text

def parse_prices(data):
    minPrice = app.parse_price(str(data.get("min") or ""))
    maxPrice = app.parse_price(str(data.get("max") or ""))
    if minPrice is None or maxPrice is None:
        raise ValueError("I prezzi devono essere numeri interi.")
    if minPrice != "null" and maxPrice != "null" and int(minPrice) > int(maxPrice):
        raise ValueError("Il prezzo minimo è più alto del massimo.")
    return minPrice, maxPrice


#-------------------------------------
# operations
def get_status():
    with app.dataLock:
        searches = list(app.iter_searches())
    now = datetime.now()
    pausedUntil = app.pausedUntil if app.pausedUntil and app.pausedUntil > now else None
    if app.pollingPaused:
        state = "paused"
    elif pausedUntil:
        state = "antibot"
    elif app.pollRunning:
        state = "running"
    else:
        state = "active"
    return {
        "state": state,
        "searches": len(searches),
        "items": sum(len(s[4]) for s in searches),
        "lastPoll": fmt_time(app.lastPoll),
        "lastPollNew": app.lastPollNew,
        "nextPoll": fmt_time(app.nextPoll) if state == "active" else None,
        "pausedUntil": fmt_time(pausedUntil),
        "pollMinutes": app.pollDelay // 60,
        "queryDelay": app.queryDelay,
    }

def get_searches():
    with app.dataLock:
        return [{"name": name, "url": url,
                 "min": None if minP == "null" else int(minP),
                 "max": None if maxP == "null" else int(maxP),
                 "range": app.format_range(minP, maxP),
                 "count": len(items),
                 "pending": name in app.seedPending}
                for name, url, minP, maxP, items in app.iter_searches()]

def get_items(name):
    with app.dataLock:
        found = [s for s in app.iter_searches() if s[0] == name]
        if not found:
            raise LookupError("Ricerca non trovata.")
        items = list(found[0][4].items())
    # salvati in ordine di scoperta: i piu' recenti in fondo
    return [{"link": link, "title": item.get("title"), "price": item.get("price"),
             "location": item.get("location")}
            for link, item in reversed(items)]

def add_search(data):
    url, suggested = resolve_query(str(data.get("query") or ""))
    minPrice, maxPrice = parse_prices(data)
    name = str(data.get("name") or "").strip() or suggested
    with app.dataLock:
        if name in app.queries:
            raise ValueError("Esiste già una ricerca chiamata \"" + name + "\".")
    return strip_html(app.add(url, name, minPrice, maxPrice))

def update_search(data):
    oldName = str(data.get("oldName") or "")
    url, suggested = resolve_query(str(data.get("query") or ""))
    minPrice, maxPrice = parse_prices(data)
    name = str(data.get("name") or "").strip() or suggested

    with app.dataLock:
        found = [s for s in app.iter_searches() if s[0] == oldName]
        if not found:
            raise LookupError("Ricerca non trovata.")
        if name != oldName and name in app.queries:
            raise ValueError("Esiste già una ricerca chiamata \"" + name + "\".")
        _, oldUrl, _, _, items = found[0]
        pending = oldName in app.seedPending

        app.queries.pop(oldName)
        app.seedPending.discard(oldName)

        if url != oldUrl:
            # ricerca diversa: rilegge gli annunci attuali senza notificarli
            return strip_html(app.add(url, name, minPrice, maxPrice))

        # stesso link: tiene gli annunci gia' visti, cosi' non arrivano di nuovo
        app.queries[name] = {url: {minPrice: {maxPrice: items}}}
        if pending:
            app.seedPending.add(name)
        app.save_queries()
    return "Ricerca \"" + name + "\" aggiornata (" + app.format_range(minPrice, maxPrice) + ")."

def delete_search(data):
    name = str(data.get("name") or "")
    with app.dataLock:
        if name not in app.queries:
            raise LookupError("Ricerca non trovata.")
        return strip_html(app.delete(name))

def refresh():
    if app.pollingPaused:
        raise ValueError("I controlli sono sospesi: riprendili prima.")
    if app.pausedUntil and app.pausedUntil > datetime.now():
        raise ValueError("Pausa anti-bot in corso: usa Riprendi per forzare.")
    if app.pollRunning:
        raise ValueError("C'è già un controllo in corso.")
    app.manualRefresh = True
    app.pollNow.set()
    return "Controllo avviato: i nuovi annunci arriveranno su Telegram."

def pause():
    app.pollingPaused = True
    return "Controlli sospesi."

def resume():
    app.pollingPaused = False
    app.pausedUntil = None
    app.pollNow.set()
    return "Controlli ripresi."


#-------------------------------------
# HTTP server
class Handler(BaseHTTPRequestHandler):
    server_version = "SubitoSearcherWeb"

    def log_message(self, format, *args):
        pass  # niente log per ogni richiesta

    def check_access(self):
        if not is_local_address(self.client_address[0]):
            print("[WEBUI] refused connection from " + self.client_address[0])
            self.send_json({"error": "Accesso consentito solo dalla rete locale."}, 403)
            return False
        password = os.environ.get("WEBUI_PASSWORD", "")
        if password:
            ok = False
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Basic "):
                try:
                    _, _, given = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
                    ok = hmac.compare_digest(given.encode(), password.encode())
                except Exception:
                    ok = False
            if not ok:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="Subito Searcher"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
        return True

    def send_body(self, body, contentType, status=200):
        self.send_response(status)
        self.send_header("Content-Type", contentType)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, status=200):
        self.send_body(json.dumps(data).encode("utf-8"), "application/json; charset=utf-8", status)

    def split_path(self):
        '''Percorso normalizzato e query: "//" o "//api/x" (es. link di CasaOS) diventano "/" e "/api/x"'''
        rawPath, _, query = self.path.partition("?")
        return "/" + rawPath.lstrip("/"), query

    def do_GET(self):
        if not self.check_access():
            return
        path, query = self.split_path()
        try:
            if path == "/api/status":
                self.send_json(get_status())
            elif path == "/api/searches":
                self.send_json(get_searches())
            elif path == "/api/items":
                name = parse_qs(query).get("name", [""])[0]
                self.send_json(get_items(name))
            elif path.startswith("/api/"):
                print("[WEBUI] unknown API path " + self.path)
                self.send_json({"error": "Non trovato."}, 404)
            else:
                # qualsiasi altro percorso mostra la pagina
                self.send_body(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        except LookupError as e:
            self.send_json({"error": str(e)}, 404)
        except Exception as e:
            print("[WEBUI] error on GET " + path + ": " + str(e))
            self.send_json({"error": "Errore interno: " + str(e)}, 500)

    def do_POST(self):
        if not self.check_access():
            return
        # solo JSON: un form di un altro sito non puo' inviarlo senza preflight CORS
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self.send_json({"error": "Content-Type non valido."}, 415)
            return
        actions = {
            "/api/searches/add": add_search,
            "/api/searches/update": update_search,
            "/api/searches/delete": delete_search,
            "/api/refresh": lambda data: refresh(),
            "/api/pause": lambda data: pause(),
            "/api/resume": lambda data: resume(),
        }
        action = actions.get(self.split_path()[0])
        if action is None:
            print("[WEBUI] unknown API path " + self.path)
            self.send_json({"error": "Non trovato."}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length) or b"{}") if length else {}
            self.send_json({"message": action(data)})
        except (ValueError, LookupError) as e:
            self.send_json({"error": str(e)}, 400)
        except Exception as e:
            print("[WEBUI] error on POST " + self.path + ": " + str(e))
            self.send_json({"error": "Errore interno: " + str(e)}, 500)


def start(appModule):
    '''Avvia il server web in un thread daemon'''
    global app
    app = appModule
    if os.environ.get("WEBUI_ENABLED", "true").strip().lower() in ("false", "0", "no"):
        print("Web UI disabled.")
        return
    port = int(os.environ.get("WEBUI_PORT", "8080"))
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    except OSError as e:
        print("Web UI not started on port " + str(port) + ": " + str(e))
        return
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("Web UI running on port " + str(port)
          + (" (password protected)" if os.environ.get("WEBUI_PASSWORD") else ""))


#-------------------------------------
# page
PAGE = r'''<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Subito Searcher</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🔎</text></svg>">
<style>
:root {
  --bg: #f5f6f8; --surface: #ffffff; --text: #1b1f24; --muted: #5f6b7a; --border: #dde2e8;
  --accent: #e8442e; --accent-text: #ffffff; --ok: #1f8a4c; --warn: #b26a00; --danger: #c62828;
  --ok-bg: #e5f4ea; --warn-bg: #fff2dc; --danger-bg: #fde8e8; --info-bg: #e8effc; --info: #2456b3;
  --radius: 10px; --shadow: 0 1px 2px rgba(0,0,0,.06), 0 2px 8px rgba(0,0,0,.04);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111418; --surface: #1a1f25; --text: #e7eaee; --muted: #9aa5b1; --border: #2c333b;
    --accent: #ff5a43; --ok: #5cc98a; --warn: #f0ad4e; --danger: #ff6b6b;
    --ok-bg: #173324; --warn-bg: #3a2c12; --danger-bg: #3d1a1a; --info-bg: #1b2a44; --info: #8fb3ff;
    --shadow: none;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 1040px; margin: 0 auto; padding: 20px 16px 48px; }
header { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; justify-content: space-between; margin-bottom: 18px; }
h1 { font-size: 22px; margin: 0; display: flex; align-items: center; gap: 10px; }
h2 { font-size: 16px; margin: 0 0 12px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); padding: 16px; margin-bottom: 16px; }
.pill { font-size: 13px; font-weight: 600; padding: 3px 10px; border-radius: 999px; white-space: nowrap; }
.pill.active { background: var(--ok-bg); color: var(--ok); }
.pill.running { background: var(--info-bg); color: var(--info); }
.pill.paused, .pill.antibot { background: var(--warn-bg); color: var(--warn); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px; }
.stat .label { color: var(--muted); font-size: 13px; }
.stat .value { font-size: 20px; font-weight: 650; }
.stat .sub { color: var(--muted); font-size: 13px; }
.actions { display: flex; gap: 8px; flex-wrap: wrap; }
button { font: inherit; border: 1px solid var(--border); background: var(--surface); color: var(--text);
  padding: 7px 14px; border-radius: 8px; cursor: pointer; }
button:hover { border-color: var(--muted); }
button.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-text); font-weight: 600; }
button.danger { color: var(--danger); }
button.small { padding: 4px 10px; font-size: 13px; }
button:disabled { opacity: .55; cursor: default; }
form.grid { display: grid; grid-template-columns: 2fr 1fr 1fr 1.4fr auto; gap: 10px; align-items: end; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--muted); }
input { font: inherit; color: var(--text); background: var(--bg); border: 1px solid var(--border);
  border-radius: 8px; padding: 7px 10px; width: 100%; min-width: 0; }
input:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
.hint { color: var(--muted); font-size: 13px; margin: 10px 0 0; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--border); vertical-align: middle; }
th { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); font-weight: 600; }
td.num { font-variant-numeric: tabular-nums; }
tr:last-child td { border-bottom: none; }
a { color: var(--info); }
.name { font-weight: 600; }
.tag { font-size: 12px; color: var(--warn); margin-left: 6px; }
.empty { color: var(--muted); text-align: center; padding: 28px 8px; }
dialog { border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); color: var(--text);
  padding: 0; width: min(720px, calc(100vw - 32px)); max-height: calc(100vh - 48px); }
dialog::backdrop { background: rgba(0,0,0,.45); }
.dlg-head { display: flex; justify-content: space-between; align-items: center; padding: 14px 16px; border-bottom: 1px solid var(--border); }
.dlg-body { padding: 16px; overflow: auto; max-height: calc(100vh - 160px); }
.dlg-body form { display: grid; gap: 12px; }
.row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.dlg-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
#toast { position: fixed; left: 50%; bottom: 20px; transform: translateX(-50%); max-width: calc(100vw - 32px);
  padding: 10px 16px; border-radius: 8px; font-weight: 500; box-shadow: 0 4px 16px rgba(0,0,0,.2); display: none; z-index: 10; white-space: pre-line; }
#toast.ok { display: block; background: var(--ok-bg); color: var(--ok); }
#toast.err { display: block; background: var(--danger-bg); color: var(--danger); }
@media (max-width: 760px) {
  form.grid { grid-template-columns: 1fr 1fr; }
  form.grid .wide { grid-column: 1 / -1; }
  thead { display: none; }
  table, tbody, tr, td { display: block; width: 100%; }
  tr { border-bottom: 1px solid var(--border); padding: 8px 0; }
  tr:last-child { border-bottom: none; }
  td { border: none; padding: 3px 0; }
  td[data-label]::before { content: attr(data-label) ": "; color: var(--muted); font-size: 13px; }
}
</style>
</head>
<body>
<main>
  <header>
    <h1>🔎 Subito Searcher <span id="state" class="pill">…</span></h1>
    <div class="actions">
      <button id="btnRefresh" class="primary">Controlla ora</button>
      <button id="btnPause">Pausa</button>
    </div>
  </header>

  <section class="card stats">
    <div class="stat"><div class="label">Ricerche</div><div class="value" id="stSearches">–</div></div>
    <div class="stat"><div class="label">Annunci memorizzati</div><div class="value" id="stItems">–</div></div>
    <div class="stat"><div class="label">Ultimo controllo</div><div class="value" id="stLast">–</div><div class="sub" id="stLastNew"></div></div>
    <div class="stat"><div class="label" id="stNextLabel">Prossimo controllo</div><div class="value" id="stNext">–</div><div class="sub" id="stEvery"></div></div>
  </section>

  <section class="card">
    <h2>Nuova ricerca</h2>
    <form id="addForm" class="grid">
      <label class="wide">Cosa cercare
        <input name="query" required placeholder="es. dell wyse 5070, oppure un link di subito.it"></label>
      <label>Prezzo min €<input name="min" inputmode="numeric" pattern="[0-9]*" placeholder="nessuno"></label>
      <label>Prezzo max €<input name="max" inputmode="numeric" pattern="[0-9]*" placeholder="nessuno"></label>
      <label class="wide">Nome<input name="name" placeholder="automatico"></label>
      <button class="primary wide" type="submit">Aggiungi</button>
    </form>
    <p class="hint">Con le parole chiave si cerca in tutta Italia. Per categoria, zona o altri filtri fai la ricerca su subito.it e incolla qui il link.
      Gli annunci già online vengono memorizzati senza notifiche: riceverai solo i nuovi.</p>
  </section>

  <section class="card">
    <h2>Le tue ricerche</h2>
    <table>
      <thead><tr><th>Nome</th><th>Prezzo</th><th>Annunci</th><th>Link</th><th></th></tr></thead>
      <tbody id="searches"><tr><td colspan="5" class="empty">Caricamento…</td></tr></tbody>
    </table>
  </section>
</main>

<dialog id="editDlg">
  <div class="dlg-head"><strong>Modifica ricerca</strong><button class="small" data-close>Chiudi</button></div>
  <div class="dlg-body">
    <form id="editForm">
      <input type="hidden" name="oldName">
      <label>Nome<input name="name" required></label>
      <label>Link o parole chiave<input name="query" required></label>
      <div class="row2">
        <label>Prezzo min €<input name="min" inputmode="numeric" pattern="[0-9]*" placeholder="nessuno"></label>
        <label>Prezzo max €<input name="max" inputmode="numeric" pattern="[0-9]*" placeholder="nessuno"></label>
      </div>
      <p class="hint">Se cambi il link, gli annunci vengono riletti da capo senza notifiche. Se cambi solo nome o prezzi, quelli già visti restano.</p>
      <div class="dlg-foot"><button type="button" data-close>Annulla</button><button class="primary" type="submit">Salva</button></div>
    </form>
  </div>
</dialog>

<dialog id="itemsDlg">
  <div class="dlg-head"><strong id="itemsTitle">Annunci</strong><button class="small" data-close>Chiudi</button></div>
  <div class="dlg-body">
    <table>
      <thead><tr><th>Annuncio</th><th>Prezzo</th><th>Località</th></tr></thead>
      <tbody id="items"></tbody>
    </table>
  </div>
</dialog>

<div id="toast"></div>

<script>
const $ = (id) => document.getElementById(id);
const stateLabels = { active: "Attivo", running: "Controllo in corso", paused: "In pausa", antibot: "Pausa anti-bot" };
let status = null;
let searches = [];

async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || ("Errore " + res.status));
  return data;
}

let toastTimer;
function toast(text, ok = true) {
  const el = $("toast");
  el.textContent = text;
  el.className = ok ? "ok" : "err";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.className = "", ok ? 4000 : 7000);
}

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  for (const c of [].concat(children)) node.append(c);
  return node;
}

function price(v) { return typeof v === "number" ? v.toLocaleString("it-IT") + " €" : "n.d."; }

async function loadStatus() {
  status = await api("/api/status");
  const s = status;
  $("state").textContent = stateLabels[s.state];
  $("state").className = "pill " + s.state;
  $("stSearches").textContent = s.searches;
  $("stItems").textContent = s.items.toLocaleString("it-IT");
  $("stLast").textContent = s.lastPoll || "–";
  $("stLastNew").textContent = s.lastPoll && s.lastPollNew !== null ? s.lastPollNew + " nuovi annunci" : "";
  if (s.state === "antibot") {
    $("stNextLabel").textContent = "Ripresa automatica";
    $("stNext").textContent = s.pausedUntil;
  } else {
    $("stNextLabel").textContent = "Prossimo controllo";
    $("stNext").textContent = s.state === "paused" ? "sospeso" : (s.state === "running" ? "in corso" : (s.nextPoll || "–"));
  }
  $("stEvery").textContent = "ogni " + s.pollMinutes + " minuti";
  const paused = s.state === "paused" || s.state === "antibot";
  $("btnPause").textContent = paused ? "Riprendi" : "Pausa";
  $("btnRefresh").disabled = s.state !== "active";
}

async function loadSearches() {
  searches = await api("/api/searches");
  const body = $("searches");
  body.replaceChildren();
  if (!searches.length) {
    body.append(el("tr", {}, el("td", { colSpan: 5, className: "empty", textContent: "Nessuna ricerca salvata. Aggiungine una qui sopra." })));
    return;
  }
  searches.forEach((s, i) => {
    const name = el("td", {}, [el("span", { className: "name", textContent: s.name })]);
    if (s.pending) name.append(el("span", { className: "tag", textContent: "in attesa del primo controllo" }));
    const actions = el("td", {}, el("div", { className: "actions" }, [
      el("button", { className: "small", textContent: "Annunci", onclick: () => showItems(s.name) }),
      el("button", { className: "small", textContent: "Modifica", onclick: () => editSearch(i) }),
      el("button", { className: "small danger", textContent: "Elimina", onclick: () => deleteSearch(s.name) }),
    ]));
    const priceCell = el("td", { textContent: s.range });
    priceCell.dataset.label = "Prezzo";
    const countCell = el("td", { className: "num", textContent: s.count });
    countCell.dataset.label = "Annunci";
    const linkCell = el("td", {}, el("a", { href: s.url, target: "_blank", rel: "noopener", textContent: "apri su subito.it" }));
    body.append(el("tr", {}, [name, priceCell, countCell, linkCell, actions]));
  });
}

async function reload() {
  try { await Promise.all([loadStatus(), loadSearches()]); }
  catch (e) { toast(e.message, false); }
}

function formData(form) { return Object.fromEntries(new FormData(form).entries()); }

async function submitWith(button, fn) {
  button.disabled = true;
  const label = button.textContent;
  button.textContent = "Attendi…";
  try { await fn(); }
  catch (e) { toast(e.message, false); }
  finally { button.disabled = false; button.textContent = label; }
}

$("addForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const form = ev.target;
  submitWith(form.querySelector("button"), async () => {
    const res = await api("/api/searches/add", formData(form));
    form.reset();
    toast(res.message);
    await reload();
  });
});

function editSearch(i) {
  const s = searches[i];
  const f = $("editForm").elements;
  f["oldName"].value = s.name;
  f["name"].value = s.name;
  f["query"].value = s.url;
  f["min"].value = s.min ?? "";
  f["max"].value = s.max ?? "";
  $("editDlg").showModal();
}

$("editForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const form = ev.target;
  submitWith(form.querySelector("button[type=submit]"), async () => {
    const res = await api("/api/searches/update", formData(form));
    $("editDlg").close();
    toast(res.message);
    await reload();
  });
});

async function deleteSearch(name) {
  if (!confirm("Eliminare la ricerca \"" + name + "\" e gli annunci memorizzati?")) return;
  try {
    const res = await api("/api/searches/delete", { name });
    toast(res.message);
    await reload();
  } catch (e) { toast(e.message, false); }
}

async function showItems(name) {
  $("itemsTitle").textContent = name;
  const body = $("items");
  body.replaceChildren(el("tr", {}, el("td", { colSpan: 3, className: "empty", textContent: "Caricamento…" })));
  $("itemsDlg").showModal();
  try {
    const items = await api("/api/items?name=" + encodeURIComponent(name));
    body.replaceChildren();
    if (!items.length) {
      body.append(el("tr", {}, el("td", { colSpan: 3, className: "empty", textContent: "Nessun annuncio memorizzato." })));
      return;
    }
    for (const it of items) {
      const p = el("td", { className: "num", textContent: price(it.price) }); p.dataset.label = "Prezzo";
      const l = el("td", { textContent: it.location || "" }); l.dataset.label = "Località";
      body.append(el("tr", {}, [
        el("td", {}, el("a", { href: it.link, target: "_blank", rel: "noopener", textContent: it.title || it.link })), p, l]));
    }
  } catch (e) { body.replaceChildren(); toast(e.message, false); }
}

$("btnRefresh").addEventListener("click", (ev) => submitWith(ev.target, async () => {
  toast((await api("/api/refresh", {})).message);
  await reload();
}));

$("btnPause").addEventListener("click", (ev) => submitWith(ev.target, async () => {
  const paused = status && (status.state === "paused" || status.state === "antibot");
  toast((await api(paused ? "/api/resume" : "/api/pause", {})).message);
  await reload();
}));

document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));

reload();
setInterval(() => { if (!document.hidden && !document.querySelector("dialog[open]")) reload(); }, 10000);
</script>
</body>
</html>
'''
