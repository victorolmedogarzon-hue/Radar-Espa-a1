"""Envía a Telegram el radar semanal de productos ganadores orgánicos."""
import html
import os
import sys

import requests

import productos


def _env(*nombres):
    for n in nombres:
        v = os.getenv(n)
        if v:
            return v
    return ""


def _fmt(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def _bloque(i, g):
    esc = html.escape
    lineas = [f"🔥 <b>{i}. {esc(g['nombre'].capitalize())}</b>"
              + (f" <i>({esc(g['categoria'])})</i>" if g["categoria"] else "")]
    if g["problema"]:
        lineas.append(f"💡 {esc(g['problema'])}")
    if not g.get("estricto", True):
        lineas.append("⚠️ Señal más débil (vídeo más antiguo, con menos vistas o producto poco claro)")
    lineas.append(
        f"👥 {g['creadores']} creador(es) · 👁 {_fmt(g['vistas'])} vistas totales "
        f"(mejor vídeo: {_fmt(g.get('max_vistas', g['vistas']))}) · "
        f"📈 {g['inter'] * 100:.1f}% interacción"
    )
    enlaces = []
    if g["url"]:
        enlaces.append(f"🎬 <a href=\"{esc(g['url'])}\">Mejor vídeo</a>")
    enlaces += [
        f"🛒 <a href=\"{esc(g['aliexpress'])}\">AliExpress</a>",
        f"📊 <a href=\"{esc(g['trends'])}\">Trends</a>",
        f"🎵 <a href=\"{esc(g['tiktok'])}\">TikTok</a>",
    ]
    lineas.append(" · ".join(enlaces))
    return "\n".join(lineas)


def construir_mensajes(grupos, palabra):
    titulo = "🏆 <b>Productos ganadores ORGÁNICOS · TikTok España</b>\n"
    titulo += f"<i>Últimos {productos.DIAS_MAX} días · sin anuncios · por interacción y nº de creadores</i>"
    if palabra:
        titulo += f"\n🔎 Filtro: <b>{html.escape(palabra)}</b>"
    if not grupos:
        return [titulo + "\n\nNo encontré productos con esos criterios esta vez."]

    if len(grupos) < productos.MIN_PRODUCTOS:
        titulo += (f"\n\n⚠️ Solo encontré {len(grupos)} productos (el mínimo era "
                   f"{productos.MIN_PRODUCTOS}): TikTok/Apify no devolvieron más vídeos válidos esta vez.")
    bloques = [_bloque(i, g) for i, g in enumerate(grupos, 1)]
    pie = ("\n✅ Antes de vender: mira precio en AliExpress (margen ≥3x), "
           "que Trends suba y que no esté saturado en anuncios.")
    mensajes, actual = [], titulo
    for b in bloques:
        if len(actual) + len(b) + 40 > 3800:
            mensajes.append(actual)
            actual = ""
        actual += "\n\n———————\n\n" + b if actual else b
    mensajes.append(actual + "\n" + pie)
    return mensajes


def enviar(mensajes):
    token = _env("TELEGRAM_TOKEN", "TELEGRAM_BOT_TOKEN", "BOT_TOKEN")
    chat = _env("TELEGRAM_CHAT_ID", "CHAT_ID")
    if not token or not chat:
        print("Faltan TELEGRAM_TOKEN / TELEGRAM_CHAT_ID: muestro el mensaje por consola\n")
        print("\n\n".join(mensajes))
        return
    for texto in mensajes:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat,
                "text": texto,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        if not r.ok:
            raise SystemExit(f"Error de Telegram: {r.status_code} {r.text}")


def main():
    palabra = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("PALABRA", "")).strip()
    grupos = productos.buscar(palabra)
    enviar(construir_mensajes(grupos, palabra))
    print(f"Enviados {len(grupos)} productos")


if __name__ == "__main__":
    main()
