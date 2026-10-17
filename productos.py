"""
Radar de productos GRATIS (Amazon.es) con enlaces para validar en Meta, TikTok,
Instagram y Google Trends.

Tres formas de usarlo:
  1) Cada lunes: informe automático con los nichos por defecto.
  2) A mano desde GitHub Actions: "Run workflow" escribiendo un nicho.
  3) Desde Telegram: escribes al bot un nicho (ej. "perros" o "/nicho masajeador cervical")
     y el bot te responde en unos minutos.
"""
import html
import os
import random
import re
import sys
import time
import unicodedata
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

CRON_SEMANAL = "0 8 * * 1"  # tiene que coincidir con el cron semanal de radar.yml
NICHOS_POR_DEFECTO = ["hogar", "belleza", "mascotas", "gadgets"]
TOP = 5  # productos por lista

# Nichos que coinciden con una categoría de Amazon (ranking oficial).
# Cualquier otro texto se busca directamente en Amazon.
_CATS = {
    ("hogar", "cocina", "hogar y cocina", "casa"): ("🏠 Hogar y cocina", "kitchen"),
    ("belleza", "cosmetica", "maquillaje"): ("💄 Belleza", "beauty"),
    ("mascotas", "mascota", "perros", "gatos"): ("🐶 Mascotas", "pet-supplies"),
    ("gadgets", "electronica", "tecnologia"): ("🔌 Electrónica y gadgets", "electronics"),
    ("deporte", "deportes", "fitness"): ("🏋️ Deportes", "sports"),
    ("juguetes", "juegos"): ("🧸 Juguetes", "toys"),
    ("bebe", "bebes"): ("🍼 Bebé", "baby"),
    ("jardin", "exterior"): ("🌿 Jardín", "lawn-garden"),
    ("bricolaje", "herramientas"): ("🔧 Bricolaje", "diy"),
    ("salud", "bienestar"): ("💊 Salud y bienestar", "hpc"),
    ("oficina", "papeleria"): ("📎 Oficina", "office-products"),
}
CATEGORIAS = {alias: v for claves, v in _CATS.items() for alias in claves}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
ASIN_RE = re.compile(r"/dp/([A-Z0-9]{10})")
STOPWORDS = {"de", "del", "la", "el", "los", "las", "y", "con", "sin", "para", "en",
             "un", "una", "por", "a", "al", "o", "e", "x"}


# ───────────────────────── utilidades ─────────────────────────
def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in t if not unicodedata.combining(c)).lower().strip()


def pausa() -> None:
    time.sleep(random.uniform(2, 4))  # para no parecer un robot


def descargar(url: str) -> BeautifulSoup | None:
    for intento in range(2):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException:
            r = None
        if r is not None and r.status_code == 200 and "validateCaptcha" not in r.text:
            print(f"OK {r.status_code}: {url}")
            return BeautifulSoup(r.text, "html.parser")
        motivo = "sin respuesta" if r is None else (
            "captcha" if r.status_code == 200 else f"HTTP {r.status_code}")
        print(f"FALLO ({motivo}): {url}")
        time.sleep(3)
    return None


# ───────────────────────── lectura de Amazon ─────────────────────────
def extraer_ranking(soup: BeautifulSoup, movers: bool) -> list[dict]:
    """Páginas 'Más vendidos' y 'Los que más suben'."""
    productos, vistos = [], set()
    items = soup.select("div#gridItemRoot") or soup.select("div.zg-grid-general-faceout")
    for it in items:
        a = it.select_one('a[href*="/dp/"]')
        m = ASIN_RE.search(a.get("href", "")) if a else None
        if not m or m.group(1) in vistos:
            continue
        asin = m.group(1)
        t = it.select_one('[class*="line-clamp"]')
        titulo = t.get_text(" ", strip=True) if t else ""
        if not titulo:
            img = it.select_one("img")
            titulo = (img.get("alt") or "").strip() if img else ""
        if not titulo:
            continue
        precio_el = it.select_one('[class*="price"]')
        cambio_txt, cambio_num = "", 0
        if movers:  # el % de subida solo existe en 'Los que más suben'
            c = it.select_one('[class*="percent-change"]')
            cambio_txt = c.get_text(strip=True) if c else ""
            digitos = re.sub(r"[^\d]", "", cambio_txt)
            cambio_num = int(digitos) if digitos else 0
        vistos.add(asin)
        productos.append({
            "asin": asin, "titulo": titulo,
            "precio": precio_el.get_text(strip=True) if precio_el else "",
            "cambio": cambio_txt, "cambio_num": cambio_num,
            "url": f"https://www.amazon.es/dp/{asin}",
        })
    return productos


def extraer_busqueda(soup: BeautifulSoup) -> list[dict]:
    """Resultados de búsqueda de Amazon (sin anuncios patrocinados)."""
    productos, vistos = [], set()
    for it in soup.select('div[data-component-type="s-search-result"]'):
        asin = (it.get("data-asin") or "").strip()
        if not asin or asin in vistos:
            continue
        if it.select_one('.puis-sponsored-label-text, [class*="sponsored-label"]'):
            continue
        h2 = it.select_one("h2")
        titulo = h2.get_text(" ", strip=True) if h2 else ""
        if not titulo:
            continue
        precio_el = it.select_one("span.a-price span.a-offscreen")
        vistos.add(asin)
        productos.append({
            "asin": asin, "titulo": titulo,
            "precio": precio_el.get_text(strip=True) if precio_el else "",
            "cambio": "", "cambio_num": 0,
            "url": f"https://www.amazon.es/dp/{asin}",
        })
    return productos


def recoger(nicho: str) -> tuple[str, list[tuple[str, list[dict]]]]:
    """Devuelve (título del nicho, [(nombre de lista, productos)])."""
    cat = CATEGORIAS.get(normalizar(nicho))
    secciones = []
    if cat:
        nombre, slug = cat
        for etiqueta, ruta, movers in (
            ("🚀 Los que más suben", "movers-and-shakers", True),
            ("🆕 Novedades que despegan", "new-releases", False),
            ("🏅 Más vendidos", "bestsellers", False),
        ):
            soup = descargar(f"https://www.amazon.es/gp/{ruta}/{slug}")
            pausa()
            prods = extraer_ranking(soup, movers) if soup else []
            print(f"{etiqueta} «{nicho}»: {len(prods)} productos leídos")
            if movers:
                prods.sort(key=lambda p: p["cambio_num"], reverse=True)
            if prods:
                secciones.append((etiqueta, prods[:TOP]))
    else:
        nombre = f"🔎 {nicho.strip().capitalize()}"
    if not secciones:  # nicho libre, o la categoría falló: buscar en Amazon
        url = f"https://www.amazon.es/s?k={quote_plus(nicho)}&s=exact-aware-popularity-rank"
        soup = descargar(url)
        pausa()
        prods = extraer_busqueda(soup) if soup else []
        print(f"Búsqueda «{nicho}»: {len(prods)} productos leídos")
        if prods:
            secciones.append(("🏅 Más populares en Amazon", prods[:TOP]))
    return nombre, secciones


# ───────────────────────── enlaces de validación ─────────────────────────
def termino(titulo: str, palabras: int = 3) -> str:
    """Saca un término de búsqueda limpio del título de Amazon."""
    t = re.sub(r"(?i)\bby amazon\b|\bamazon basics\b", " ", titulo)
    t = re.split(r"\s[-–|]\s|[,(|]", t)[0]
    util = []
    for w in re.sub(r"[^\w\s]", " ", t).split():
        if any(ch.isdigit() for ch in w) or len(w) < 2 or w.lower() in STOPWORDS:
            continue
        util.append(w)
    return " ".join(util[:palabras]) or " ".join(titulo.split()[:3])


def enlaces(titulo: str) -> str:
    q = quote_plus(termino(titulo))
    meta = ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
            f"&country=ES&q={q}&search_type=keyword_unordered")
    tiktok = f"https://www.tiktok.com/search/video?q={q}"
    # Instagram no tiene búsqueda de vídeos por palabra: se usa Google sobre sus reels
    insta = "https://www.google.com/search?q=" + quote_plus(f"site:instagram.com/reel {termino(titulo)}")
    trends = f"https://trends.google.es/trends/explore?geo=ES&q={q}"
    e = lambda u: html.escape(u, quote=True)
    return (f'📢 <a href="{e(meta)}">Meta</a>  ·  🎵 <a href="{e(tiktok)}">TikTok</a>  ·  '
            f'📸 <a href="{e(insta)}">Instagram</a>  ·  📊 <a href="{e(trends)}">Trends</a>')


# ───────────────────────── formato del mensaje ─────────────────────────
def formatear(nombre: str, secciones: list[tuple[str, list[dict]]]) -> str:
    out = [f"<b>{html.escape(nombre)}</b>", "<i>Amazon.es · valida cada uno con los enlaces</i>"]
    for etiqueta, prods in secciones:
        out += ["", f"<b>{etiqueta}</b>", "━━━━━━━━━━━━"]
        for i, p in enumerate(prods, 1):
            titulo = html.escape(p["titulo"][:60] + ("…" if len(p["titulo"]) > 60 else ""))
            datos = []
            if p["precio"]:
                datos.append(f"💶 {html.escape(p['precio'])}")
            if p["cambio"]:
                datos.append(f"📈 {html.escape(p['cambio'])}")
            out.append(f'<b>{i}.</b> <a href="{p["url"]}">{titulo}</a>')
            if datos:
                out.append("    " + "  ·  ".join(datos))
            out.append("    " + enlaces(p["titulo"]))
            out.append("")
    return "\n".join(out).strip()


# ───────────────────────── Telegram ─────────────────────────
def _api(metodo: str) -> str:
    return f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/{metodo}"


def trocear(texto: str, limite: int = 3800) -> list[str]:
    partes, actual = [], ""
    for linea in texto.split("\n"):
        if len(actual) + len(linea) + 1 > limite:
            partes.append(actual)
            actual = ""
        actual += linea + "\n"
    if actual.strip():
        partes.append(actual)
    return partes


def enviar(texto: str, html_mode: bool = True) -> None:
    for parte in trocear(texto):
        datos = {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": parte,
                 "disable_web_page_preview": True}
        if html_mode:
            datos["parse_mode"] = "HTML"
        requests.post(_api("sendMessage"), json=datos, timeout=30).raise_for_status()


AYUDA = ("👋 Escríbeme un nicho o producto y te mando lo que más vende en Amazon.es.\n"
         "Ejemplos: perros · masajeador cervical · hogar, belleza\n"
         "(Respondo en unos minutos.)")


def leer_nichos_telegram() -> list[str]:
    """Lee los mensajes nuevos del chat y devuelve los nichos pedidos."""
    r = requests.get(_api("getUpdates"), params={"timeout": 0}, timeout=30)
    r.raise_for_status()
    novedades = r.json().get("result", [])
    if not novedades:
        return []
    # Confirmar los mensajes para no procesarlos dos veces
    ultimo = max(u["update_id"] for u in novedades)
    requests.get(_api("getUpdates"), params={"offset": ultimo + 1, "timeout": 0}, timeout=30)

    chat = str(os.environ["TELEGRAM_CHAT_ID"])
    nichos = []
    for u in novedades:
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != chat:
            continue
        texto = (m.get("text") or "").strip()
        if not texto:
            continue
        if re.match(r"(?i)^/(start|ayuda|help)\b", texto):
            enviar(AYUDA, html_mode=False)
            continue
        texto = re.sub(r"(?i)^/(nicho|radar)(@\w+)?\s*", "", texto)
        if texto.startswith("/"):
            continue
        for parte in texto.split(","):
            parte = parte.strip()
            if parte and len(parte) <= 60 and parte not in nichos:
                nichos.append(parte)
    return nichos[:5]


# ───────────────────────── programa principal ─────────────────────────
def procesar(nicho: str) -> bool:
    nombre, secciones = recoger(nicho)
    if not secciones:
        # Amazon no dejó leer productos: se mandan igualmente los enlaces del propio nicho
        enviar(
            f"⚠️ No pude leer productos de Amazon para «{html.escape(nicho)}» "
            "(Amazon suele bloquear las visitas desde GitHub).\n\n"
            "Aun así, aquí tienes la búsqueda directa de ese nicho:\n"
            f"{enlaces(nicho)}"
        )
        return False
    enviar(formatear(nombre, secciones))
    return True


def main() -> int:
    evento = os.environ.get("EVENTO", "").strip()  # cron que ha disparado la ejecución
    manual = os.environ.get("NICHO", "").strip()

    if evento and evento != CRON_SEMANAL:  # ejecución de "escucha" de Telegram
        nichos = leer_nichos_telegram()
        if not nichos:
            print("Sin mensajes nuevos")
            return 0
        enviar("🔎 Buscando: " + ", ".join(nichos) + " …", html_mode=False)
    elif manual:
        nichos = [n.strip() for n in manual.split(",") if n.strip()]
    else:
        nichos = NICHOS_POR_DEFECTO

    fallos = 0
    for n in nichos:
        if not procesar(n):
            fallos += 1
    return 1 if fallos == len(nichos) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
