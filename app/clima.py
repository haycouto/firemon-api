"""Consumo de webservice de terceiro: Open-Meteo (https://open-meteo.com).

API REST pública e gratuita, sem chave de acesso, que retorna JSON.
A partir da temperatura e umidade calculamos o Índice de Angström,
um índice clássico de risco de incêndio:  I = H/20 + (27 - T)/10
"""
import httpx

URL_OPEN_METEO = "https://api.open-meteo.com/v1/forecast"


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


async def consultar_clima(latitude: float, longitude: float) -> dict:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation",
        "timezone": "America/Sao_Paulo",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as cliente:
            r = await cliente.get(URL_OPEN_METEO, params=params)
            r.raise_for_status()
            atual = r.json()["current"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise ClimaIndisponivel(str(e)) from e

    temperatura = atual["temperature_2m"]
    umidade = atual["relative_humidity_2m"]
    indice, risco = indice_angstrom(temperatura, umidade)
    return {
        "horario_local": atual.get("time"),
        "temperatura_c": temperatura,
        "umidade_relativa": umidade,
        "vento_kmh": atual.get("wind_speed_10m"),
        "precipitacao_mm": atual.get("precipitation"),
        "indice_angstrom": indice,
        "risco_incendio": risco,
        "fonte": "Open-Meteo (open-meteo.com)",
    }
