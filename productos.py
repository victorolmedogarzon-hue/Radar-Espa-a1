"""
Radar semanal GRATIS: lee los rankings públicos de Amazon.es
("Los que más suben" y "Más vendidos") y manda los mejores a Telegram,
con un enlace para validar cada producto en la Biblioteca de anuncios de Meta.
No usa ninguna API de pago.
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

# Categorías a vigilar: (nombre para mostrar, slug de Amazon.es)
CATEGORIAS = [
    ("🏠 Hogar y cocina", "kitchen"),
    ("💄 Belleza", "beauty"),
    ("🐶 Mascotas", "pet-supplies"),
    ("🔌 Electrónica / gadgets", "electronics"),
]
TOP_POR_LISTA = 5  # productos que se muestran por cada lista

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

ASIN_RE = re.compile(r"/dp/([A-Z0-9]{10})")


def descargar(url: str) -> BeautifulSoup | None:
    """Descarga una página. Devuelve None si Amazon bloquea o falla."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    if "validateCaptcha" in r.text:
        return None
    return BeautifulSoup(r.text, "html.parser")


def extraer_productos(soup: BeautifulSoup) -> list[dict]:
    """Saca título, enlace, precio y % de subida de una página de ranking."""
    productos, vistos = [], set()
    items = soup.select("div#gridItemRoot") or soup.select("div.zg-grid-general-faceout")
    for it in items:
        a = it.select_one('a[href*="/dp/"]')
        if not a:
            continue
        m = ASIN_RE.search(a.get("href", ""))
        if not m or m.group(1) in vistos:
            continue
        asin = m.group(1)

        # Título: bloque "line-clamp" o, si no, el alt de la imagen
        t = it.select_one('[class*="line-clamp"]')
        titulo = t.get_text(" ", strip=True) if t else ""
        if not titulo:
            img = it.select_one("img")
            titulo = (img.get("alt") or "").strip() if img else ""
        if not titulo:
            continue

        precio_el = it.select_one('[class*="price"]')
        precio = precio_el.get_text(strip=True) if precio_el else ""

        # % de subida de ventas (solo en "Los que más suben")
        cambio_el = it.select_one('[class*="percent-change"], [class*="zg-bdg-text"]')
        cambio_txt = cambio_el.get_text(strip=True) if cambio_el else ""
        n = re.search(r"([\d.]+)", cambio_txt.replace(",", "").replace(".", ""))
        cambio = int(n.group(1)) if n else 0

        vistos.add(asin)
        productos.append(
            {
                "asin": asin,
                "titulo": titulo,
                "precio": precio,
                "cambio": cambio_txt,
                "cambio_num": cambio,
                "url": f"https://www.amazon.es/dp/{asin}",
            }
        )
    return productos


def consulta_corta(titulo: str, palabras: int = 4) -> str:
    """Primeras palabras del título, sin signos raros, para usar en búsquedas."""
    limpio = re.sub(r"[^\w\s]", " ", titulo)
    return " ".join(limpio.split()[:palabras])


def etiqueta_instagram(titulo: str) -> str:
    """Hashtag de Instagram: 2 primeras palabras, minúsculas, sin acentos ni espacios."""
    base = unicodedata.normalize("NFKD", consulta_corta(titulo, 2))
    base = "".join(c for c in base if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", base.lower())


def enlaces_validacion(titulo: str) -> str:
    """Enlaces para comprobar el producto en Meta, TikTok, Instagram y Google Trends."""
    q = quote_plus(consulta_corta(titulo))
    meta = (
        "https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
        f"&country=ES&q={q}&search_type=keyword_unordered"
    )
    tiktok = f"https://www.tiktok.com/search?q={q}"
    insta = f"https://www.instagram.com/explore/tags/{etiqueta_instagram(titulo)}/"
    trends = f"https://trends.google.es/trends/explore?geo=ES&q={q}"
    return (
        f'🔎 <a href="{html.escape(meta, quote=True)}">Meta</a> · '
        f'<a href="{html.escape(tiktok, quote=True)}">TikTok</a> · '
        f'<a href="{html.escape(insta, quote=True)}">Instagram</a> · '
        f'<a href="{html.escape(trends, quote=True)}">Trends</a>'
    )


def linea_producto(i: int, p: dict) -> str:
    titulo = html.escape(p["titulo"][:70] + ("…" if len(p["titulo"]) > 70 else ""))
    extra = []
    if p["precio"]:
        extra.append(html.escape(p["precio"]))
    if p["cambio"]:
        extra.append(f"📈 {html.escape(p['cambio'])}")
    extra_txt = " · ".join(extra)
    return (
        f'{i}. <a href="{p["url"]}">{titulo}</a>'
        + (f"\n    {extra_txt}" if extra_txt else "")
        + f"\n    {enlaces_validacion(p['titulo'])}"
    )


def construir_mensaje() -> tuple[str, int]:
    bloques, total = [], 0
    for nombre, slug in CATEGORIAS:
        secciones = []
        for etiqueta, ruta in (
            ("🚀 Los que más suben", "movers-and-shakers"),
            ("🏅 Más vendidos", "bestsellers"),
        ):
            soup = descargar(f"https://www.amazon.es/gp/{ruta}/{slug}")
            time.sleep(random.uniform(2, 4))  # pausa para no parecer un robot
            if not soup:
                continue
            prods = extraer_productos(soup)
            if ruta == "movers-and-shakers":
                prods.sort(key=lambda p: p["cambio_num"], reverse=True)
            prods = prods[:TOP_POR_LISTA]
            if not prods:
                continue
            total += len(prods)
            lineas = [linea_producto(i, p) for i, p in enumerate(prods, 1)]
            secciones.append(f"<b>{etiqueta}</b>\n" + "\n".join(lineas))
        if secciones:
            bloques.append(f"<b>{nombre}</b>\n\n" + "\n\n".join(secciones))
    cabecera = (
        "🏆 <b>Radar semanal de productos · España</b>\n"
        "(Amazon.es · valida cada uno en Meta, TikTok, Instagram y Trends)\n\n"
    )
    return cabecera + "\n\n━━━━━━━━━━\n\n".join(bloques), total


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


def enviar_telegram(mensaje: str, html_mode: bool = True) -> None:
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/sendMessage"
    for parte in trocear(mensaje):
        datos = {
            "chat_id": os.environ["TELEGRAM_CHAT_ID"],
            "text": parte,
            "disable_web_page_preview": True,
        }
        if html_mode:
            datos["parse_mode"] = "HTML"
        r = requests.post(url, json=datos, timeout=30)
        r.raise_for_status()


if __name__ == "__main__":
    try:
        mensaje, total = construir_mensaje()
        if total == 0:
            enviar_telegram(
                "⚠️ El radar no pudo leer Amazon.es esta semana "
                "(probablemente bloqueó la visita desde GitHub). "
                "Prueba a lanzarlo de nuevo más tarde.",
                html_mode=False,
            )
            print("Amazon bloqueó las visitas", file=sys.stderr)
            sys.exit(1)
        enviar_telegram(mensaje)
        print(f"Enviados {total} productos")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
