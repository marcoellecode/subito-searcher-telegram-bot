# Subito Searcher Telegram Bot

Bot Telegram che controlla periodicamente le ricerche salvate su subito.it e invia i nuovi annunci in chat.

## Comandi

| Comando | Descrizione |
|---|---|
| `/add <url> [nome] [prezzo_min] [prezzo_max]` | aggiunge una ricerca (usa `null` per saltare un prezzo) |
| `/delete <nome>` | elimina una ricerca |
| `/list` | elenca le ricerche con gli annunci trovati |
| `/shortlist` | elenco compatto delle ricerche |

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
