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

## Avvio con Docker

```bash
mkdir bindata
# copia telegram_api_credentials (ed eventualmente searches.tracked) in bindata/
docker compose up -d --build
docker compose logs -f
```

## Avvio con CasaOS

1. Copia il progetto in `/DATA/AppData/subito-searcher/` e i file di dati in `/DATA/AppData/subito-searcher/bindata/`.
2. Costruisci l'immagine: `sudo docker build -t subito-searcher:latest .`
3. In CasaOS: App Store → **+** → **Install a customized app** → **Import**, incolla `docker-compose.casaos.yml`.

## Avvio senza Docker

```bash
pip install -r requirements.txt
python app.py
```

## Anti-bot

Le pagine vengono scaricate con `curl_cffi`, che imita il fingerprint TLS di Chrome. Se subito.it risponde con la challenge DataDome, il polling si ferma per 24 ore e arriva un avviso su Telegram. Le pause sono configurabili in cima ad `app.py` (`queryDelay`, `pollDelay`, `botDetectionSleep`).
