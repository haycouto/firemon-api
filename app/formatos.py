"""Suporte a JSON e XML nas requisições e respostas.

Resposta em XML quando:
  - o parâmetro de consulta ?formato=xml é informado, ou
  - o primeiro tipo do cabeçalho Accept é application/xml ou text/xml.
Corpo da requisição em XML quando Content-Type contém "xml".
"""
import json
import xml.etree.ElementTree as ET

from defusedxml.ElementTree import fromstring as xml_seguro
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ValidationError


class ApiError(Exception):
    def __init__(self, status_code: int, mensagem: str, detalhes=None, status: str = "erro"):
        self.status_code = status_code
        self.mensagem = mensagem
        self.detalhes = detalhes
        self.status = status


# ---------- saída ----------

def quer_xml(request: Request) -> bool:
    formato = request.query_params.get("formato", "").lower()
    if formato:
        return formato == "xml"
    accept = request.headers.get("accept", "")
    primeiro = accept.split(",")[0].split(";")[0].strip().lower()
    return primeiro in ("application/xml", "text/xml")


def _montar(elemento: ET.Element, valor):
    if isinstance(valor, dict):
        for chave, v in valor.items():
            _montar(ET.SubElement(elemento, str(chave)), v)
    elif isinstance(valor, (list, tuple)):
        for v in valor:
            _montar(ET.SubElement(elemento, "item"), v)
    elif valor is None:
        elemento.set("nulo", "true")
    elif isinstance(valor, bool):
        elemento.text = "true" if valor else "false"
    else:
        elemento.text = str(valor)


def para_xml(dados, raiz: str = "resposta") -> bytes:
    elemento = ET.Element(raiz)
    _montar(elemento, dados)
    ET.indent(elemento)
    return ET.tostring(elemento, encoding="utf-8", xml_declaration=True)


def responder(request: Request, dados, status_code: int = 200):
    if quer_xml(request):
        return Response(para_xml(dados), status_code=status_code, media_type="application/xml; charset=utf-8")
    return JSONResponse(dados, status_code=status_code)


# ---------- entrada ----------

def _de_xml(elemento) -> dict | str | None:
    filhos = list(elemento)
    if not filhos:
        return elemento.text.strip() if elemento.text and elemento.text.strip() else None
    return {f.tag: _de_xml(f) for f in filhos}


async def ler_corpo(request: Request, modelo: type[BaseModel]):
    bruto = await request.body()
    tipo = request.headers.get("content-type", "").lower()
    try:
        if "xml" in tipo:
            dados = _de_xml(xml_seguro(bruto))
            dados = dados if isinstance(dados, dict) else {}
        else:
            dados = json.loads(bruto or b"{}")
    except Exception as e:  # JSON/XML malformado ou XML inseguro
        raise ApiError(400, f"Corpo da requisição inválido: {e}")
    try:
        return modelo.model_validate(dados)
    except ValidationError as e:
        raise ApiError(422, "Dados inválidos", erros_validacao(e.errors()))


def erros_validacao(erros) -> list[dict]:
    return [
        {"campo": ".".join(str(p) for p in e.get("loc", []) if p != "body"), "erro": e.get("msg", "")}
        for e in erros
    ]


# ---------- documentação OpenAPI ----------

def corpo_openapi(modelo: type[BaseModel], raiz: str) -> dict:
    """Declara no Swagger que o endpoint aceita JSON e XML."""
    schema = modelo.model_json_schema()
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": schema},
                "application/xml": {"schema": {**schema, "xml": {"name": raiz}}},
            },
        }
    }


RESPOSTAS_JSON_XML = {
    200: {"content": {"application/json": {}, "application/xml": {}}, "description": "OK (JSON ou XML)"}
}
