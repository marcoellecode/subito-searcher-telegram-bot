# Subito Searcher Telegram Bot

Bot Telegram che controlla periodicamente le ricerche salvate su subito.it e invia i nuovi annunci in chat.

## Comandi

| Comando | Descrizione |
|---|---|
| `/add` | procedura guidata: parole chiave o link, prezzo minimo, massimo, nome |
| `/add <link> [nome] [min] [max]` | aggiunge una ricerca in una riga (`-` per saltare un prezzo) |
| `/list` | le ricerche salvate, con pulsanti per vedere gli annunci o eliminarle |
| `/items <nome>` | gli ultimi annunci memorizzati per una ricerca |
| `/delete [nome]` | elimina una ricerca; senza nome mostra i pulsanti con conferma |
| `/refresh` | controlla subito tutte le ricerche |
| `/status` | ultimo e prossimo controllo, eventuale pausa |
| `/pause` / `/resume` | sospende / riprende i controlli (`/resume` interrompe anche la pausa anti-bot) |
| `/help` | guida |

Incollare in chat un link di subito.it avvia direttamente la procedura guidata.
Il bot risponde solo alla chat configurata in `TELEGRAM_CHAT_ID`; i messaggi da altre chat vengono ignorati.

## File di dati

Lo script legge e scrive nella cartella da cui viene avviato:

- `telegram_api_credentials`: JSON con token del bot e chat id

  ```json
  {"token": "123456:ABC...", "chatid": "123456789"}
  ```

- `searches.tracked`: ricerche salvate e annunci già visti (creato automaticamente se manca)

Nessuno dei due va messo nel repository.

In alternativa al file si possono usare le variabili d'ambiente `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID`: se impostate hanno la precedenza e al primo avvio creano (o aggiornano) `telegram_api_credentials`.

## Immagine Docker

Ad ogni push su `main` che tocca `app.py`, `requirements.txt` o `Dockerfile`, GitHub Actions pubblica l'immagine (amd64 e arm64):

```
ghcr.io/marcoellecode/subito-searcher-telegram-bot:latest
```

Un tag `vX.Y.Z` pubblica anche l'immagine con quella versione.

## Avvio con CasaOS

1. App Store → **+** (in alto a destra) → **Install a customized app** → **Import**.
2. Incolla il contenuto di [`docker-compose.casaos.yml`](docker-compose.casaos.yml).
3. Compila `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID` e premi **Install**.

I dati finiscono in `/DATA/AppData/subito-searcher/bindata/`: per riusare ricerche esistenti copia lì `searches.tracked`.
Per aggiornare l'app basta ricaricare l'immagine `latest` da CasaOS (o ricrearla).

## Avvio con Docker

```bash
mkdir bindata
# copia in bindata/ telegram_api_credentials (oppure imposta TELEGRAM_TOKEN e TELEGRAM_CHAT_ID)
docker compose up -d            # usa l'immagine pubblicata
docker compose up -d --build    # oppure la costruisce in locale
docker compose logs -f
```

## Avvio senza Docker

```bash
pip install -r requirements.txt
python app.py
```

## Anti-bot

Le pagine vengono scaricate con `curl_cffi`, che imita il fingerprint TLS di Chrome. Se subito.it risponde con la challenge DataDome, il polling si ferma per 24 ore e arriva un avviso su Telegram. Le pause sono configurabili in cima ad `app.py` (`queryDelay`, `pollDelay`, `botDetectionSleep`).
