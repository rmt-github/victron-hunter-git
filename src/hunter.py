"""
Price Hunter - Monitor OLX Portugal + Wallapop para bons negócios
Nichos: Victron, instrumentação, LiFePO4, Hi-Fi vintage, Arduino/RPi
"""

import os
import json
import time
import logging
import hashlib
import requests
import tls_client
from bs4 import BeautifulSoup

from playwright.sync_api import sync_playwright

# ── Configuração ──────────────────────────────────────────────────────────────

NTFY_CHANNEL      = os.environ.get("NTFY_CHANNEL", "victron-hunter")   
NTFY_SERVER       = os.environ.get("NTFY_SERVER", "https://ntfy.sh")  
CHECK_INTERVAL    = int(os.environ.get("CHECK_INTERVAL", "900"))   
MIN_MARGIN        = float(os.environ.get("MIN_MARGIN", "30"))       
SEEN_FILE         = "data/seen_ads.json"

WALLAPOP_LAT      = float(os.environ.get("WALLAPOP_LAT",  "39.5"))
WALLAPOP_LNG      = float(os.environ.get("WALLAPOP_LNG", "-8.0"))
WALLAPOP_DIST_KM  = int(os.environ.get("WALLAPOP_DIST_KM", "400"))  

# ── Nichos e palavras-chave ───────────────────────────────────────────────────

NICHOS = [
    {
        "nome": "Victron Energy",
        "emoji": "⚡",
        "termos": [
            {"query": "multiplus 12 500 ", "preco_mercado": 275},
            {"query": "multiplus 12 800 ", "preco_mercado": 350},
            {"query": "multiplus 12 1200 ", "preco_mercado": 350},
            {"query": "multiplus 12 1600 ", "preco_mercado": 350},
            {"query": "multiplus 12 2000 ", "preco_mercado": 350},
            {"query": "multiplus 12 3000 ", "preco_mercado": 550},
            {"query": "victron mppt 75 10 ",  "preco_mercado": 30},
            {"query": "victron mppt 75 15 ",  "preco_mercado": 30},
            {"query": "victron mppt 100 15 ",  "preco_mercado": 75},
            {"query": "victron mppt 100 20 ",  "preco_mercado": 60},
            {"query": "victron mppt 100 30 ",  "preco_mercado": 70},
            {"query": "victron mppt 100 50 ",  "preco_mercado": 75},
            {"query": "victron mppt 150 35 ",  "preco_mercado": 140},
            {"query": "victron mppt 150 45 ",  "preco_mercado": 200},
            {"query": "victron mppt 150 60 ",  "preco_mercado": 230},
            {"query": "victron mppt 150 70 ",  "preco_mercado": 260},
            {"query": "victron mppt 150 85 ",  "preco_mercado": 360},
            {"query": "victron mppt 150 100 ",  "preco_mercado": 285},
            {"query": "victron mppt 250 60 ",  "preco_mercado": 350},
            {"query": "victron mppt 250 70 ",  "preco_mercado": 425},
            {"query": "victron mppt 250 85 ",  "preco_mercado": 345},
            {"query": "victron mppt 250 100 ",  "preco_mercado": 400},
            {"query": "victron dc dc 9",            "preco_mercado": 70},
            {"query": "victron dc dc 18",            "preco_mercado": 100},
            {"query": "victron dc dc 30",            "preco_mercado": 150},
            {"query": "victron dc dc 50",            "preco_mercado": 250},
            {"query": "victron cerbo gx",  "preco_mercado": 150},
            {"query": "victron gx touch 50",  "preco_mercado": 165},
            {"query": "victron gx touch 70",  "preco_mercado": 250},
            {"query": "victron mk3",  "preco_mercado": 75},
        ],
    },
]

# Inicializa o cliente TLS que simula perfeitamente um browser Chrome estável
wallapop_session = tls_client.Session(
    client_identifier="chrome_120",
    random_tls_extension_order=True
)

olx_session = requests.Session()

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Persistência ──────────────────────────────────────────────────────────────

def load_seen():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()

def save_seen(seen: set):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

# ── Utilitários ───────────────────────────────────────────────────────────────

def parse_price(text: str):
    import re
    text = str(text).replace(".", "").replace(",", ".")
    nums = re.findall(r"\d+\.?\d*", text)
    if nums:
        val = float(nums[0])
        if 1 < val < 50000:
            return val
    return None

def make_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]

def calc_margin(preco_anuncio: float, preco_mercado: float) -> float:
    if preco_anuncio <= 0:
        return 0
    return ((preco_mercado - preco_anuncio) / preco_anuncio) * 100

# ── Scraper OLX Portugal ──────────────────────────────────────────────────────

OLX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-PT,pt;q=0.9",
}

def search_olx(query: str) -> list:
    url = f"https://www.olx.pt/ads/q-{query.replace(' ', '-')}/"
    ads = []
    try:
        r = olx_session.get(url, headers=OLX_HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for card in soup.select("[data-cy='l-card']")[:20]:
            try:
                title_el = card.select_one("h6, [data-testid='ad-title']")
                title    = title_el.get_text(strip=True) if title_el else ""
                price_el = card.select_one("[data-testid='ad-price'], .price")
                price    = parse_price(price_el.get_text(strip=True)) if price_el else None
                link_el  = card.select_one("a[href]")
                link     = link_el["href"] if link_el else ""
                if link and not link.startswith("http"):
                    link = "https://www.olx.pt" + link
                loc_el   = card.select_one("[data-testid='location-date']")
                location = loc_el.get_text(strip=True) if loc_el else ""
                if title and price and link:
                    ads.append({
                        "id": make_id(link),
                        "title": title,
                        "price": price,
                        "location": location,
                        "link": link,
                        "fonte": "OLX",
                    })
            except Exception:
                continue
    except Exception as e:
        log.warning(f"[OLX] Erro em '{query}': {e}")
    return ads

# ── Scraper Wallapop (Anti-Bloqueio via TLS Client) ───────────────────────────

WALLAPOP_API = "https://api.wallapop.com/api/v3/general/search"

WALLAPOP_HEADERS = {
    "User-Agent": "Wallapop/V10.23.0 (Android 13; Mobile)",
    "Accept": "application/json",
    "Accept-Language": "pt-PT",
    "X-App-Version": "10.23.0",
    "X-Device-OS": "Android",
    "Connection": "keep-alive"
}

# ── Scraper Wallapop (Via Navegador Virtual Playwright) ───────────────────────

def search_wallapop(query: str) -> list:
    url = f"https://pt.wallapop.com/app/search?keywords={query.replace(' ', '%20')}&order_by=newest"
    ads = []
    
    log.info(f"[Wallapop] A abrir navegador virtual para: '{query}'")
    
    try:
        # Inicia o motor do Playwright
        with sync_playwright() as p:
            # Lança o Chrome em segundo plano (headless=True). 
            # Se quiseres ver o navegador a abrir fisicamente para testar, muda para False.
            browser = p.chromium.launch(headless=True)
            
            # Cria um contexto simulando um ecrã e idioma padrão de Portugal
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                locale="pt-PT"
            )
            
            page = context.new_page()
            
            # Navega até ao Wallapop e espera que a rede estabilize
            page.goto(url, wait_until="networkidle", timeout=30000)
            
            # Aguarda 3 segundos extra para garantir que os scripts de segurança carregam e passam
            page.wait_for_timeout(3000)
            
            # Extrai o conteúdo HTML completo após a página estar totalmente processada
            html_content = page.content()
            browser.close()
        
        # Agora que temos o HTML real, usamos a BeautifulSoup idêntica ao OLX
        soup = BeautifulSoup(html_content, "html.parser")
        cards = soup.select("a.ItemCard, [class*='ItemCard'], a[href*='/item/']")
        
        for card in cards[:20]:
            try:
                link = card.get("href", "")
                if not link:
                    continue
                if not link.startswith("http"):
                    link = "https://pt.wallapop.com" + link

                # Localiza o preço
                price_el = card.select_one(".ItemCard__price, [class*='price'], [class*='Price']")
                price = parse_price(price_el.get_text(strip=True)) if price_el else None
                
                # Localiza o título
                title_el = card.select_one(".ItemCard__title, [class*='title'], [class*='Title']")
                title = title_el.get_text(strip=True) if title_el else ""
                
                if not title and card.get("title"):
                    title = card.get("title")

                # Fallback caso as classes tenham mudado ligeiramente
                if not price or not title:
                    text_content = card.get_text(" ", strip=True)
                    if not price:
                        price = parse_price(text_content)
                    if not title:
                        title = text_content[:50]

                if title and price and link:
                    ads.append({
                        "id":       make_id(link),
                        "title":    title[:60],
                        "price":    price,
                        "location": "Wallapop",
                        "link":     link,
                        "fonte":    "Wallapop",
                    })
            except Exception:
                continue

    except Exception as e:
        log.error(f"[Wallapop] Erro crítico no navegador virtual: {e}")

    log.info(f"[Wallapop] Encontrados {len(ads)} anúncios para '{query}'")
    return ads
    
# ── Ntfy ─────────────────────────────────────────────────────────────────────

import unicodedata
import os
import requests

def send_ntfy(title: str, message: str, link: str, priority: int = 3):
    # 1. Garante que o canal está configurado (ajusta para o teu se necessário)
    channel = os.getenv("NTFY_CHANNEL", "victron-hunter")
    url = f"https://ntfy.sh/{channel}"
    
    # 2. LIMPEZA ANTI-ERRO 400: Remove emojis e caracteres não-ASCII do Título
    title_clean = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode('ascii')
    title_clean = " ".join(title_clean.split()) # Remove espaços duplos ou quebras de linha
    
    # 3. LIMPEZA DA MENSAGEM: Garante que o corpo do texto vai em UTF-8 limpo e sem quebras corrompidas
    message_clean = unicodedata.normalize('NFKD', message).encode('ascii', 'ignore').decode('ascii')
    
    # Adiciona o link diretamente no fim do corpo da mensagem para o ntfy mapear o clique
    corpo_alerta = f"{message_clean}\nLink: {link}"
    
    # 4. Mapeia a tua prioridade numérica (1 a 5) para os termos do Ntfy
    prioridades = {1: "min", 2: "low", 3: "default", 4: "high", 5: "max"}
    priority_str = prioridades.get(priority, "default")
    
    headers = {
        "Title": f"Negocio: {title_clean[:50]}...", # Título seguro e curto
        "Priority": priority_str,
        "Tags": "money_with_wings,loudspeaker"
    }
    
    try:
        # Envia os dados forçando codificação estável
        r = requests.post(
            url, 
            data=corpo_alerta.encode('utf-8'), 
            headers=headers, 
            timeout=10
        )
        r.raise_for_status()
        # Usamos log se o teu script tiver, ou um print simples:
        print(f"[Ntfy] Alerta enviado com sucesso para: {channel}")
    except Exception as e:
        print(f"[Ntfy] Erro ao enviar notificacao: {e}")
        
def format_alert(ad: dict, nicho: dict, margin: float) -> tuple[str, str, int]:
    fonte    = ad.get("fonte", "")
    badge    = "Wallapop" if fonte == "Wallapop" else "OLX"
    emoji    = nicho["emoji"]

    if margin >= 80:
        stars, priority = "🔥", 5   
    elif margin >= 50:
        stars, priority = "✅", 4   
    else:
        stars, priority = "👀", 3   

    title = f"{stars} {emoji} {ad['title'][:50]}"
    body  = (
        f"{nicho['nome']} · {badge}\n"
        f"Preço: {ad['price']:.0f} € · Margem: +{margin:.0f}%\n"
        f"📍 {ad['location']}"
    )
    return title, body, priority

# ── Loop principal ────────────────────────────────────────────────────────────

def run_cycle(seen: set) -> set:
    novos = 0
    alertas = 0

    for nicho in NICHOS:
        for termo in nicho["termos"]:
            query        = termo["query"]
            preco_mercado = termo["preco_mercado"]

            # ---- OLX ----
            log.info(f"[OLX] Pesquisando '{query}'...")
            for ad in search_olx(query):
                if ad["id"] not in seen:
                    seen.add(ad["id"])
                    novos += 1
                    margin = calc_margin(ad["price"], preco_mercado)
                    if margin >= MIN_MARGIN:
                        alertas += 1
                        title, body, prio = format_alert(ad, nicho, margin)
                        send_ntfy(title, body, ad["link"], prio)
                        log.info(f"  ALERTA OLX: {ad['title'][:45]} | {ad['price']:.0f}€ | +{margin:.0f}%")
                        time.sleep(1)
            time.sleep(3)   

            # ---- Wallapop ----
            log.info(f"[Wallapop] Pesquisando '{query}'...")
            for ad in search_wallapop(query):
                if ad["id"] not in seen:
                    seen.add(ad["id"])
                    novos += 1
                    margin = calc_margin(ad["price"], preco_mercado)
                    if margin >= MIN_MARGIN:
                        alertas += 1
                        title, body, prio = format_alert(ad, nicho, margin)
                        send_ntfy(title, body, ad["link"], prio)
                        log.info(f"  ALERTA Wallapop: {ad['title'][:40]} | {ad['price']:.0f}€ | +{margin:.0f}%")
                        time.sleep(1)
            time.sleep(4)   

    log.info(f"Ciclo completo — {novos} anúncios novos, {alertas} alertas enviados")
    save_seen(seen)
    return seen

def main():
    log.info("🚀 Price Hunter iniciado (OLX + Wallapop com TLS-Client)")
    log.info(f"Nichos: {', '.join(n['nome'] for n in NICHOS)}")
    log.info(f"Margem mínima: {MIN_MARGIN}%  |  Intervalo: {CHECK_INTERVAL}s")

    if not NTFY_CHANNEL:
        log.warning("⚠️  NTFY_CHANNEL não definido — alertas só na consola")

    seen = load_seen()
    while True:
        try:
            seen = run_cycle(seen)
        except KeyboardInterrupt:
            log.info("Interrompido pelo utilizador.")
            break
        except Exception as e:
            log.error(f"Erro inesperado no ciclo: {e}")
        log.info(f"Próxima pesquisa em {CHECK_INTERVAL // 60} minutos...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
