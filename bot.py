"""
Radar semanal de productos para España (Telegram).
Cada vez que se ejecuta te envía:
  1) Enlaces a las fuentes gratuitas de "productos ganadores", ya filtradas por España.
  2) Tu lista de seguimiento: enlaces directos a anuncios activos y tendencia de cada producto.
  3) Las búsquedas que más suben hoy en Google España (feed RSS público).
Solo usa la librería estándar de Python. No necesita claves de ninguna otra plataforma.
"""
import html
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Productos o nichos que quieres vigilar, separados por coma. Cámbialos cuando quieras.
KEYWORDS = [k.strip() for k in os.getenv(
    "KEYWORDS", "masajeador cervical, humidificador, organizador cocina"
).split(",") if k.strip()][:10]

GEO = os.getenv("GEO", "ES")  # país para Meta y Google Trends


def q(text):
    return urllib.parse.quote_plus(text)


def esc(text):
    return html.escape(text, quote=True)


def meta_ads_url(keyword):
    return ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
            f"&country={GEO}&q={q(keyword)}&search_type=keyword_unordered&media_type=all")


def trends_url(keyword):
    return f"https://trends.google.es/trends/explore?geo={GEO}&date=today%203-m&q={q(keyword)}"


def fetch_google_trending():
    """Top de búsquedas en tendencia en Google (feed público). Si falla, devuelve lista vacía."""
    url = f"https://trends.google.com/trending/rss?geo={GEO}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (radar-bot)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            root = ET.fromstring(r.read())
    except (urllib.error.URLError, ET.ParseError, TimeoutError) as e:
        print("Aviso: no se pudo leer Google Trends:", e)
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        traffic = ""
        for child in item:
            if child.tag.endswith("approx_traffic"):
                traffic = (child.text or "").strip()
        if title:
            out.append((title, traffic))
    return out[:10]


def build_message():
    L = ["📡 <b>Radar semanal de productos · España</b>", ""]
    L.append("🔎 <b>1. Fuentes para mirar hoy</b>")
    L.append(f'• <a href="{esc(f"https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country={GEO}")}">'
             "Biblioteca de anuncios de Meta (España)</a> → busca anuncios activos desde hace semanas")
    L.append('• <a href="https://www.amazon.es/gp/movers-and-shakers">Amazon.es · Novedades más vendidas</a>')
    L.append('• <a href="https://www.amazon.es/gp/bestsellers">Amazon.es · Más vendidos</a>')
    L.append('• <a href="https://ads.tiktok.com/business/creativecenter">TikTok Creative Center</a> → elige región España')
    L.append(f'• <a href="https://trends.google.es/trending?geo={GEO}">Google Trends España</a>')
    L.append("")
    if KEYWORDS:
        L.append("👀 <b>2. Tu lista de seguimiento</b>")
        for k in KEYWORDS:
            L.append(f'• {esc(k)}: <a href="{esc(meta_ads_url(k))}">anuncios</a> · '
                     f'<a href="{esc(trends_url(k))}">tendencia 3 meses</a>')
        L.append("")
    trending = fetch_google_trending()
    if trending:
        L.append("🔥 <b>3. Lo que más suben hoy en Google España</b>")
        L.append("<i>(suelen ser noticias; úsalo solo como inspiración)</i>")
        for i, (t, traffic) in enumerate(trending, 1):
            L.append(f"{i}. {esc(t)}" + (f" ({esc(traffic)} búsquedas)" if traffic else ""))
        L.append("")
    L.append("✅ <b>Antes de vender un producto</b>")
    L.append("• ¿Hay anuncios que llevan semanas activos en España?")
    L.append("• ¿La tendencia es estable o sube, no un pico de un día?")
    L.append("• Calcula tu margen real: producto + envío + IVA + publicidad + devoluciones.")
    L.append("• Empieza con una prueba pequeña.")
    L.append("")
    L.append("<i>Esto son pistas, no garantías de ventas.</i>")
    return "\n".join(L)[:4000]


def send(text):
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    }).encode()
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data, method="POST"), timeout=30).read()
    except urllib.error.HTTPError as e:
        sys.exit(f"Telegram devolvió error {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}")


if __name__ == "__main__":
    send(build_message())
    print("Radar enviado.")
