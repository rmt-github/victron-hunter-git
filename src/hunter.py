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
from bs4 import BeautifulSoup

# ── Configuração ──────────────────────────────────────────────────────────────

NTFY_CHANNEL      = os.environ.get("NTFY_CHANNEL", "victron-hunter")
NTFY_SERVER       = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
CHECK_INTERVAL    = int(os.environ.get("CHECK_INTERVAL", "900"))
MIN_MARGIN        = float(os.environ.get("MIN_MARGIN", "30"))
SEEN_FILE         = "data/seen_ads.json"

WALLAPOP_LAT      = float(os.environ.get("WALLAPOP_LAT",  "39.5"))
WALLAPOP_LNG      = float(os.environ.get("WALLAPOP_LNG", "-8.0"))
WALLAPOP_DIST_KM  = int(os.environ.get("WALLAPOP_DIST_KM", "1000"))

# ── Nichos e palavras-chave ───────────────────────────────────────────────────

NICHOS = [
    {
        "nome": "Victron Energy",
        "emoji": "⚡",
        "termos": [
            {"query": "victron multiplus 12 1600 70",   "preco_mercado": 400},
            {"query": "victron multiplus 12 2000 80",   "preco_mercado": 500},
            {"query": "victron multiplus 12 3000 120",  "preco_mercado": 800},
            {"query": "victron dc dc 12 12 18",         "preco_mercado": 80},
            {"query": "victron dc dc 12 12 30",         "preco_mercado": 100},
            {"query": "victron dc dc XS 12 50",         "preco_mercado": 200},
            {"query": "victron mppt 75 15",             "preco_mercado": 45},
            {"query": "victron mppt 100 20",            "preco_mercado": 70},
            {"query": "victron mppt 100 30",            "preco_mercado": 90},
            {"query": "victron mppt 100 50",            "preco_mercado": 100},
            {"query": "victron mppt 150 35",            "preco_mercado": 100},
            {"query": "victron GX touch 50",            "preco_mercado": 170},
            {"query": "victron GX touch 70",            "preco_mercado": 250},
            {"query": "victron Cerbo GX",               "preco_mercado": 170},
            {"query": "victron BT 12 220",              "preco_mercado": 70},
            {"query": "victron monitor de bateria",     "preco_mercado": 70},
        ],
    },
]

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
        r = requests.get(url, headers=OLX_HEADERS, timeout=15)
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

# ── Scraper Wallapop ──────────────────────────────────────────────────────────
#
# CORREÇÕES aplicadas em relação à versão anterior:
#
# 1. ENDPOINT: mudado de /api/v3/search  →  /api/v3/general/search
#    O endpoint antigo foi descontinuado e responde sempre 400.
#
# 2. PARÂMETRO "source": substituído "filters_source" por "source"
#    O nome do parâmetro mudou na API. Valor: "search_box".
#
# 3. PARÂMETRO "order_by": valor "newest" → "newest" continua válido,
#    mas foi adicionado fallback para "most_relevance" em caso de erro.
#
# 4. HEADERS: removidos X-AppVersion, X-DeviceOS, X-PlatformType
#    Estes headers não-standard podem accionar rejeição 400/403 em
#    alguns edge nodes do Wallapop. A API funciona sem eles.
#
# 5. SESSION com cookies: o Wallapop exige agora que o browser/cliente
#    tenha feito pelo menos um GET à homepage antes de usar a API,
#    de modo a receber e reenviar os cookies de sessão necessários.
#    Usamos requests.Session() para isso automaticamente.
#
# 6. ESTRUTURA DA RESPOSTA: a chave raiz mudou de "search_objects"
#    para "data" → "section" → "payload" → "items" na v3/general.
#    O código agora tenta ambas as estruturas (nova e antiga) por
#    compatibilidade, e loga um aviso se nenhuma funcionar.

WALLAPOP_API = "https://api.wallapop.com/api/v3/general/search"

WALLAPOP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "pt-PT,pt;q=0.9,es;q=0.8",
    "Origin":          "https://pt.wallapop.com",
    "Referer":         "https://pt.wallapop.com/",
}

# Session partilhada — inicializada em _init_wallapop_session()
_wallapop_session: requests.Session | None = None

def _init_wallapop_session() -> requests.Session:
    """
    Cria uma Session e faz um GET à homepage do Wallapop PT para
    receber os cookies de sessão. Sem estes cookies a API responde 400.
    """
    global _wallapop_session
    if _wallapop_session is not None:
        return _wallapop_session

    s = requests.Session()
    s.headers.update(WALLAPOP_HEADERS)
    try:
        log.info("[Wallapop] A inicializar sessão (GET homepage)...")
        s.get("https://pt.wallapop.com/", timeout=15)
        log.info("[Wallapop] Sessão inicializada.")
    except Exception as e:
        log.warning(f"[Wallapop] Não foi possível inicializar sessão: {e}")
    _wallapop_session = s
    return s


def _extract_items(data: dict) -> list:
    """
    Tenta extrair a lista de anúncios da resposta JSON do Wallapop.
    A estrutura mudou ao longo do tempo — suporta ambas as versões.
    """
    # Estrutura nova (v3/general/search):
    # { "data": { "section": { "payload": { "items": [...] } } } }
    try:
        items = data["data"]["section"]["payload"]["items"]
        if isinstance(items, list):
            return items
    except (KeyError, TypeError):
        pass

    # Estrutura alternativa nova:
    # { "data": { "items": [...] } }
    try:
        items = data["data"]["items"]
        if isinstance(items, list):
            return items
    except (KeyError, TypeError):
        pass

    # Estrutura legada (v3/search — ainda pode aparecer em caches):
    # { "search_objects": [...] }
    try:
        items = data["search_objects"]
        if isinstance(items, list):
            return items
    except (KeyError, TypeError):
        pass

    return []


def search_wallapop(query: str) -> list:
    """
    Pesquisa o Wallapop via API JSON (endpoint /api/v3/general/search).
    Devolve lista de dicts com os mesmos campos que search_olx().
    """
    session = _init_wallapop_session()

    params = {
        "keywords":       query,
        "source":         "search_box",   # ← era "filters_source" (campo renomeado)
        "order_by":       "newest",
        "latitude":       WALLAPOP_LAT,
        "longitude":      WALLAPOP_LNG,
        "distance_in_km": WALLAPOP_DIST_KM,
    }

    ads = []
    try:
        r = session.get(
            WALLAPOP_API,
            params=params,
            timeout=15,
        )

        # Log do código HTTP para diagnóstico
        if r.status_code != 200:
            log.warning(
                f"[Wallapop] HTTP {r.status_code} em '{query}' "
                f"— resposta: {r.text[:200]}"
            )

        if r.status_code == 429:
            log.warning("[Wallapop] Rate limit (429) — a aguardar 60s...")
            time.sleep(60)
            return ads

        if r.status_code == 400:
            # 400 depois da correção de endpoint/parâmetros pode indicar
            # que o Wallapop bloqueou temporariamente o IP ou exige CAPTCHA.
            # Reinicializa a sessão na próxima chamada.
            global _wallapop_session
            _wallapop_session = None
            log.warning("[Wallapop] 400 — sessão reiniciada para próxima tentativa.")
            return ads

        r.raise_for_status()
        data = r.json()

        items = _extract_items(data)
        if not items:
            log.warning(
                f"[Wallapop] Resposta sem anúncios para '{query}'. "
                f"Chaves raiz: {list(data.keys())}"
            )

        for item in items[:25]:
            try:
                item_id   = str(item.get("id", ""))
                title     = item.get("title", "")
                price_raw = item.get("price", 0)
                price     = parse_price(price_raw)
                web_slug  = item.get("web_slug", "")
                link      = f"https://pt.wallapop.com/item/{web_slug}" if web_slug else ""
                city      = (item.get("location") or {}).get("city", "")

                if title and price and link:
                    ads.append({
                        "id":       make_id(link) if link else make_id(item_id),
                        "title":    title,
                        "price":    price,
                        "location": city,
                        "link":     link,
                        "fonte":    "Wallapop",
                    })
            except Exception:
                continue

    except requests.exceptions.HTTPError as e:
        log.warning(f"[Wallapop] HTTPError em '{query}': {e}")
    except Exception as e:
        log.warning(f"[Wallapop] Erro em '{query}': {e}")

    return ads

# ── Ntfy ──────────────────────────────────────────────────────────────────────

def send_ntfy(title: str, message: str, link: str, priority: int = 3):
    if not NTFY_CHANNEL:
        log.warning("NTFY_CHANNEL não configurado — alerta na consola:")
        print(f"[{title}] {message}")
        return
    url = f"{NTFY_SERVER}/{NTFY_CHANNEL}"
    try:
        r = requests.post(
            url,
            data=message.encode("utf-8"),
            headers={
                "Title":    title.encode("utf-8"),
                "Priority": str(priority),
                "Tags":     "moneybag",
                "Click":    link,
                "Actions":  f"view, Ver anúncio, {link}",
            },
            timeout=10,
        )
        r.raise_for_status()
    except Exception as e:
        log.error(f"Erro Ntfy: {e}")

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
            query         = termo["query"]
            preco_mercado = termo["preco_mercado"]

            # ---- OLX ----
            log.info(f"[OLX] '{query}'...")
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
            time.sleep(10)

            # ---- Wallapop ----
            log.info(f"[Wallapop] '{query}'...")
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
            time.sleep(12)  # Wallapop é mais sensível a rate limiting

    log.info(f"Ciclo completo — {novos} anúncios novos, {alertas} alertas enviados")
    save_seen(seen)
    return seen

def main():
    log.info("🚀 Price Hunter iniciado (OLX + Wallapop)")
    log.info(f"Nichos: {', '.join(n['nome'] for n in NICHOS)}")
    log.info(f"Margem mínima: {MIN_MARGIN}%  |  Intervalo: {CHECK_INTERVAL}s")
    log.info(f"Wallapop zona: lat={WALLAPOP_LAT} lng={WALLAPOP_LNG} raio={WALLAPOP_DIST_KM}km")

    if not NTFY_CHANNEL:
        log.warning("⚠️  NTFY_CHANNEL não definido — alertas só na consola")

    seen = load_seen()
    while True:
        try:
            seen = run_cycle(seen)
        except KeyboardInterrupt:
            log.info("Interrompido.")
            break
        except Exception as e:
            log.error(f"Erro no ciclo: {e}")
        log.info(f"Próxima pesquisa em {CHECK_INTERVAL // 60} minutos...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
