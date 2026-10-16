import asyncio
import threading
import sys
from telegram import (
    Update,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
    ContextTypes,
    filters,
)

import json
import os
import hashlib
from html import escape
from urllib.parse import urlparse, parse_qs, quote_plus

import requests
# curl_cffi imita il fingerprint TLS di Chrome: con requests normale DataDome blocca
from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup
import time as t
from datetime import datetime, timedelta


apiCredentials = dict()
telegramApiFile = "telegram_api_credentials"

queries = dict()
dbFile = "searches.tracked"

# headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) Gecko/20100101 Firefox/146.0"}

headers = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }

# marcatori della pagina di challenge DataDome
antibotMarkers = ("captcha-delivery.com", "Please enable JS", "dd.js")

queryDelay = 60             # secondi tra una ricerca e l'altra
pollDelay = 600             # secondi tra un giro completo e il successivo
botDetectionSleep = 86400   # pausa in caso di blocco anti-bot (24h)

daemonrunning = True
pausedUntil = None          # pausa automatica dopo un blocco anti-bot
pollingPaused = False       # pausa manuale (/pause)
manualRefresh = False       # giro richiesto con /refresh
pollRunning = False
lastPoll = None
lastPollNew = None
nextPoll = None
pollNow = threading.Event() # sveglia il thread di polling (/refresh, /resume)

# il thread di polling e i comandi Telegram toccano queries insieme
dataLock = threading.RLock()

# ricerche aggiunte senza il primo giro (anti-bot): al primo controllo non notificano,
# altrimenti arriverebbero tutti gli annunci gia' presenti
seedPending = set()

searchBaseUrl = "https://www.subito.it/annunci-italia/vendita/usato/?q="


class AntiBotError(Exception):
    '''Sollevata quando subito.it risponde con la challenge anti-bot'''
    pass

#-------------------------------------
#load telegram api from file
def load_api_credentials():
    '''A function to load the telegram api credentials from the json file'''
    global apiCredentials
    global telegramApiFile

    # se impostate, le variabili d'ambiente (es. da CasaOS) hanno la precedenza e aggiornano il file
    envToken = os.environ.get("TELEGRAM_TOKEN", "").strip()
    envChatId = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if envToken and envChatId:
        apiCredentials = {"token": envToken, "chatid": envChatId}
        with open(telegramApiFile, 'w') as file:
            file.write(json.dumps(apiCredentials, indent=4))
        return

    if os.path.isfile(telegramApiFile):
        with open(telegramApiFile) as file:
            apiCredentials = json.load(file)

    if not apiCredentials.get("token") or not apiCredentials.get("chatid"):
        print("Missing Telegram credentials: set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID "
              "or create the file '" + telegramApiFile + "'.")
        sys.exit(1)

#-------------------------------------
# load queries from file
def load_queries():
    '''A function to load the queries from the json file'''
    global queries
    global dbFile
    if not os.path.isfile(dbFile):
        return
    with open(dbFile) as file:
        queries = json.load(file)

#-------------------------------------
#save queries to json file    
def save_queries():
    '''A function to save the queries
    '''
    with open(dbFile, 'w') as file:
        file.write(json.dumps(queries,indent=4))

#-------------------------------------
# helpers for the nested searches structure: name -> url -> minPrice -> maxPrice -> items
def iter_searches():
    '''Yields (name, url, minPrice, maxPrice, items) for every saved search'''
    for name, urls in queries.items():
        for url, mins in urls.items():
            for minP, maxs in mins.items():
                for maxP, items in maxs.items():
                    yield name, url, minP, maxP, items

def search_names():
    with dataLock:
        return list(queries)

def search_id(name):
    '''Short stable id for inline buttons (callback_data is limited to 64 bytes)'''
    return hashlib.md5(name.encode("utf-8")).hexdigest()[:10]

def find_search_name(sid):
    for name in queries:
        if search_id(name) == sid:
            return name
    return None

def match_search_name(text):
    '''Exact match first, then case-insensitive'''
    if text in queries:
        return text
    for name in queries:
        if name.lower() == text.lower():
            return name
    return None

def format_range(minPrice, maxPrice):
    if minPrice == "null" and maxPrice == "null":
        return "qualsiasi prezzo"
    if minPrice == "null":
        return "fino a " + str(maxPrice) + " €"
    if maxPrice == "null":
        return "da " + str(minPrice) + " €"
    return str(minPrice) + "–" + str(maxPrice) + " €"

def format_price(price):
    return str(price) + " €" if isinstance(price, int) else "prezzo n.d."

def format_item(name, title, price, location, link):
    '''Telegram notification for a new listing (HTML)'''
    return ("🔔 <i>" + escape(name) + "</i>\n"
            + "<b>" + escape(str(title)) + "</b>\n"
            + "💶 " + format_price(price) + "\n"
            + "📍 " + escape(str(location)) + "\n"
            + escape(link))

def name_from_url(url):
    '''Default search name: the q= parameter of the subito.it url'''
    q = parse_qs(urlparse(url).query).get("q")
    return q[0].strip() if q and q[0].strip() else "ricerca"

def is_subito_url(text):
    return text.startswith("http") and "subito.it" in text

#-------------------------------------
# printing a compact list of trackings
def format_search_list():
    '''Returns (html text, inline keyboard) with all the saved searches'''
    with dataLock:
        searches = list(iter_searches())
    if not searches:
        return "Non hai ricerche salvate.\nUsa /add per aggiungerne una.", None

    output = ["📋 <b>Le tue ricerche</b> (" + str(len(searches)) + ")"]
    buttons = []
    for i, (name, url, minP, maxP, items) in enumerate(searches, 1):
        output.append("")
        output.append(str(i) + ". <b>" + escape(name) + "</b>")
        output.append("💶 " + format_range(minP, maxP) + " · 📦 " + str(len(items)) + " annunci")
        output.append('🔗 <a href="' + escape(url) + '">apri su subito.it</a>')
        sid = search_id(name)
        buttons.append([
            InlineKeyboardButton("🔍 " + str(i) + ". " + name[:25], callback_data="items:" + sid),
            InlineKeyboardButton("🗑", callback_data="delask:" + sid),
        ])
    return "\n".join(output), InlineKeyboardMarkup(buttons)

#-------------------------------------
#print queries
def format_items(name, limit=20):
    '''Returns the most recent listings stored for a search (html)'''
    with dataLock:
        found = [s for s in iter_searches() if s[0] == name]
        if not found:
            return "Ricerca <b>" + escape(name) + "</b> non trovata."
        _, url, minP, maxP, items = found[0]
        items = list(items.items())

    output = ["🔍 <b>" + escape(name) + "</b> · " + format_range(minP, maxP)]
    if not items:
        output.append("\nNessun annuncio memorizzato.")
        return "\n".join(output)

    # gli annunci sono salvati in ordine di scoperta: i piu' recenti in fondo
    shown = list(reversed(items))[:limit]
    output.append(str(len(items)) + " annunci" + (", ultimi " + str(len(shown)) if len(items) > len(shown) else "") + "\n")
    for link, item in shown:
        output.append('• <a href="' + escape(link) + '">' + escape(str(item.get('title'))) + "</a>"
                      + " — " + format_price(item.get('price'))
                      + " — " + escape(str(item.get('location'))))
    return "\n".join(output)

#-------------------------------------
#delete queries
def delete(toDelete):
    '''A function to delete a query

    Arguments
    ---------
    toDelete: str
        the query to delete

    Example usage
    -------------
    >>> delete("query")
    '''
    global queries
    with dataLock:
        name = match_search_name(toDelete)
        if name is None:
            return "Ricerca <b>" + escape(toDelete) + "</b> non trovata. Usa /list per vedere i nomi."
        queries.pop(name)
        seedPending.discard(name)
        save_queries()
    return "🗑 Ricerca <b>" + escape(name) + "</b> eliminata."

#-------------------------------------
#add queries
def add(url, name, minPrice, maxPrice):
    ''' A function to add a new query

    Arguments
    ---------
    url: str
        the url to run the query on
    name: str
        the name of the query
    minPrice: str
        the minimum price to search for
    maxPrice: str
        the maximum price to search for

    Returns
    -------
    output: str
        html report for Telegram

    Example usage
    -------------
    >>> add("https://www.subito.it/annunci-italia/vendita/usato/?q=auto", "auto", "100", "null")
    '''
    global queries
    with dataLock:
        # If the query has already been added previously, replace it
        replaced = name in queries
        queries[name] = {url:{minPrice: {maxPrice:{}}}}
        seedPending.discard(name)
        save_queries()

        output = ["✅ Ricerca <b>" + escape(name) + "</b> " + ("aggiornata" if replaced else "salvata"),
                  "💶 " + format_range(minPrice, maxPrice)]

        if pausedUntil and pausedUntil > datetime.now():
            seedPending.add(name)
            output.append("⏸ Controlli in pausa anti-bot: verrà controllata alla ripresa.")
            return "\n".join(output)

        # first run with no notify: stores the listings already online
        try:
            text, _ = run_query(url, name, minPrice, maxPrice)
            print(text)
            count = len(queries[name][url][minPrice][maxPrice])
            output.append("📦 " + str(count) + " annunci già presenti memorizzati: ti avviserò solo dei nuovi.")
        except AntiBotError:
            seedPending.add(name)
            output.append("⚠️ subito.it ha attivato la protezione anti-bot: la ricerca verrà controllata al prossimo giro.")
        except Exception as e:
            seedPending.add(name)
            output.append("⚠️ Primo controllo non riuscito (" + escape(str(e)) + "): riprovo al prossimo giro.")
    return "\n".join(output)


#-------------------------------------
#run single querie
def run_query(url, name, minPrice, maxPrice):
    '''A function to run a query

    Arguments
    ---------
    url: str
        the url to run the query on
    name: str
        the name of the query
    minPrice: str
        the minimum price to search for
    maxPrice: str
        the maximum price to search for

    Returns
    -------
    (output, msgs): (str, list)
        report on the run and the Telegram messages for the new listings (the caller sends them)

    Example usage
    -------------
    >>> run_query("https://www.subito.it/annunci-italia/vendita/usato/?q=auto", "query", "100", "null")
    '''
    global queries
    global daemonrunning
    
    output = []
    msg = []

    page = cffi_requests.get(url, headers=headers, impersonate="chrome124", timeout=15)

    if page.status_code == 403 or any(m in page.text for m in antibotMarkers):
        raise AntiBotError()

    page.raise_for_status()
    
    # print(page)
    if page.status_code == 200:
        output.append(str(datetime.now().strftime("%Y-%m-%d, %H:%M:%S")) + " running query ( " + str(name) +" - "+ str(url) +" ) ")
        products_deleted = False
        soup = BeautifulSoup(page.text, 'html.parser')
        #print(soup)

        #-----------------------------
        # product_list_items = soup.find_all('div', class_=re.compile(r'item-card'))
        script_tag = soup.find('script', id='__NEXT_DATA__')
        if not script_tag:
            # senza __NEXT_DATA__ quasi sempre e' una challenge anti-bot
            raise AntiBotError()

        json_data = json.loads(script_tag.string)

        try:
            # struttura cambiata a maggio 2026: 'originalList' (L maiuscola), annunci senza wrapper 'item'
            items_list = json_data['props']['pageProps']['initialState']['items']['originalList']
        except KeyError:
            output.append("Warning: JSON path not found, subito.it may have changed the page structure.")
            items_list = []
        #-----------------------------

        #-----------------------------
        # for product in product_list_items:
        #     title = product.find('h2').string
        # dal piu' vecchio al piu' nuovo: gli annunci restano salvati (e notificati) in ordine cronologico
        for product in reversed(items_list):
        #-----------------------------

            try:
            #-----------------------------
            #    price=product.find('p',class_=re.compile(r'price')).contents[0]
            #    # check if the span tag exists
            #    price_soup = BeautifulSoup(price, 'html.parser')
            #    if type(price_soup) == Tag:
            #       continue
            #    #at the moment (20.5.2021) the price is under the 'p' tag with 'span' inside if shipping available
            #    price = int(price.replace('.','')[:-2])
            # except:
                item_key = product.get('urn')
                if not item_key: continue

                title = product.get('subject', 'No Title')
                link = product.get('urls', {}).get('default', '')
                location = product.get('geo', {}).get('town', {}).get('value', 'Unknown location')

                # Price extraction
                raw_price = None
            #-----------------------------

                price = "Unknown price"

            #-----------------------------    
            # link = product.find('a').get('href')
                features = product.get('features', {})
                price_feature = features.get('/price')
                if price_feature and 'values' in price_feature:
                    raw_price = price_feature['values'][0].get('key')

                if raw_price:
                    try:
                        price = int(raw_price)
                    except ValueError:
                        pass

                is_sold = product.get('sold', False)
            #-----------------------------    

            #-----------------------------    
            # sold = product.find('span',re.compile(r'item-sold-badge'))
            except Exception as e:
                continue
            #-----------------------------  


            # check if the product has already been sold
            #-----------------------------  
            # if sold != None:
            if is_sold:
            #-----------------------------              
                # if the product has previously been saved remove it from the file
                if queries.get(name).get(url).get(minPrice).get(maxPrice).get(link):
                    del queries[name][url][minPrice][maxPrice][link]
                    products_deleted = True
                continue

            #-----------------------------  
            #try:
            #    location = product.find('span',re.compile(r'town')).string + product.find('span',re.compile(r'city')).string
            #except:
            #    output.append(str(datetime.now().strftime("%Y-%m-%d, %H:%M:%S")) + " Unknown location for item " + str(title))
            #    location = "Unknown location"
            #-----------------------------  


            if minPrice == "null" or price == "Unknown price" or price>=int(minPrice):
                if maxPrice == "null" or price == "Unknown price" or price<=int(maxPrice):
                    if not queries.get(name).get(url).get(minPrice).get(maxPrice).get(link):   # found a new element
                        queries[name][url][minPrice][maxPrice][link] ={'title': title, 'price': price, 'location': location}
                        output.append(" Adding result: "+ str(price) + "€ - " + str(title) + " - " + str(location))
                        msg.append(format_item(name, title, price, location, link)) # compose telegram msg



        # recap of query run
        if len(msg) > 0:
            output.append(str(len(msg)) + " new elements have been found.\n")
            save_queries()
        else:
            output.append("All lists are already up to date.\n")
            # if at least one search was deleted, update the search file
            if products_deleted:
                save_queries()
    else:
        output.append(str(datetime.now().strftime("%Y-%m-%d, %H:%M:%S")) + " Failed to fetch " + str(url) + ": status code " + str(page.status_code))
        send_telegram_messages(["⚠️ Errore leggendo la ricerca <b>" + escape(name) + "</b>: status code " + str(page.status_code)])
        daemonrunning = False

    return "\n".join(output), msg

#-------------------------------------
#refresh list
def refresh(notify):
    '''A function to refresh the queries

    Arguments
    ---------
    notify: bool
        whether to send notifications or not

    Returns
    -------
    (output, newCount): (str, int)

    Example usage
    -------------
    >>> refresh(True)   # Refresh queries and send notifications
    >>> refresh(False)  # Refresh queries and don't send notifications
    '''
    global queries
    global pausedUntil
    output = []
    newCount = 0
    # copia perche' /add e /delete possono modificare queries durante il giro
    with dataLock:
        searches = [(name, url, minP, maxP) for name, url, minP, maxP, _ in iter_searches()]

    for i, (name, url, minP, maxP) in enumerate(searches):
        if i > 0:
            t.sleep(queryDelay)
        if pollingPaused:
            output.append("Polling paused, stopping the cycle.")
            break
        now = datetime.now().strftime("%Y-%m-%d, %H:%M:%S")
        try:
            with dataLock:
                # eliminata o modificata mentre il giro era in corso
                if queries.get(name, {}).get(url, {}).get(minP, {}).get(maxP) is None:
                    continue
                text, msgs = run_query(url, name, minP, maxP)
                seeding = name in seedPending
                seedPending.discard(name)
            output.append(text)
            # i messaggi partono fuori dal lock: con molti annunci l'invio richiede tempo
            if notify and msgs and not seeding:
                send_telegram_messages(msgs)
                newCount += len(msgs)
        except AntiBotError:
            # interrompe il giro per non peggiorare la reputazione dell'IP
            pausedUntil = datetime.now() + timedelta(seconds=botDetectionSleep)
            output.append(now + " Bot detection triggered, polling paused until " + pausedUntil.strftime("%Y-%m-%d %H:%M"))
            send_telegram_messages(["⚠️ subito.it ha attivato la protezione anti-bot.\n"
                                    "Controlli in pausa fino al " + pausedUntil.strftime("%d/%m alle %H:%M")
                                    + ", poi riprendono da soli.\nPer forzare la ripresa: /resume"])
            break
        except Exception as e:
            # un errore su una ricerca salta solo quella
            output.append(now + " ***Error on " + str(name) + ": " + str(e) + "***")
    return "\n".join(output), newCount

#-------------------------------------
#send telegram notify
def send_telegram_messages(messages):
    '''A function to send HTML messages to telegram

    Arguments
    ---------
    messages: list
        the list of messages to send

    Example usage
    -------------
    >>> send_telegram_messages(["message1", "message2"])
    '''
    request_url = "https://api.telegram.org/bot" + apiCredentials["token"] + "/sendMessage"
    for i, msg in enumerate(messages):
        if i > 0:
            t.sleep(1)  # Telegram limita a circa un messaggio al secondo per chat
        data = {"chat_id": apiCredentials["chatid"], "text": msg, "parse_mode": "HTML"}
        try:
            for attempt in range(2):
                r = requests.post(request_url, data=data, timeout=15)
                if r.status_code == 429 and attempt == 0:
                    t.sleep(r.json().get("parameters", {}).get("retry_after", 5))
                    continue
                if not r.ok:
                    print(datetime.now().strftime("%Y-%m-%d, %H:%M:%S") + " Telegram send failed: " + r.text)
                break
        except requests.exceptions.RequestException as e:
            print(datetime.now().strftime("%Y-%m-%d, %H:%M:%S") + " Telegram send failed: " + str(e))



# ==========================
# COMMAND FUNCTION
# ==========================

helpText = (
    "🤖 <b>Subito Searcher</b>\n"
    "Controllo le tue ricerche su subito.it ogni " + str(pollDelay // 60) + " minuti "
    "e ti scrivo quando escono annunci nuovi.\n\n"
    "<b>Comandi</b>\n"
    "/add – aggiungi una ricerca (procedura guidata)\n"
    "/list – le tue ricerche, con annunci ed eliminazione\n"
    "/delete – elimina una ricerca\n"
    "/refresh – controlla subito tutte le ricerche\n"
    "/status – stato dei controlli\n"
    "/pause – sospendi i controlli\n"
    "/resume – riprendi i controlli\n"
    "/cancel – annulla l'operazione in corso\n\n"
    "<b>Scorciatoie</b>\n"
    "• Incolla un link di subito.it per aggiungerlo come ricerca\n"
    "• <code>/add &lt;link&gt; [nome] [min] [max]</code> in una riga sola\n"
    "  es. <code>/add https://www.subito.it/...?q=wyse wyse 50 150</code>\n"
    "  usa <code>-</code> per saltare un prezzo"
)

commandMenu = [
    BotCommand("add", "Aggiungi una ricerca"),
    BotCommand("list", "Le tue ricerche"),
    BotCommand("delete", "Elimina una ricerca"),
    BotCommand("refresh", "Controlla subito"),
    BotCommand("status", "Stato dei controlli"),
    BotCommand("pause", "Sospendi i controlli"),
    BotCommand("resume", "Riprendi i controlli"),
    BotCommand("help", "Guida"),
]

def parse_price(text):
    '''Returns "null" for no limit, the price as a string, or None if invalid'''
    text = text.strip().lower().replace("€", "").replace(".", "").strip()
    if text in ("", "-", "null", "salta", "no", "nessuno"):
        return "null"
    return text if text.isdigit() else None

def parse_add_args(args):
    '''Parses "/add <url> [name] [min] [max]" or "/add <name...> <url> [min] [max]".

    Returns (url, name, minPrice, maxPrice) or an error string.
    '''
    urlIdx = next((i for i, a in enumerate(args) if a.startswith("http")), None)
    if urlIdx is None:
        return "Manca il link di subito.it.\nUsa /add senza argomenti per la procedura guidata."
    url = args[urlIdx]
    nameWords = list(args[:urlIdx])
    prices = []
    for a in args[urlIdx + 1:]:
        p = parse_price(a)
        if p is not None and len(prices) < 2:
            prices.append(p)
        else:
            nameWords.append(a)
    minPrice = prices[0] if len(prices) > 0 else "null"
    maxPrice = prices[1] if len(prices) > 1 else "null"
    if minPrice != "null" and maxPrice != "null" and int(minPrice) > int(maxPrice):
        return "Il prezzo minimo è più alto del massimo."
    name = " ".join(nameWords).strip() or name_from_url(url)
    return url, name, minPrice, maxPrice

def status_text():
    fmt = "%d/%m %H:%M"
    with dataLock:
        searches = list(iter_searches())
    totalItems = sum(len(s[4]) for s in searches)
    output = ["📊 <b>Stato</b>",
              "Ricerche: " + str(len(searches)) + " · annunci memorizzati: " + str(totalItems)]
    if pollRunning:
        output.append("🔄 Controllo in corso")
    if lastPoll:
        output.append("Ultimo controllo: " + lastPoll.strftime(fmt)
                      + ("" if lastPollNew is None else " (" + str(lastPollNew) + " nuovi)"))
    if pollingPaused:
        output.append("⏸ Controlli sospesi (/resume per riprendere)")
    elif pausedUntil and pausedUntil > datetime.now():
        output.append("⚠️ Pausa anti-bot fino al " + pausedUntil.strftime(fmt) + " (/resume per forzare)")
    else:
        output.append("▶️ Controlli attivi, ogni " + str(pollDelay // 60) + " minuti")
        if nextPoll and not pollRunning:
            output.append("Prossimo controllo: " + nextPoll.strftime(fmt))
    return "\n".join(output)

def split_message(text, limit=4000):
    '''Splits on line boundaries to stay under the Telegram limit of 4096 chars'''
    chunks = []
    current = ""
    for line in text.split("\n"):
        if current and len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks



# ==========================
# TELEGRAM HANDLERS
# ==========================

ASK_WHAT, ASK_MIN, ASK_MAX, ASK_NAME = range(4)

skipKeyboard = ReplyKeyboardMarkup([["Salta"]], resize_keyboard=True, one_time_keyboard=True)
noPreview = LinkPreviewOptions(is_disabled=True)

async def reply_html(update: Update, text, reply_markup=None):
    chunks = split_message(text)
    for i, chunk in enumerate(chunks):
        await update.effective_message.reply_text(
            chunk,
            parse_mode=ParseMode.HTML,
            link_preview_options=noPreview,
            reply_markup=reply_markup if i == len(chunks) - 1 else None,
        )

async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''Only the configured chat can use the bot'''
    chat = update.effective_chat
    if chat is None or str(chat.id) != str(apiCredentials["chatid"]):
        print(f"[FROM TELEGRAM] ignored update from chat {chat.id if chat else '?'}")
        raise ApplicationHandlerStop
    if update.message and update.message.text:
        print(f"[FROM TELEGRAM] text={update.message.text}")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_html(update, helpText)

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, keyboard = await asyncio.to_thread(format_search_list)
    await reply_html(update, text, keyboard)

async def cmd_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await cmd_list(update, context)
        return
    name = await asyncio.to_thread(match_search_name, " ".join(context.args))
    text = await asyncio.to_thread(format_items, name or " ".join(context.args))
    await reply_html(update, text)

async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        result = await asyncio.to_thread(delete, " ".join(context.args))
        await reply_html(update, result)
        return
    names = await asyncio.to_thread(search_names)
    if not names:
        await reply_html(update, "Non hai ricerche salvate.")
        return
    buttons = [[InlineKeyboardButton("🗑 " + n[:40], callback_data="delask:" + search_id(n))] for n in names]
    buttons.append([InlineKeyboardButton("Annulla", callback_data="delno")])
    await reply_html(update, "Quale ricerca vuoi eliminare?", InlineKeyboardMarkup(buttons))

async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global manualRefresh
    if pollingPaused:
        await reply_html(update, "⏸ I controlli sono sospesi: usa /resume.")
        return
    if pausedUntil and pausedUntil > datetime.now():
        await reply_html(update, "⚠️ Pausa anti-bot fino al " + pausedUntil.strftime("%d/%m %H:%M")
                         + ".\nUsa /resume se vuoi forzare la ripresa.")
        return
    if pollRunning:
        await reply_html(update, "🔄 C'è già un controllo in corso.")
        return
    count = len(await asyncio.to_thread(search_names))
    if count == 0:
        await reply_html(update, "Non hai ricerche salvate. Usa /add.")
        return
    manualRefresh = True
    pollNow.set()
    minutes = (count - 1) * queryDelay // 60
    await reply_html(update, "🔄 Controllo avviato su " + str(count) + " ricerche"
                     + (" (circa " + str(minutes) + " min)" if minutes else "") + ".")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_html(update, await asyncio.to_thread(status_text))

async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global pollingPaused
    pollingPaused = True
    await reply_html(update, "⏸ Controlli sospesi. Usa /resume per riprendere.")

async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global pollingPaused, pausedUntil
    wasAntibot = pausedUntil and pausedUntil > datetime.now()
    pollingPaused = False
    pausedUntil = None
    pollNow.set()
    text = "▶️ Controlli ripresi, parto subito."
    if wasAntibot:
        text += "\nSe subito.it blocca ancora, si rimetteranno in pausa da soli."
    await reply_html(update, text)

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action, _, sid = query.data.partition(":")

    if action == "delno":
        await query.edit_message_text("Eliminazione annullata.")
        return

    name = await asyncio.to_thread(find_search_name, sid)
    if name is None:
        await query.edit_message_text("Questa ricerca non esiste più.")
        return

    if action == "items":
        await reply_html(update, await asyncio.to_thread(format_items, name))
    elif action == "delask":
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Elimina", callback_data="del:" + sid),
            InlineKeyboardButton("❌ Annulla", callback_data="delno"),
        ]])
        await query.edit_message_text("Eliminare la ricerca <b>" + escape(name) + "</b>?",
                                      parse_mode=ParseMode.HTML, reply_markup=keyboard)
    elif action == "del":
        result = await asyncio.to_thread(delete, name)
        await query.edit_message_text(result, parse_mode=ParseMode.HTML)

#-------------------------------------
# /add: one line or guided conversation
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        parsed = parse_add_args(context.args)
        if isinstance(parsed, str):
            await reply_html(update, parsed)
            return ConversationHandler.END
        await update.effective_message.reply_text("⏳ Salvo la ricerca e leggo gli annunci attuali...")
        await reply_html(update, await asyncio.to_thread(add, *parsed))
        return ConversationHandler.END

    context.user_data.clear()
    await reply_html(update, "🔎 <b>Nuova ricerca</b>\n"
                     "Mandami le <b>parole da cercare</b> (es. <i>dell wyse 5070</i>) "
                     "oppure il <b>link</b> di una ricerca su subito.it, "
                     "se vuoi categoria, zona o altri filtri.\n\n/cancel per annullare.")
    return ASK_WHAT

async def add_from_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''A subito.it link pasted in chat starts the guided /add'''
    context.user_data.clear()
    return await add_what(update, context)

async def add_what(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if is_subito_url(text):
        context.user_data["url"] = text
        context.user_data["name"] = name_from_url(text)
    elif text.startswith("http"):
        await reply_html(update, "Il link deve essere di subito.it. Riprova o /cancel.")
        return ASK_WHAT
    else:
        context.user_data["url"] = searchBaseUrl + quote_plus(text)
        context.user_data["name"] = text
    await reply_html(update, "💶 <b>Prezzo minimo</b>? Scrivi un numero oppure premi Salta.", skipKeyboard)
    return ASK_MIN

async def add_min(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price = parse_price(update.message.text)
    if price is None:
        await reply_html(update, "Scrivi solo un numero (es. <code>50</code>) oppure premi Salta.", skipKeyboard)
        return ASK_MIN
    context.user_data["min"] = price
    await reply_html(update, "💶 <b>Prezzo massimo</b>? Scrivi un numero oppure premi Salta.", skipKeyboard)
    return ASK_MAX

async def add_max(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price = parse_price(update.message.text)
    if price is None:
        await reply_html(update, "Scrivi solo un numero (es. <code>150</code>) oppure premi Salta.", skipKeyboard)
        return ASK_MAX
    minPrice = context.user_data["min"]
    if price != "null" and minPrice != "null" and int(price) < int(minPrice):
        await reply_html(update, "Il massimo deve essere almeno " + minPrice + " €. Riprova.", skipKeyboard)
        return ASK_MAX
    context.user_data["max"] = price
    keyboard = ReplyKeyboardMarkup([[context.user_data["name"][:60]]], resize_keyboard=True, one_time_keyboard=True)
    await reply_html(update, "🏷 <b>Nome</b> della ricerca? Premi il suggerimento o scrivine uno.", keyboard)
    return ASK_NAME

async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    data = context.user_data
    exists = name in await asyncio.to_thread(search_names)
    await update.message.reply_text(
        ("♻️ Esiste già una ricerca con questo nome: la sostituisco.\n" if exists else "")
        + "⏳ Salvo la ricerca e leggo gli annunci attuali...",
        reply_markup=ReplyKeyboardRemove())
    result = await asyncio.to_thread(add, data["url"], name, data["min"], data["max"])
    data.clear()
    await reply_html(update, result)
    return ConversationHandler.END

async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Operazione annullata.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_html(update, "Comando sconosciuto. /help per la lista dei comandi.")

async def receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_html(update, "Per aggiungere una ricerca usa /add oppure incolla un link di subito.it.\n/help per tutti i comandi.")



# ==========================
# TERMINAL INPUT THREAD
# ==========================

def stdin_sender(loop, bot):
    for line in sys.stdin:
        msg = line.strip()
        if msg:
            asyncio.run_coroutine_threadsafe(
                bot.send_message(
                    chat_id=apiCredentials["chatid"],
                    text=msg
                ),
                loop
            )



# ==========================
# POST INIT (GET REAL LOOP)
# ==========================

async def post_init(app):
    loop = asyncio.get_running_loop()

    threading.Thread(
        target=stdin_sender,
        args=(loop, app.bot),
        daemon=True
    ).start()

    print("stdin thread started")

    # menu dei comandi mostrato da Telegram accanto al campo di testo
    try:
        await app.bot.set_my_commands(commandMenu)
    except Exception as e:
        print("Could not set the command menu: " + str(e))


# ==========================
# BACKGROUND TASK
# ==========================
def background_task():
    '''Ciclo in background che elabora i dati periodicamente.'''
    global manualRefresh, pollRunning, lastPoll, lastPollNew, nextPoll

    while daemonrunning:
        if pollingPaused:
            nextPoll = None
            pollNow.wait()
            pollNow.clear()
            continue
        if pausedUntil and pausedUntil > datetime.now():
            nextPoll = pausedUntil
            pollNow.wait((pausedUntil - datetime.now()).total_seconds())
            pollNow.clear()
            continue

        manual = manualRefresh
        manualRefresh = False
        pollRunning = True
        newCount = None
        # un'eccezione qui ucciderebbe il thread per sempre (il bot risponderebbe ma non cercherebbe piu')
        try:
            text, newCount = refresh(True)
            print(text)
        except Exception as e:
            print(datetime.now().strftime("%Y-%m-%d, %H:%M:%S") + " Polling cycle failed: " + str(e))
        finally:
            pollRunning = False
        lastPoll = datetime.now()
        lastPollNew = newCount

        blocked = pausedUntil and pausedUntil > datetime.now()
        if manual and not blocked and newCount is not None:
            send_telegram_messages(["✅ Controllo completato: "
                                    + (str(newCount) + " nuovi annunci." if newCount else "nessun annuncio nuovo.")])

        nextPoll = datetime.now() + timedelta(seconds=pollDelay)
        print(str(pollDelay) + " seconds to next poll.")
        pollNow.wait(pollDelay)
        pollNow.clear()


# ==========================
# MAIN BOT THREAD
# ==========================

def main():

    app = ApplicationBuilder().token(apiCredentials["token"]).post_init(post_init).build()

    # Only the configured chat
    app.add_handler(TypeHandler(Update, guard), group=-1)

    # /add, guided or in one line; a pasted subito.it link starts it too
    app.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("add", add_start),
            MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r"^\s*https?://\S*subito\.it"), add_from_link),
        ],
        states={
            ASK_WHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_what)],
            ASK_MIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_min)],
            ASK_MAX: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_max)],
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
        allow_reentry=True,
    ))

    # Commands
    app.add_handler(CommandHandler(["start", "help"], cmd_help))
    app.add_handler(CommandHandler(["list", "shortlist"], cmd_list))
    app.add_handler(CommandHandler("items", cmd_items))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("refresh", cmd_refresh))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("cancel", add_cancel))
    app.add_handler(CallbackQueryHandler(on_button))

    # Everything else
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_message))


    print("Bot running...")
    print("Commands: /add | /list | /delete | /refresh | /status | /pause | /resume | /help")
    print("Type here to send messages to chat ID.")

    app.run_polling()


if __name__ == "__main__":
    load_api_credentials()

    load_queries()
    save_queries()

    daemonrunning = True
    # Avvia il thread per il ciclo in background
    thread = threading.Thread(target=background_task, daemon=True)
    thread.start()

    # interfaccia web per la rete locale (webui.py), stesso processo e stessi dati
    import webui
    webui.start(sys.modules[__name__])

    main()
