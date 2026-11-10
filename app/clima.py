"""Consumo de webservices de terceiros para clima.

1º Open-Meteo (https://open-meteo.com) - REST/JSON, gratuito, sem chave.
2º MET Norway (https://api.met.no) - REST/JSON, gratuito, sem chave; usado
   quando o Open-Meteo recusa a chamada (ex.: 429 Too Many Requests, comum
   em hospedagens com IP compartilhado como o Render).

As respostas ficam em cache por 10 minutos por coordenada, o que reduz as
chamadas aos terceiros; se todos falharem, devolve o último valor em cache.

A partir da temperatura e umidade calculamos o Índice de Angström,
um índice clássico de risco de incêndio:  I = H/20 + (27 - T)/10
"""
import time

import httpx

URL_OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
URL_MET_NORWAY = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
USER_AGENT = "FireMon-API/1.0 academic-project"  # MET Norway exige User-Agent identificado
CACHE_SEGUNDOS = 600

_cache: dict[tuple[float, float], tuple[float, dict]] = {}


class ClimaIndisponivel(Exception):
    pass


def indice_angstrom(temperatura: float, umidade: float) -> tuple[float, str]:
    indice = round(umidade / 20 + (27 - temperatura) / 10, 2)
    if indice > 4.0:
        risco = "baixo"
    elif indice > 2.5:
        risco = "moderado"
    elif indice > 2.0:
        risco = "alto"
    else:
        risco = "muito_alto"
    return indice, risco


async def _open_meteo(cliente: httpx.AsyncClient, lat: float, lon: float) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation",
        "timezone": "America/Sao_Paulo",
    }
    r = await cliente.get(URL_OPEN_METEO, params=params)
    r.raise_for_status()
    atual = r.json()["current"]
    return {
        "horario": atual.get("time"),
        "temperatura_c": atual["temperature_2m"],
        "umidade_relativa": atual["relative_humidity_2m"],
        "vento_kmh": atual.get("wind_speed_10m"),
        "precipitacao_mm": atual.get("precipitation"),
        "fonte": "Open-Meteo (open-meteo.com)",
    }


async def _met_norway(cliente: httpx.AsyncClient, lat: float, lon: float) -> dict:
    params = {"lat": round(lat, 4), "lon": round(lon, 4)}
    r = await cliente.get(URL_MET_NORWAY, params=params, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    serie = r.json()["properties"]["timeseries"][0]
    det = serie["data"]["instant"]["details"]
    prox = serie["data"].get("next_1_hours", {}).get("details", {})
    return {
        "horario": serie.get("time"),
        "temperatura_c": det["air_temperature"],
        "umidade_relativa": det["relative_humidity"],
        "vento_kmh": round(det.get("wind_speed", 0) * 3.6, 1),  # m/s -> km/h
        "precipitacao_mm": prox.get("precipitation_amount"),
        "fonte": "MET Norway (api.met.no)",
    }


async def consultar_clima(latitude: float, longitude: float) -> dict:
    chave = (round(latitude, 4), round(longitude, 4))
    em_cache = _cache.get(chave)
    if em_cache and time.time() - em_cache[0] < CACHE_SEGUNDOS:
        return {**em_cache[1], "cache": True}

    erros = []
    dados = None
    async with httpx.AsyncClient(timeout=8.0) as cliente:
        for provedor in (_open_meteo, _met_norway):
            try:
                dados = await provedor(cliente, latitude, longitude)
                break
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                erros.append(f"{provedor.__name__.strip('_')}: {e}")

    if dados is None:
        if em_cache:  # todos falharam: devolve o último valor conhecido
            return {**em_cache[1], "cache": True, "aviso": "dados antigos; terceiros indisponíveis"}
        raise ClimaIndisponivel(" | ".join(erros))

    indice, risco = indice_angstrom(dados["temperatura_c"], dados["umidade_relativa"])
    dados.update(indice_angstrom=indice, risco_incendio=risco)
    if erros:
        dados["observacao"] = "Open-Meteo indisponível; usado provedor alternativo"
    _cache[chave] = (time.time(), dados)
    return {**dados, "cache": False}
