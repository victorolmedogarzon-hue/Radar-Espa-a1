"""
Radar de productos ganadores: anuncios TOP de TikTok en España.

Usa el scraper de Apify "scrapesage/tiktok-ad-library-scraper" (fuente Creative Center
Top Ads), saca la página de venta de cada anuncio, agrupa por producto y manda a Telegram
los más repetidos / con mejor CTR, con enlaces para validarlos.

Necesita los secrets: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID y APIFY_TOKEN.
"""
import html
import os
import re
import sys
import time
from urllib.parse import quote_plus, urlparse

import requests

# ───────────────────────── AJUSTES (puedes editarlos) ─────────────────────────
ACTOR = "scrapesage~tiktok-ad-library-scraper"
PAIS = "ES"
PERIODO = "30"            # días del ranking: "7", "30" o "180"
INDUSTRIAS = [            # si TikTok no reconoce un nombre, simplemente lo salta
    "E-commerce", "Beauty & Personal Care", "Household Products", "Home Improvement",
    "Health", "Pets", "Sports & Outdoor", "Tech & Electronics",
]
MAX_ANUNCIOS = 60         # tope de anuncios por ejecución (controla el gasto)
MAX_COSTE_USD = 1.0       # tope de gasto por ejecución en Apify
TOP = 10                  # productos que se mandan a Telegram

API = "https://api.apify.com/v2"
STOPWORDS = {"de", "del", "la", "el", "los", "las", "y", "con", "sin", "para", "en", "un",
             "una", "por", "a", "al", "o", "e", "the", "and", "for", "with", "your", "you"}
HOSTS_NO_PRODUCTO = ("apps.apple.com", "play.google.com", "tiktok.com", "instagram.com",
                     "facebook.com", "wa.me", "t.me")


# ───────────────────────── Apify ─────────────────────────
class ApifyError(Exception):
    pass


def _cab() -> dict:
    return {"Authorization": f"Bearer {os.environ['APIFY_TOKEN']}"}


def lanzar_scraper(con_idioma: bool = True) -> list[dict]:
    entrada = {
        "source": "creative-center",
        "countryCodes": [PAIS],
        "period": PERIODO,
        "orderBy": "ctr",
        "industries": INDUSTRIAS,
        "includeAdDetails": True,   # necesario para obtener la página de venta
        "includeVideoUrl": False,
        "maxResults": MAX_ANUNCIOS,
    }
    if con_idioma:
        entrada["adLanguages"] = ["es"]

    r = requests.post(f"{API}/acts/{ACTOR}/runs", headers=_cab(),
                      params={"maxTotalChargeUsd": MAX_COSTE_USD}, json=entrada, timeout=60)
    if r.status_code in (401, 403):
        raise ApifyError("Apify rechazó el token (revisa el secret APIFY_TOKEN).")
    if r.status_code == 402:
        raise ApifyError("Apify dice que no queda crédito en tu cuenta.")
    r.raise_for_status()
    run = r.json()["data"]
    print(f"Run de Apify lanzado: {run['id']}")

    fin, estado = time.time() + 15 * 60, "RUNNING"
    datos = run
    while time.time() < fin:
        datos = requests.get(f"{API}/actor-runs/{run['id']}", headers=_cab(), timeout=30).json()["data"]
        estado = datos["status"]
        if estado in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break
        time.sleep(10)
    print(f"Estado final del run: {estado}")

    items = requests.get(f"{API}/datasets/{datos['defaultDatasetId']}/items", headers=_cab(),
                         params={"format": "json", "clean": "true"}, timeout=60).json()
    if not isinstance(items, list):
        items = []
    if not items and estado != "SUCCEEDED":
        raise ApifyError(f"El scraper terminó con estado {estado} y sin datos.")
    # Algunos anuncios no tienen idioma etiquetado: si el filtro deja 0, repetir sin él
    if not items and con_idioma:
        print("Sin resultados con filtro de idioma; reintento sin filtro")
        return lanzar_scraper(con_idioma=False)
    return items


# ───────────────────────── tratamiento de anuncios ─────────────────────────
def limpiar_url(url: str) -> tuple[str, str]:
    """Devuelve (clave del producto, url sin parámetros de seguimiento)."""
    p = urlparse(url)
    host = p.netloc.lower().removeprefix("www.")
    ruta = p.path.rstrip("/")
    return f"{host}{ruta}", f"{p.scheme}://{p.netloc}{p.path}" if p.netloc else ""


def termino_busqueda(anuncio: dict) -> str:
    """Término para buscar el producto: slug de la URL o primeras palabras del texto."""
    url = anuncio.get("landingPageUrl") or ""
    segs = [s for s in urlparse(url).path.split("/") if s]
    slug = ""
    if "products" in segs and segs.index("products") + 1 < len(segs):
        slug = segs[segs.index("products") + 1]
    elif segs and re.fullmatch(r"[a-zA-Z\-]{6,}", segs[-1]):
        slug = segs[-1]
    fuente = slug.replace("-", " ") if slug else re.sub(r"[#@]\S+", " ", anuncio.get("adCaption") or "")
    palabras = [w for w in re.sub(r"[^\w\s]", " ", fuente).split()
                if len(w) > 2 and not any(c.isdigit() for c in w) and w.lower() not in STOPWORDS]
    return " ".join(palabras[:4])


def agrupar(items: list[dict], filtro: str = "") -> list[dict]:
    """Agrupa anuncios por producto (misma página de venta) y los ordena."""
    grupos: dict[str, list[dict]] = {}
    for a in items:
        url = a.get("landingPageUrl") or ""
        if any(h in url.lower() for h in HOSTS_NO_PRODUCTO):
            continue  # apps, redes sociales...: no son productos
        clave = limpiar_url(url)[0] if url else f"sin-url-{a.get('adId')}"
        if filtro:
            texto = " ".join(str(a.get(k) or "") for k in ("adCaption", "advertiserName", "brandName")) + " " + url
            if normalizar(filtro) not in normalizar(texto):
                continue
        grupos.setdefault(clave, []).append(a)

    productos = []
    for clave, anuncios in grupos.items():
        mejor = max(anuncios, key=lambda a: (a.get("ctr") or 0, a.get("likes") or 0))
        productos.append({"clave": clave, "anuncios": anuncios, "mejor": mejor,
                          "n": len(anuncios), "ctr": mejor.get("ctr") or 0,
                          "likes": max((a.get("likes") or 0) for a in anuncios)})
    productos.sort(key=lambda p: (p["n"], p["ctr"], p["likes"]), reverse=True)
    return productos


def normalizar(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


# ───────────────────────── mensaje ─────────────────────────
def abreviar(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "-"
    return f"{n/1000:.1f}K" if n >= 1000 else f"{n:g}"


def enlaces(term: str) -> str:
    q = quote_plus(term)
    e = lambda u: html.escape(u, quote=True)
    tiktok = f"https://www.tiktok.com/search/video?q={q}"
    insta = "https://www.google.com/search?q=" + quote_plus(f"site:instagram.com/reel {term}")
    trends = f"https://trends.google.es/trends/explore?geo=ES&q={q}"
    ali = f"https://es.aliexpress.com/wholesale?SearchText={q}"
    return (f'🎵 <a href="{e(tiktok)}">TikTok</a>  ·  📸 <a href="{e(insta)}">Instagram</a>  ·  '
            f'📊 <a href="{e(trends)}">Trends</a>  ·  🛒 <a href="{e(ali)}">AliExpress</a>')


def formatear(productos: list[dict]) -> str:
    out = ["🏆 <b>Productos ganadores · anuncios top de TikTok España</b>",
           f"<i>Ranking de los últimos {PERIODO} días · ordenados por nº de anuncios y CTR</i>"]
    for i, p in enumerate(productos[:TOP], 1):
        a = p["mejor"]
        marca = a.get("brandName") or a.get("advertiserName") or "Anunciante"
        caption = re.sub(r"\s+", " ", a.get("adCaption") or "").strip()
        caption = html.escape(caption[:110] + ("…" if len(caption) > 110 else ""))
        term = termino_busqueda(a) or marca
        url_prod = limpiar_url(a.get("landingPageUrl") or "")[1]

        out += ["", "━━━━━━━━━━━━", f"<b>{i}. {html.escape(str(marca))}</b>"]
        if a.get("industry"):
            out[-1] += f"  <i>({html.escape(str(a['industry']))})</i>"
        if caption:
            out.append(f"“{caption}”")
        datos = []
        if p["n"] > 1:
            datos.append(f"🔥 {p['n']} anuncios")
        datos.append(f"📈 CTR {a.get('ctr', '-')}")
        datos.append(f"❤️ {abreviar(p['likes'])}")
        out.append("  ·  ".join(datos))
        botones = []
        if a.get("adUrl"):
            botones.append(f'🎬 <a href="{html.escape(a["adUrl"], quote=True)}">Ver anuncio</a>')
        if url_prod:
            botones.append(f'🛍 <a href="{html.escape(url_prod, quote=True)}">Página de venta</a>')
        if botones:
            out.append("  ·  ".join(botones))
        if term:
            out.append(enlaces(term))
    return "\n".join(out)


# ───────────────────────── Telegram ─────────────────────────
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
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/sendMessage"
    for parte in trocear(texto):
        datos = {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": parte,
                 "disable_web_page_preview": True}
        if html_mode:
            datos["parse_mode"] = "HTML"
        requests.post(url, json=datos, timeout=30).raise_for_status()


# ───────────────────────── programa principal ─────────────────────────
def main() -> int:
    filtro = os.environ.get("FILTRO", "").strip()
    try:
        items = lanzar_scraper()
    except ApifyError as e:
        enviar(f"⚠️ Radar: {e}", html_mode=False)
        print(e, file=sys.stderr)
        return 1

    print(f"Anuncios recibidos: {len(items)}")
    productos = agrupar(items, filtro)
    if not productos:
        enviar("⚠️ El radar no encontró anuncios de producto esta vez"
               + (f" con la palabra «{filtro}»" if filtro else "")
               + ". Prueba otra vez o amplía las industrias en productos.py.", html_mode=False)
        return 1
    enviar(formatear(productos))
    print(f"Enviados {min(TOP, len(productos))} productos")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
