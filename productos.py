import os
import sys
import requests
import anthropic

# Ajusta los nichos a lo que quieras vigilar
NICHOS = "hogar, cocina, belleza y bienestar, mascotas, gadgets"

PROMPT = f"""Busca en la web qué productos físicos están creciendo ahora mismo
en España para dropshipping o venta online (nichos: {NICHOS}).
Usa fuentes como artículos de tendencias, rankings de Amazon.es, TikTok Shop,
reseñas y noticias de ecommerce.

Dame EXACTAMENTE 7 productos. Para cada uno:
- Nombre del producto
- Por qué sube (señal concreta: ventas, virales, estacionalidad)
- Precio de venta orientativo en España y coste aproximado
- Margen estimado
- Nivel de competencia (bajo/medio/alto) y riesgo principal
- Qué buscar en la Biblioteca de anuncios de Meta para validarlo

Ordénalos del más al menos prometedor. Sin introducción ni conclusión.
Texto plano, sin Markdown, con emojis para separar. No inventes datos:
si no estás seguro de una cifra, dilo."""


def generar_lista() -> str:
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
        messages=[{"role": "user", "content": PROMPT}],
    )
    texto = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not texto:
        raise RuntimeError("Claude no devolvió texto")
    return texto


def trocear(texto: str, limite: int = 4000) -> list[str]:
    """Divide el texto en trozos <= limite, cortando por saltos de línea."""
    partes, actual = [], ""
    for linea in texto.split("\n"):
        if len(actual) + len(linea) + 1 > limite:
            partes.append(actual)
            actual = ""
        actual += linea + "\n"
    if actual.strip():
        partes.append(actual)
    return partes


def enviar_telegram(mensaje: str) -> None:
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/sendMessage"
    for parte in trocear(mensaje):
        r = requests.post(
            url,
            json={
                "chat_id": os.environ["TELEGRAM_CHAT_ID"],
                "text": parte,
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        r.raise_for_status()


if __name__ == "__main__":
    try:
        lista = generar_lista()
        enviar_telegram("🏆 Productos ganadores de la semana · España\n\n" + lista)
        print("Mensaje enviado")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
