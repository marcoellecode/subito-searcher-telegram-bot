import asyncio
import threading
import sys
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import json
import os

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
pausedUntil = None


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
# printing a compact list of trackings
def print_sitrep():
    '''A function to print a compact list of trackings'''
    global queries
    output = []
    i = 1
    for search in queries.items():
        output.append("\n" + str(i) + " search: "+ str(search[0]))
        for query_url in search[1].items():
            for minP in query_url[1].items():
                for maxP in minP[1].items():
                    output.append("query url: "+str(query_url[0]))
                    if minP[0] !="null":
                        output.append(str(minP[0])+"<")
                    if minP[0] !="null" or maxP[0] !="null":
                        output.append(" price ")
                    if maxP[0] !="null":
                        output.append("<" + str(maxP[0]))
        i+=1
    return "\n".join(output)

#-------------------------------------
#print queries
def print_queries():
    '''A function to print the queries'''
    global queries
    output = []
    for search in queries.items():
        output.append("\n\n"+"search: "+ str(search[0]))
        for query_url in search[1]:
            output.append("query url:" + str(query_url))
            for url in search[1].items():
                for minP in url[1].items():
                    for maxP in minP[1].items():
                        for result in maxP[1].items():
                            output.append("\n" + str(result[1].get('title')))#.encode("utf-8", errors="ignore")))
                            output.append("Price: " + str(result[1].get('price')))
                            output.append("Location :" + str(result[1].get('location')))
                            output.append("Link: " + str(result[0]))
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
    output = []
    try:
        queries.pop(toDelete)
        output.append(str(toDelete) + " Deleted")
        save_queries()
    except:
        output.append(str(toDelete) + " Not Found")
    return "\n".join(output)

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

    Example usage
    -------------
    >>> add("https://www.subito.it/annunci-italia/vendita/usato/?q=auto", "auto", 100, "null")
    '''
    global queries
    output = []
    # If the query has already been added previously, delete it
    if queries.get(name):
        output.append(delete(name))
    queries[name] = {url:{minPrice: {maxPrice:{}}}}
    output.append(str(name) + " Added")

    # add run query with no notify
    try:
        output.append(run_query(url, name, False, minPrice, maxPrice))
    except AntiBotError:
        output.append("Bot detection triggered: search saved, it will be checked at the next poll.")
    except Exception as e:
        output.append("Error running query: " + str(e))

    save_queries()
    return "\n".join(output)


#-------------------------------------
#run single querie
def run_query(url, name, notify, minPrice, maxPrice):
    '''A function to run a query

    Arguments
    ---------
    url: str
        the url to run the query on
    name: str
        the name of the query
    notify: bool
        whether to send notifications or not
    minPrice: str
        the minimum price to search for
    maxPrice: str
        the maximum price to search for

    Returns
    -------
    output: str
        report on the entry added

    Example usage
    -------------
    >>> run_query("https://www.subito.it/annunci-italia/vendita/usato/?q=auto", "query", True, 100, "null")
    '''
    global queries
    global daemonrunning
    
    output = []

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


        msg = []

        #-----------------------------
        # for product in product_list_items:
        #     title = product.find('h2').string
        for product in items_list:
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
                        tmp = (str(price) + "€ - " + str(title) + " - " + str(location) + " - " + str(link) + '\n' ) # compose telegram msg
                        msg.append(tmp)


                        
        # recap of query run                
        if len(msg) > 0:
            if notify:
                send_telegram_messages(msg)
                output.append(str(len(msg)) + " new elements have been found.\n")
            save_queries()
        else:
            output.append("All lists are already up to date.\n")
            # if at least one search was deleted, update the search file
            if products_deleted:
                save_queries()
    else:
        output.append(str(datetime.now().strftime("%Y-%m-%d, %H:%M:%S")) + " Failed to fetch " + str(url) + ": status code " + str(page.status_code))
        msg = str(datetime.now().strftime("%Y-%m-%d, %H:%M:%S")) + " Failed to fetch " + str(url) + ": status code " + str(page.status_code)
        send_telegram_messages([msg])
        daemonrunning = False
        
    return "\n".join(output)

#-------------------------------------
#refresh list
def refresh(notify):
    '''A function to refresh the queries

    Arguments
    ---------
    notify: bool
        whether to send notifications or not

    Example usage
    -------------
    >>> refresh(True)   # Refresh queries and send notifications
    >>> refresh(False)  # Refresh queries and don't send notifications
    '''
    global queries
    global pausedUntil
    output = []
    first = True
    # list() perche' /add e /delete possono modificare queries durante il giro
    for search in list(queries.items()):
        for url in search[1].items():
            for minP in url[1].items():
                for maxP in minP[1].items():
                    if not first:
                        t.sleep(queryDelay)
                    first = False
                    now = datetime.now().strftime("%Y-%m-%d, %H:%M:%S")
                    try:
                        output.append(run_query(url[0], search[0], notify, minP[0], maxP[0]))
                    except AntiBotError:
                        # interrompe il giro per non peggiorare la reputazione dell'IP
                        pausedUntil = datetime.now() + timedelta(seconds=botDetectionSleep)
                        text = ("Bot detection triggered. Polling paused until "
                                + pausedUntil.strftime("%Y-%m-%d %H:%M") + ".")
                        output.append(now + " " + text)
                        send_telegram_messages([text])
                        return "\n".join(output)
                    except Exception as e:
                        # un errore su una ricerca salta solo quella
                        output.append(now + " ***Error on " + str(search[0]) + ": " + str(e) + "***")
    return "\n".join(output)

#-------------------------------------
#send telegram notify
def send_telegram_messages(messages):
    '''A function to send messages to telegram

    Arguments
    ---------
    messages: list
        the list of messages to send

    Example usage
    -------------
    >>> send_telegram_messages(["message1", "message2"])
    '''
    for msg in messages:
        request_url = "https://api.telegram.org/bot" + apiCredentials["token"] + "/sendMessage"
        # params= codifica il testo, altrimenti & e # nei titoli troncano il messaggio
        try:
            requests.get(request_url, params={"chat_id": apiCredentials["chatid"], "text": msg}, timeout=15)
        except requests.exceptions.RequestException as e:
            print(datetime.now().strftime("%Y-%m-%d, %H:%M:%S") + " Telegram send failed: " + str(e))



# ==========================
# COMMAND FUNCTION
# ==========================

def handle_list():
    """
    Placeholder for future /list logic
    """
    #return "List function executed (placeholder)"
    return print_queries()

def handle_shortlist():
    """
    Placeholder for future /shortlist logic
    """
    #return "List function executed (placeholder)"
    return print_sitrep()

def handle_add(args: list):
    """
    Placeholder for future /add logic
    """

    if len(args) == 1: # solo url
        args.append("noname")
    if len(args) == 2: # url e nome
        args.append("null") 
    if len(args) == 3: #url e nome e miprice
        args.append("null")

    #return f"Add function executed with args: {args}"
    return add(args[0], #url
              args[1] if args[1] != "" else "noname", #name
              args[2] if args[2] != "" else "null", #minprice
              args[3] if args[3] != "" else "null") #maxprice

def handle_delete(args: list):
    """
    Placeholder for future /delete logic
    """
    #return f"Delete function executed with args: {args}"
    return delete(args[0])





# ==========================
# TELEGRAM HANDLERS
# ==========================

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = handle_list()
    print(f"[FROM TELEGRAM] text={result}")
    await update.message.reply_text(result)

async def cmd_shortlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = handle_shortlist()
    print(f"[FROM TELEGRAM] text={result}")
    await update.message.reply_text(result)

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args  # words after /add
    result = handle_add(args)
    print(f"[FROM TELEGRAM] text={result}")
    await update.message.reply_text(result)

async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args  # words after /delete
    result = handle_delete(args)
    print(f"[FROM TELEGRAM] text={result}")
    await update.message.reply_text(result)



async def receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    print(f"[FROM TELEGRAM] chat={chat_id} text={text}")
    await update.message.reply_text(text)



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


# ==========================
# BACKGROUND TASK
# ==========================
def background_task():
    '''Ciclo in background che elabora i dati periodicamente.'''

    while daemonrunning:
        if pausedUntil and pausedUntil > datetime.now():
            t.sleep((pausedUntil - datetime.now()).total_seconds())
            continue
        # un'eccezione qui ucciderebbe il thread per sempre (il bot risponderebbe ma non cercherebbe piu')
        try:
            print(refresh(True))
        except Exception as e:
            print(datetime.now().strftime("%Y-%m-%d, %H:%M:%S") + " Polling cycle failed: " + str(e))
        print(str(pollDelay) + " seconds to next poll.")
        t.sleep(pollDelay)


# ==========================
# MAIN BOT THREAD
# ==========================

def main():

    app = ApplicationBuilder().token(apiCredentials["token"]).post_init(post_init).build()

    # Commands
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("shortlist", cmd_shortlist))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("delete", cmd_delete))

    # Normal messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_message))


    print("Bot running...")
    print("Commands: /list | /shortlist | /add <args> | /delete <args>")
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

    main()
