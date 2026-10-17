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
from decimal import Decimal
from urllib.parse import quote_plus

import requests
from apify_client import ApifyClient

ACTOR = "clockworks/tiktok-scraper"

# ---------------------------------------------------------------- AJUSTES
# COSTE: Apify cobra ~0,005 $ por vídeo. Vídeos pedidos = (hashtags + búsquedas) x VIDEOS_POR_TERMINO
HASHTAGS = [
    "tiktokmehizocomprarlo", "productoviral", "comprastiktok",
    "gadgetsutiles", "hogarutil",
]
BUSQUEDAS = [
    "producto viral", "gadget que necesitas", "no sabía que existía",
]
VIDEOS_POR_TERMINO = 12      # 8 términos x 12 = ~96 vídeos (~0,50 $ por ejecución)
MAX_COSTE_USD = 0.60         # tope duro: Apify para el run si supera este gasto
DIAS_MAX = 14                # criterio ESTRICTO: vídeos de los últimos X días
MIN_VISTAS = 20_000          # criterio ESTRICTO: mínimo de vistas
DIAS_RELAJADO = 30           # si faltan productos, se aceptan vídeos de hasta X días...
MIN_VISTAS_RELAJADO = 5_000  # ...y con al menos estas vistas (se marcan con ⚠️)
MIN_PRODUCTOS = 10           # el bot intenta SIEMPRE llegar a este mínimo
TOP_PRODUCTOS = 20           # máximo de productos que se envían
MAX_VIDEOS_A_IA = 120        # cuántos vídeos top se analizan con IA

# Si con la primera búsqueda no se llega al mínimo, se hace UNA segunda con estos términos
EXTRA_HASHTAGS = [
    "tiktokmademebuyit", "amazonfinds", "cosasutiles", "descubrimientostiktok",
    "cosasdealiexpress",
]
EXTRA_BUSQUEDAS = [
    "gadget útil casa", "invento genial", "producto que tienes que tener",
]

# Vídeos de compras/paseos/vlogs: NO son productos. Se descartan por texto o duración.
DURACION_MAX = 75            # segundos; los vlogs y hauls suelen durar más
PALABRAS_EXCLUIDAS = {
    "bazar", "bazaar", "bazares", "haul", "vlog", "mercadillo", "mercado", "rastro",
    "zoco", "tour", "paseo", "shopping", "tienda", "tiendas", "comprando", "compras",
    "primark", "ikea", "mercadona", "lidl", "carrefour", "action", "tedi", "leroy",
    "outlet", "rebajas", "viaje", "turquia", "dubai", "marruecos", "grwm", "ootd",
    "tryon", "tryonhaul", "sheinhaul", "unboxing", "pedido",
}

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
def _lanzar(client, run_input):
    """Lanza el actor con tope de gasto (si la versión de apify-client lo soporta)."""
    try:
        return client.actor(ACTOR).call(
            run_input=run_input, max_total_charge_usd=Decimal(str(MAX_COSTE_USD))
        )
    except TypeError:
        return client.actor(ACTOR).call(run_input=run_input)


def recoger_videos(palabra="", extra=False):
    token = _env("APIFY_TOKEN", "APIFY_API_TOKEN")
    if not token:
        raise SystemExit("Falta el secret APIFY_TOKEN")
    client = ApifyClient(token)

    # Si das un DATASET_ID reutiliza datos ya descargados: NO gasta crédito de Apify
    dataset_id = _env("DATASET_ID").strip()
    if dataset_id and extra:
        return []
    if dataset_id:
        print(f"Reutilizando dataset {dataset_id} (sin coste)")
    else:
        busquedas = list(EXTRA_BUSQUEDAS if extra else BUSQUEDAS)
        if palabra:
            busquedas += [f"{palabra} viral", f"{palabra} me compré"]

        run_input = {
            "hashtags": EXTRA_HASHTAGS if extra else HASHTAGS,
            "searchQueries": busquedas,
            "resultsPerPage": VIDEOS_POR_TERMINO,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
            "shouldDownloadSubtitles": False,
            "proxyCountryCode": "ES",
            "oldestPostDateUnified": (datetime.now(timezone.utc) - timedelta(days=DIAS_RELAJADO)).strftime("%Y-%m-%d"),
        }
        try:
            run = _lanzar(client, run_input)
        except Exception as e:  # por si el actor rechaza algún campo opcional
            print(f"Apify falló ({e}); reintento sin proxyCountryCode ni fecha")
            run_input.pop("proxyCountryCode", None)
            run_input.pop("oldestPostDateUnified", None)
            run = _lanzar(client, run_input)
        # Compatible con versiones nuevas (objeto Run) y antiguas (diccionario) de apify-client
        if isinstance(run, dict):
            dataset_id = run.get("defaultDatasetId") or run.get("default_dataset_id")
        else:
            dataset_id = getattr(run, "default_dataset_id", None) or getattr(run, "defaultDatasetId", None)
    if not dataset_id:
        raise SystemExit(f"Apify no devolvió dataset. Respuesta: {run}")
    items = []
    for it in client.dataset(dataset_id).iterate_items():
        items.append(it if isinstance(it, dict) else (it.model_dump(by_alias=True) if hasattr(it, "model_dump") else dict(it)))
    return items


# ---------------------------------------------------------------- 2) FILTRAR Y PUNTUAR
def _es_anuncio(v):
    if v.get("isAd") or v.get("isSponsored"):
        return True
    if set(_hashtags(v)) & TAGS_PUBLI:
        return True
    texto = _sin_acentos(v.get("text", ""))
    return bool(re.search(r"#(ad|publi|sponsored|patrocinado)\b|paid partnership", texto))


def _es_ruido(v):
    """Vlogs, hauls, paseos por tiendas/bazares y vídeos demasiado largos."""
    dur = _num((v.get("videoMeta") or {}).get("duration"))
    if dur and dur > DURACION_MAX:
        return True
    texto = _sin_acentos(v.get("text", "")) + " " + " ".join(_hashtags(v))
    palabras = set(re.findall(r"[a-z0-9ñ]+", texto))
    return bool(palabras & PALABRAS_EXCLUIDAS)


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
    """Acepta vídeos con criterio RELAJADO y marca cuáles cumplen el ESTRICTO."""
    ahora = datetime.now(timezone.utc)
    lim_estricto = ahora - timedelta(days=DIAS_MAX)
    lim_relajado = ahora - timedelta(days=DIAS_RELAJADO)
    vistos, salida = set(), []
    for v in items:
        vid = v.get("id") or v.get("webVideoUrl")
        if not vid or vid in vistos:
            continue
        vistos.add(vid)
        if _es_anuncio(v) or _es_ruido(v):
            continue
        f = _fecha(v)
        if f is None or f < lim_relajado:
            continue
        if v.get("textLanguage") not in IDIOMAS_OK:
            continue
        m = _metricas(v)
        if m["vistas"] < MIN_VISTAS_RELAJADO:
            continue
        m["estricto"] = f >= lim_estricto and m["vistas"] >= MIN_VISTAS
        salida.append((v, m))
    # primero los que cumplen el criterio estricto, luego por puntuación
    salida.sort(key=lambda x: (x[1]["estricto"], x[1]["score"]), reverse=True)
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
        "Eres analista de dropshipping. Estos son textos de vídeos orgánicos de TikTok España. "
        "Para cada uno decide si el vídeo trata de UN ÚNICO PRODUCTO FÍSICO concreto que se "
        "podría vender online (gadget, accesorio, utensilio, artículo de hogar, belleza, mascotas, "
        "coche, etc.).\n"
        "Devuelve SOLO un JSON (lista) con objetos "
        '{"i": número, "producto": "nombre específico del producto en español, minúsculas, 2-4 palabras", '
        '"categoria": "una palabra", "problema": "qué problema resuelve, máx 10 palabras", '
        '"confianza": 1-5}.\n'
        'Pon "producto": null si el vídeo es: compras o paseo por una tienda/bazar/mercado/centro comercial, '
        "vlog, haul con varios productos, lista de varias cosas, ropa de moda genérica, comida o receta, "
        "humor, servicio, app, viaje, persona famosa, marca grande (Apple, Nike, Dyson…), "
        "medicamento o suplemento, o si el texto NO permite saber qué producto concreto es.\n"
        '"confianza" = lo seguro que estás de que es un único producto concreto (5 = el texto lo nombra claramente; '
        "3 = lo deduces; menos de 3 = no lo pongas). Usa el MISMO nombre para el mismo producto en distintos vídeos. "
        "Nombres genéricos como 'gadget', 'cosa útil' o 'producto viral' NO valen.\n\n"
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
            json={"model": modelo, "max_tokens": 8000,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=120,
        )
        r.raise_for_status()
        texto = r.json()["content"][0]["text"]
        datos = json.loads(texto[texto.find("["): texto.rfind("]") + 1])
        return {int(d["i"]): d for d in datos if d.get("producto") and _num(d.get("confianza", 3)) >= 3}
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
            # si la IA no está muy segura (confianza 3), el producto sale como "señal más débil"
            m["estricto"] = bool(m.get("estricto")) and _num(d.get("confianza", 3)) >= 4
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
        g["estricto"] = any(m.get("estricto") for _, m in g["videos"])
        g["url"] = mejor[0].get("webVideoUrl", "")
        g.update(_enlaces(g["nombre"]))
        resultado.append(g)
    resultado.sort(key=lambda g: (g["estricto"], g["score"]), reverse=True)
    return resultado


# ---------------------------------------------------------------- API PRINCIPAL
def _procesar(items, palabra):
    videos = filtrar_y_puntuar(items)
    print(f"Vídeos válidos (sin anuncios, últimos {DIAS_RELAJADO} días): {len(videos)}")
    grupos = agrupar(videos)
    if palabra:
        p = _sin_acentos(palabra)
        grupos = [g for g in grupos if p in _sin_acentos(g["nombre"] + " " + g["categoria"])]
    print(f"Productos encontrados: {len(grupos)} "
          f"({sum(1 for g in grupos if g['estricto'])} con criterio estricto)")
    return grupos


def buscar(palabra=""):
    palabra = (palabra or "").strip()
    items = recoger_videos(palabra)
    print(f"Apify devolvió {len(items)} vídeos")
    grupos = _procesar(items, palabra)

    # Si no se llega al mínimo, una segunda búsqueda con más hashtags (solo 1 vez)
    if len(grupos) < MIN_PRODUCTOS and not _env("DATASET_ID").strip():
        print(f"Solo {len(grupos)} productos (mínimo {MIN_PRODUCTOS}): amplío la búsqueda")
        extra = recoger_videos(palabra, extra=True)
        print(f"Búsqueda ampliada: {len(extra)} vídeos más")
        grupos = _procesar(items + extra, palabra)

    return grupos[:TOP_PRODUCTOS]

    return grupos[:TOP_PRODUCTOS]
