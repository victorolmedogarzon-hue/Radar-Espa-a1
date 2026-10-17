"""Radar de productos ganadores ORGÁNICOS en TikTok España (para dropshipping).

Flujo: Apify (vídeos orgánicos por hashtag/búsqueda) -> filtro anti-anuncios y
antigüedad -> puntuación (interacción + viralidad relativa) -> extracción del
producto con IA (opcional) -> agrupación por producto -> ranking.
"""
import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import requests
from apify_client import ApifyClient

ACTOR = "clockworks/tiktok-scraper"

# ---------------------------------------------------------------- AJUSTES
HASHTAGS = [
    "tiktokmademebuyit", "tiktokmehizocomprarlo", "productoviral",
    "comprastiktok", "amazonfinds", "gadgetsutiles", "hogarutil",
    "cosasdealiexpress", "cosasutiles", "descubrimientostiktok",
]
BUSQUEDAS = [
    "producto viral", "me compré esto", "dónde lo compro",
    "esto es una locura producto", "gadget útil", "no sabía que existía",
]
VIDEOS_POR_TERMINO = 40      # vídeos que pide a Apify por hashtag/búsqueda
DIAS_MAX = 14                # solo vídeos de los últimos X días
MIN_VISTAS = 20_000          # descarta vídeos con pocas vistas
MAX_VIDEOS_A_IA = 80         # cuántos vídeos top se analizan con IA
TOP_PRODUCTOS = 10           # cuántos productos se envían

# Si el vídeo lleva alguno de estos hashtags, es publicidad/colaboración
TAGS_PUBLI = {
    "ad", "ads", "publi", "publicidad", "sponsored", "patrocinado",
    "colaboracion", "colab", "anuncio", "paidpartnership", "gifted",
}
# Hashtags demasiado genéricos para usarlos como "nombre de producto" (modo sin IA)
TAGS_GENERICOS = {
    "fyp", "foryou", "foryoupage", "parati", "viral", "tiktok", "trend",
    "tiktokmademebuyit", "tiktokmehizocomprarlo", "productoviral",
    "comprastiktok", "amazonfinds", "gadgetsutiles", "hogarutil",
    "cosasdealiexpress", "cosasutiles", "descubrimientostiktok", "espana",
    "spain", "madrid", "barcelona", "amazon", "aliexpress", "shein", "temu",
    "review", "unboxing", "haul", "oferta", "chollo", "chollos", "tiktokshop",
}
IDIOMAS_OK = {"es", "ca", "gl", "eu", "un", "und", "", None}


# ---------------------------------------------------------------- UTILIDADES
def _env(*nombres, default=""):
    for n in nombres:
        v = os.getenv(n)
        if v:
            return v
    return default


def _num(x):
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


def _sin_acentos(s):
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower().strip()


def _hashtags(v):
    salida = []
    for h in v.get("hashtags") or []:
        n = h.get("name") if isinstance(h, dict) else h
        if n:
            salida.append(_sin_acentos(str(n)))
    return salida


def _fecha(v):
    iso = v.get("createTimeISO")
    if iso:
        try:
            return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        except ValueError:
            pass
    ts = v.get("createTime")
    if ts:
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            pass
    return None


# ---------------------------------------------------------------- 1) RECOGER
def recoger_videos(palabra=""):
    token = _env("APIFY_TOKEN", "APIFY_API_TOKEN")
    if not token:
        raise SystemExit("Falta el secret APIFY_TOKEN")
    client = ApifyClient(token)

    busquedas = list(BUSQUEDAS)
    if palabra:
        busquedas += [f"{palabra} viral", f"{palabra} me compré", f"{palabra} producto"]

    run_input = {
        "hashtags": HASHTAGS,
        "searchQueries": busquedas,
        "resultsPerPage": VIDEOS_POR_TERMINO,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadSubtitles": False,
        "proxyCountryCode": "ES",
    }
    try:
        run = client.actor(ACTOR).call(run_input=run_input)
    except Exception as e:  # por si el actor no acepta proxyCountryCode
        print(f"Apify falló con proxyCountryCode ({e}); reintento sin él")
        run_input.pop("proxyCountryCode", None)
        run = client.actor(ACTOR).call(run_input=run_input)
    return list(client.dataset(run["defaultDatasetId"]).iterate_items())


# ---------------------------------------------------------------- 2) FILTRAR Y PUNTUAR
def _es_anuncio(v):
    if v.get("isAd") or v.get("isSponsored"):
        return True
    if set(_hashtags(v)) & TAGS_PUBLI:
        return True
    texto = _sin_acentos(v.get("text", ""))
    return bool(re.search(r"#(ad|publi|sponsored|patrocinado)\b|paid partnership", texto))


def _metricas(v):
    vistas = max(_num(v.get("playCount")), 1)
    likes = _num(v.get("diggCount"))
    coment = _num(v.get("commentCount"))
    shares = _num(v.get("shareCount"))
    guard = _num(v.get("collectCount"))
    seguidores = _num((v.get("authorMeta") or {}).get("fans"))

    inter = (likes + coment + shares + guard) / vistas            # para mostrar
    inter_pond = (likes + coment + 2 * shares + 2 * guard) / vistas  # shares/guardados pesan doble
    viral = min(vistas / max(seguidores, 100), 50)                # vistas vs tamaño de la cuenta
    return {
        "vistas": vistas, "likes": likes, "coment": coment,
        "shares": shares, "guardados": guard, "seguidores": seguidores,
        "inter": inter, "viral": viral,
        "score": inter_pond * 100 + viral * 0.5,
    }


def filtrar_y_puntuar(items):
    limite = datetime.now(timezone.utc) - timedelta(days=DIAS_MAX)
    vistos, salida = set(), []
    for v in items:
        vid = v.get("id") or v.get("webVideoUrl")
        if not vid or vid in vistos:
            continue
        vistos.add(vid)
        if _es_anuncio(v):
            continue
        f = _fecha(v)
        if f is None or f < limite:
            continue
        if v.get("textLanguage") not in IDIOMAS_OK:
            continue
        m = _metricas(v)
        if m["vistas"] < MIN_VISTAS:
            continue
        salida.append((v, m))
    salida.sort(key=lambda x: x[1]["score"], reverse=True)
    return salida[:MAX_VIDEOS_A_IA]


# ---------------------------------------------------------------- 3) PRODUCTO (IA)
def _extraer_con_ia(videos):
    """Devuelve {indice: {producto, categoria, problema}} o None si no hay IA."""
    clave = _env("ANTHROPIC_API_KEY")
    if not clave:
        return None
    modelo = _env("ANTHROPIC_MODEL", default="claude-haiku-5-5")

    lineas = []
    for i, (v, _) in enumerate(videos):
        tags = " ".join("#" + t for t in _hashtags(v)[:8])
        texto = (v.get("text") or "").replace("\n", " ")[:220]
        lineas.append(f"{i}. {texto} {tags}")

    prompt = (
        "Estos son vídeos orgánicos de TikTok España. Para cada uno identifica el "
        "PRODUCTO FÍSICO que se muestra o recomienda.\n"
        "Devuelve SOLO un JSON (lista) con objetos "
        '{"i": número, "producto": "nombre genérico en español, minúsculas, 2-4 palabras", '
        '"categoria": "una palabra", "problema": "qué problema resuelve, máx 10 palabras"}.\n'
        "Si el vídeo NO vende/recomienda un producto físico (humor, receta, servicio, app, "
        "famoso, marca grande como Apple/Nike/Dyson, medicamento o suplemento), "
        'pon "producto": null. Usa el MISMO nombre para el mismo producto en distintos vídeos.\n\n'
        + "\n".join(lineas)
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": clave,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={"model": modelo, "max_tokens": 4000,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=120,
        )
        r.raise_for_status()
        texto = r.json()["content"][0]["text"]
        datos = json.loads(texto[texto.find("["): texto.rfind("]") + 1])
        return {int(d["i"]): d for d in datos if d.get("producto")}
    except Exception as e:
        print(f"IA no disponible ({e}); uso hashtags como alternativa")
        return None


def _nombre_sin_ia(v):
    for t in _hashtags(v):
        if t not in TAGS_GENERICOS and t not in TAGS_PUBLI and len(t) > 3:
            return t
    return None


# ---------------------------------------------------------------- 4) AGRUPAR
def _enlaces(nombre):
    q = quote_plus(nombre)
    return {
        "aliexpress": f"https://es.aliexpress.com/wholesale?SearchText={q}",
        "trends": f"https://trends.google.es/trends/explore?date=today%203-m&geo=ES&q={q}",
        "tiktok": f"https://www.tiktok.com/search?q={q}",
    }


def agrupar(videos):
    ia = _extraer_con_ia(videos)
    grupos = {}
    for i, (v, m) in enumerate(videos):
        if ia is not None:
            d = ia.get(i)
            if not d:
                continue
            nombre = str(d["producto"]).strip()
            cat, prob = d.get("categoria", ""), d.get("problema", "")
        else:
            nombre = _nombre_sin_ia(v)
            cat = prob = ""
            if not nombre:
                continue
        g = grupos.setdefault(
            _sin_acentos(nombre),
            {"nombre": nombre, "categoria": cat, "problema": prob, "videos": []},
        )
        g["videos"].append((v, m))

    resultado = []
    for g in grupos.values():
        mejor_por_creador = {}
        for v, m in g["videos"]:
            autor = (v.get("authorMeta") or {}).get("name") or v.get("id")
            if autor not in mejor_por_creador or m["score"] > mejor_por_creador[autor][1]["score"]:
                mejor_por_creador[autor] = (v, m)
        mejor = max(mejor_por_creador.values(), key=lambda x: x[1]["score"])
        g["creadores"] = len(mejor_por_creador)
        g["vistas"] = sum(m["vistas"] for _, m in g["videos"])
        g["inter"] = sum(m["inter"] for _, m in g["videos"]) / len(g["videos"])
        g["score"] = sum(m["score"] for _, m in mejor_por_creador.values())
        g["url"] = mejor[0].get("webVideoUrl", "")
        g.update(_enlaces(g["nombre"]))
        resultado.append(g)
    resultado.sort(key=lambda g: g["score"], reverse=True)
    return resultado


# ---------------------------------------------------------------- API PRINCIPAL
def buscar(palabra=""):
    palabra = (palabra or "").strip()
    items = recoger_videos(palabra)
    print(f"Apify devolvió {len(items)} vídeos")
    videos = filtrar_y_puntuar(items)
    print(f"Tras filtrar anuncios/antiguos/poco alcance: {len(videos)}")
    grupos = agrupar(videos)
    if palabra:
        p = _sin_acentos(palabra)
        grupos = [g for g in grupos if p in _sin_acentos(g["nombre"] + " " + g["categoria"])]
    return grupos[:TOP_PRODUCTOS]
