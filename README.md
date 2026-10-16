# Subito Searcher Telegram Bot

Bot Telegram che controlla periodicamente le tue ricerche su [subito.it](https://www.subito.it) e ti scrive appena compare un annuncio nuovo.
Le ricerche si aggiungono, consultano ed eliminano direttamente dalla chat.

- Ricerche per **parole chiave** o per **link** di subito.it (con categoria, zona e filtri del sito)
- Filtro per **prezzo minimo e massimo**
- **Notifiche** con titolo, prezzo, località e link all'annuncio
- Procedura guidata, pulsanti e menu dei comandi su Telegram
- Rimozione automatica degli annunci venduti
- Protezione dal blocco **anti-bot** di subito.it (DataDome)
- Immagine Docker pronta per **CasaOS**, amd64 e arm64

---

## Indice

1. [Prima di iniziare](#prima-di-iniziare)
2. [Installazione](#installazione)
   - [CasaOS](#casaos)
   - [Docker Compose](#docker-compose)
   - [Senza Docker](#senza-docker)
3. [Configurazione](#configurazione)
4. [Uso del bot](#uso-del-bot)
5. [Come funziona](#come-funziona)
6. [Protezione anti-bot](#protezione-anti-bot)
7. [Aggiornamento](#aggiornamento)
8. [Backup e migrazione](#backup-e-migrazione)
9. [Risoluzione dei problemi](#risoluzione-dei-problemi)
10. [Sviluppo](#sviluppo)

---

## Prima di iniziare

Servono due dati di Telegram.

### Token del bot

1. Apri [@BotFather](https://t.me/BotFather) su Telegram e invia `/newbot`.
2. Scegli nome e username del bot.
3. BotFather risponde con il **token**, simile a `123456789:AAH...`. Tienilo privato: chi lo possiede controlla il bot.

### Chat id

È l'identificativo della chat in cui il bot manda gli annunci. **Il bot risponde solo a questa chat** e ignora tutte le altre.

- **Chat privata**: scrivi a [@userinfobot](https://t.me/userinfobot), ti risponde con il tuo id (un numero come `123456789`).
- **Gruppo**: aggiungi il bot al gruppo, scrivi un messaggio nel gruppo e apri
  `https://api.telegram.org/bot<TOKEN>/getUpdates`: l'id è il valore `chat.id`, negativo (es. `-1001234567890`).

Prima del primo avvio apri la chat con il tuo bot e premi **Avvia**: Telegram non permette a un bot di scrivere per primo.

---

## Installazione

### CasaOS

L'immagine viene pubblicata su GitHub Container Registry, quindi l'installazione si fa tutta dall'interfaccia web.

1. Nella dashboard di CasaOS apri **App Store** → **+** (in alto a destra) → **Install a customized app**.
2. Clicca **Import** (in alto a destra) e incolla il contenuto di [`docker-compose.casaos.yml`](docker-compose.casaos.yml).
3. Nella sezione delle variabili d'ambiente compila:
   - `TELEGRAM_TOKEN`: il token di BotFather
   - `TELEGRAM_CHAT_ID`: il chat id
4. Premi **Install**.

Il bot parte, crea la cartella dati `/DATA/AppData/subito-searcher/bindata/` e ti risponde su Telegram: invia `/help` per verificarlo.
I log sono nel menu dell'app (⋮ → **Logs**).

> Hai già un `searches.tracked` da una vecchia installazione? Copialo in `/DATA/AppData/subito-searcher/bindata/` (con l'app **Files** di CasaOS o via SCP) e riavvia l'app.

### Docker Compose

```bash
git clone https://github.com/marcoellecode/subito-searcher-telegram-bot.git
cd subito-searcher-telegram-bot
mkdir bindata
```

Imposta le credenziali in un file `.env` accanto a `docker-compose.yml`:

```env
TELEGRAM_TOKEN=123456789:AAH...
TELEGRAM_CHAT_ID=123456789
```

oppure crea a mano `bindata/telegram_api_credentials` (formato nella [configurazione](#file-delle-credenziali)). Poi:

```bash
docker compose up -d            # scarica e avvia l'immagine pubblicata
docker compose logs -f          # segue i log
```

Per costruire l'immagine in locale invece di scaricarla: `docker compose up -d --build`.

### Senza Docker

Serve Python 3.11 o successivo.

```bash
git clone https://github.com/marcoellecode/subito-searcher-telegram-bot.git
cd subito-searcher-telegram-bot
python -m venv venv
source venv/bin/activate        # su Windows: venv\Scripts\activate
pip install -r requirements.txt
export TELEGRAM_TOKEN=123456789:AAH...    # oppure crea il file telegram_api_credentials
export TELEGRAM_CHAT_ID=123456789
python app.py
```

I file di dati vengono letti e scritti nella **cartella da cui lanci lo script**.

---

## Configurazione

### Variabili d'ambiente

| Variabile | Obbligatoria | Descrizione |
|---|---|---|
| `TELEGRAM_TOKEN` | sì¹ | Token del bot fornito da BotFather |
| `TELEGRAM_CHAT_ID` | sì¹ | Chat che riceve gli annunci e può usare i comandi |
| `TZ` | no | Fuso orario per orari e log (nei compose: `Europe/Rome`) |

¹ Obbligatorie se non esiste già il file `telegram_api_credentials`.

Se impostate, le due variabili Telegram **hanno la precedenza** sul file e a ogni avvio lo creano o lo aggiornano. Per cambiare bot o chat basta quindi modificarle (in CasaOS: impostazioni dell'app) e riavviare.
Se mancano sia le variabili sia il file, lo script lo segnala nei log e si chiude.

### File delle credenziali

`telegram_api_credentials`, in formato JSON:

```json
{
    "token": "123456789:AAH...",
    "chatid": "123456789"
}
```

### File delle ricerche

`searches.tracked` contiene le ricerche salvate e gli annunci già visti. Viene creato vuoto al primo avvio e aggiornato dal bot: non serve modificarlo a mano.

```json
{
    "nome ricerca": {
        "https://www.subito.it/annunci-italia/vendita/usato/?q=...": {
            "<prezzo min o null>": {
                "<prezzo max o null>": {
                    "<link annuncio>": {"title": "...", "price": 90, "location": "..."}
                }
            }
        }
    }
}
```

### Tempi

Si cambiano in cima ad [`app.py`](app.py) (poi va ricostruita l'immagine):

| Costante | Default | Significato |
|---|---|---|
| `pollDelay` | `600` | secondi tra un giro di controlli e il successivo |
| `queryDelay` | `60` | secondi di attesa tra una ricerca e l'altra nello stesso giro |
| `botDetectionSleep` | `86400` | durata della pausa dopo un blocco anti-bot (24 ore) |

Con tante ricerche un giro dura `(ricerche − 1) × queryDelay`: con 6 ricerche circa 5 minuti, più i 10 minuti di attesa.
Abbassare questi valori aumenta il rischio di blocco da parte di subito.it.

---

## Uso del bot

Premendo `/` nella chat compare il menu con tutti i comandi.

### Comandi

| Comando | Cosa fa |
|---|---|
| `/add` | aggiunge una ricerca con la procedura guidata |
| `/add <link> [nome] [min] [max]` | aggiunge una ricerca in una sola riga |
| `/list` | elenca le ricerche, con i pulsanti 🔍 (annunci) e 🗑 (elimina) |
| `/items <nome>` | mostra gli ultimi 20 annunci memorizzati di una ricerca |
| `/delete` | mostra le ricerche da eliminare, con conferma |
| `/delete <nome>` | elimina subito la ricerca indicata (maiuscole e minuscole indifferenti) |
| `/refresh` | controlla subito tutte le ricerche e manda un riepilogo |
| `/status` | numero di ricerche, ultimo e prossimo controllo, pause attive |
| `/pause` | sospende i controlli automatici |
| `/resume` | riprende i controlli, anche durante una pausa anti-bot |
| `/cancel` | annulla la procedura guidata in corso |
| `/help` | guida rapida |

`/shortlist` funziona ancora come sinonimo di `/list`.

### Aggiungere una ricerca con la procedura guidata

1. Invia `/add`.
2. Scrivi **cosa cercare**:
   - parole chiave, es. `dell wyse 5070`: il bot cerca in tutta Italia, negli annunci di vendita;
   - oppure il **link** di una ricerca fatta su subito.it, se vuoi limitarla a una categoria, una regione o usare altri filtri del sito.
3. **Prezzo minimo**: scrivi un numero o premi **Salta**.
4. **Prezzo massimo**: scrivi un numero o premi **Salta**.
5. **Nome**: premi il suggerimento o scrivine uno. Se esiste già una ricerca con quel nome viene sostituita.

Il bot legge gli annunci già online e li memorizza **senza notificarli**: da quel momento ti scrive solo per quelli nuovi.

> Scorciatoia: **incolla un link di subito.it** nella chat e la procedura parte direttamente dal prezzo minimo.

### Aggiungere una ricerca in una riga

```
/add <link> [nome] [prezzo_min] [prezzo_max]
```

- Il nome può essere di più parole; se manca, viene preso dalle parole cercate nel link.
- Per saltare un prezzo usa `-`.

Esempi:

```
/add https://www.subito.it/annunci-italia/vendita/usato/?q=dell+wyse+5070
/add https://www.subito.it/annunci-italia/vendita/usato/?q=dell+wyse+5070 wyse 50 150
/add https://www.subito.it/annunci-italia/vendita/usato/?q=iphone+13 iphone 13 - 400
```

### Le notifiche

Ogni annuncio nuovo arriva in un messaggio separato, con l'anteprima della pagina:

```
🔔 wyse
Dell Wyse 5070 thin client
💶 90 €
📍 Pozzallo
https://www.subito.it/informatica/dell-wyse-5070-....htm
```

Se un giro trova più annunci, arrivano in ordine cronologico, circa uno al secondo.
Il bot ti avvisa anche quando subito.it attiva la protezione anti-bot e quando un `/refresh` è terminato.

---

## Come funziona

- Un thread in background esegue un **giro di controlli** ogni `pollDelay` secondi. Per ogni ricerca scarica la prima pagina dei risultati (circa 30 annunci, i più recenti) e legge i dati che subito.it inserisce nella pagina (`__NEXT_DATA__`).
- Un annuncio è **nuovo** se il suo link non è ancora in `searches.tracked`: viene salvato e notificato.
- Gli annunci segnati come **venduti** vengono tolti dall'elenco.
- I filtri di prezzo si applicano agli annunci con prezzo; quelli senza prezzo passano sempre.
- Al primo controllo di una ricerca gli annunci presenti vengono solo memorizzati, così la chat non viene inondata.

Visto che viene letta solo la prima pagina, conviene fare **ricerche mirate**: con una ricerca molto generica gli annunci nuovi possono superare i 30 tra un controllo e l'altro.

---

## Protezione anti-bot

subito.it usa DataDome per bloccare i bot. Per ridurre il rischio:

- le pagine vengono scaricate con [`curl_cffi`](https://github.com/lexiforest/curl_cffi), che si presenta con il fingerprint TLS e gli header di Chrome;
- tra una ricerca e l'altra si aspetta `queryDelay` secondi.

Se subito.it risponde comunque con la pagina di verifica (errore 403 o captcha):

1. il giro si interrompe subito, per non peggiorare la reputazione dell'IP;
2. ricevi un messaggio su Telegram con l'orario di ripresa;
3. i controlli restano in pausa per `botDetectionSleep` (24 ore) e poi ripartono da soli.

Puoi forzare la ripresa con `/resume`; se il blocco è ancora attivo, il bot si rimette in pausa da solo.
Se i blocchi si ripetono, riduci il numero di ricerche o aumenta `pollDelay` e `queryDelay`.

---

## Aggiornamento

**CasaOS**: dal menu dell'app (⋮) scegli **Check then update**, oppure reinstallala: i dati in `bindata` restano.

**Docker Compose**:

```bash
docker compose pull
docker compose up -d
```

**Senza Docker**: `git pull`, poi riavvia lo script.

---

## Backup e migrazione

Tutto lo stato è in due file nella cartella dati (`bindata/`, in CasaOS `/DATA/AppData/subito-searcher/bindata/`):

- `searches.tracked`: ricerche e annunci visti;
- `telegram_api_credentials`: credenziali (si ricrea dalle variabili d'ambiente).

Per spostare il bot su un altro server basta copiare `searches.tracked`.

---

## Risoluzione dei problemi

| Problema | Causa e soluzione |
|---|---|
| Il container si riavvia di continuo | Mancano le credenziali: nei log c'è `Missing Telegram credentials`. Imposta `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID`. |
| Il bot non risponde ai comandi | Il chat id è sbagliato: nei log compare `ignored update from chat <id>`. Usa quell'id come `TELEGRAM_CHAT_ID` se è la tua chat. |
| Nessuna notifica | Controlla `/status` (pausa manuale o anti-bot?) e `/list` (filtri di prezzo troppo stretti?). Ricorda che gli annunci già presenti quando hai aggiunto la ricerca non vengono notificati. |
| `Bot detection triggered` nei log | Blocco anti-bot di subito.it: vedi [Protezione anti-bot](#protezione-anti-bot). |
| `JSON path not found` nei log | subito.it ha cambiato la struttura della pagina: lo scraper va aggiornato (`run_query` in `app.py`). |
| CasaOS: `pull access denied` | Il nome dell'immagine è sbagliato o il pacchetto su GHCR non è pubblico. |
| Il container si chiude con `InvalidToken` o `Unauthorized` | Token sbagliato o revocato: controllalo, o generane uno nuovo con BotFather (`/token`). |

Dal terminale del server puoi anche scrivere nella chat del bot:

```bash
docker attach --sig-proxy=false subito-searcher
```

scrivi il testo e premi Invio. **Ctrl+C** ti stacca senza fermare il container (grazie a `--sig-proxy=false`).

---

## Sviluppo

```
app.py                         bot, scraper e ciclo di controllo
requirements.txt               dipendenze Python
Dockerfile                     immagine (python:3.13-slim)
docker-compose.yml             avvio con Docker Compose
docker-compose.casaos.yml      import in CasaOS
.github/workflows/docker-image.yml   build e pubblicazione dell'immagine
```

Il workflow GitHub Actions costruisce l'immagine per `linux/amd64` e `linux/arm64` e la pubblica su
`ghcr.io/marcoellecode/subito-searcher-telegram-bot`:

| Evento | Tag pubblicati |
|---|---|
| push su `main` che modifica `app.py`, `requirements.txt`, `Dockerfile` o il workflow | `latest`, `sha-<commit>` |
| tag git `vX.Y.Z` | `X.Y.Z`, `sha-<commit>` |
| avvio manuale da **Actions** | `sha-<commit>` (e `latest` se lanciato su `main`) |

Per pubblicare una versione:

```bash
git tag v1.0.0
git push origin v1.0.0
```

Le correzioni allo scraper e alla protezione anti-bot sono basate sul fork [rizlas/subito-it-searcher](https://github.com/rizlas/subito-it-searcher).
