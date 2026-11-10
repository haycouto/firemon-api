"""FireMon - Webservices REST para monitoramento e combate a incêndio.

Trabalho final - CAC3024 Integração de Sistemas para Automação.
Documentação interativa (Swagger): /docs   |   Painel: /
"""
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import database as db
from .clima import ClimaIndisponivel, consultar_clima
from .formatos import (
    RESPOSTAS_JSON_XML,
    ApiError,
    corpo_openapi,
    erros_validacao,
    ler_corpo,
    responder,
)

VERSAO = "1.0.0"
STATIC = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    db.inicializar()
    yield


app = FastAPI(
    title="FireMon API",
    version=VERSAO,
    description=(
        "Webservices REST para monitoramento de sensores de incêndio e comando de atuadores "
        "(sirenes, sprinklers, exaustores).\n\n"
        "**Formatos:** todas as rotas respondem em JSON (padrão) ou XML "
        "(`?formato=xml` ou cabeçalho `Accept: application/xml`). "
        "Rotas POST/PUT aceitam corpo em JSON ou XML (`Content-Type: application/xml`)."
    ),
    lifespan=ciclo_de_vida,
)

Formato = Annotated[
    Literal["json", "xml"] | None,
    Query(description="Formato da resposta (alternativa ao cabeçalho Accept)"),
]


# ======================================================================
# Tratamento de erros (mesmo formato em JSON e XML)
# ======================================================================

@app.exception_handler(ApiError)
async def _erro_api(request: Request, exc: ApiError):
    dados = {"status": exc.status, "mensagem": exc.mensagem}
    if exc.detalhes is not None:
        dados["detalhes"] = exc.detalhes
    return responder(request, dados, exc.status_code)


@app.exception_handler(RequestValidationError)
async def _erro_validacao(request: Request, exc: RequestValidationError):
    dados = {"status": "erro", "mensagem": "Parâmetros inválidos",
             "detalhes": erros_validacao(exc.errors())}
    return responder(request, dados, 422)


@app.exception_handler(StarletteHTTPException)
async def _erro_http(request: Request, exc: StarletteHTTPException):
    return responder(request, {"status": "erro", "mensagem": str(exc.detail)}, exc.status_code)


# ======================================================================
# Modelos de entrada
# ======================================================================

TipoSensor = Literal["fumaca", "temperatura", "gas", "chama"]
TipoAtuador = Literal["sirene", "sprinkler", "exaustor"]


class SensorCriar(BaseModel):
    codigo: str = Field(pattern=r"^[A-Za-z0-9-]{3,20}$", examples=["SF-SP02"])
    tipo: TipoSensor
    setor_id: int = Field(examples=[1])
    descricao: str | None = Field(default=None, examples=["Fumaça - mezanino"])
    limite_alarme: float | None = Field(
        default=None, description="Se omitido, usa o padrão do tipo de sensor")


class SensorAlterar(BaseModel):
    setor_id: int | None = None
    descricao: str | None = None
    limite_alarme: float | None = None
    ativo: bool | None = None


class LeituraCriar(BaseModel):
    sensor_codigo: str = Field(examples=["ST-CT01"])
    valor: float = Field(examples=[72.5])


class ComandoAtuador(BaseModel):
    comando: Literal["ligar", "desligar"]
    forcar: bool = Field(
        default=False,
        description="Permite desligar sprinkler mesmo com o setor em alarme")


# ======================================================================
# Funções auxiliares
# ======================================================================

def _sensor_dict(r) -> dict:
    d = {
        "codigo": r["codigo"],
        "tipo": r["tipo"],
        "setor_id": r["setor_id"],
        "setor": r["setor_nome"],
        "descricao": r["descricao"],
        "unidade": r["unidade"],
        "limite_alarme": r["limite_alarme"],
        "ativo": bool(r["ativo"]),
    }
    if "ult_valor" in r.keys():
        d["ultima_leitura"] = None if r["ult_em"] is None else {
            "valor": r["ult_valor"], "alarme": bool(r["ult_alarme"]), "registrado_em": r["ult_em"]}
    return d


def _leitura_dict(r) -> dict:
    d = {"id": r["id"], "sensor_codigo": r["sensor_codigo"], "valor": r["valor"],
         "alarme": bool(r["alarme"]), "registrado_em": r["registrado_em"]}
    for extra in ("tipo", "unidade", "setor_id"):
        if extra in r.keys():
            d[extra] = r[extra]
    return d


def _atuador_dict(r) -> dict:
    return {"codigo": r["codigo"], "tipo": r["tipo"], "setor_id": r["setor_id"],
            "setor": r["setor_nome"], "descricao": r["descricao"], "estado": r["estado"],
            "atualizado_em": r["atualizado_em"]}


SQL_SENSORES = """
SELECT s.*, st.nome AS setor_nome,
       l.valor AS ult_valor, l.alarme AS ult_alarme, l.registrado_em AS ult_em
  FROM sensores s
  JOIN setores st ON st.id = s.setor_id
  LEFT JOIN leituras l ON l.id = (
        SELECT id FROM leituras WHERE sensor_codigo = s.codigo
         ORDER BY registrado_em DESC, id DESC LIMIT 1)
"""

SQL_ATUADORES = """
SELECT a.*, st.nome AS setor_nome FROM atuadores a JOIN setores st ON st.id = a.setor_id
"""


def _buscar_sensor(conn, codigo: str):
    return conn.execute(SQL_SENSORES + " WHERE s.codigo = ?", (codigo.upper(),)).fetchone()


def _buscar_atuador(conn, codigo: str):
    return conn.execute(SQL_ATUADORES + " WHERE a.codigo = ?", (codigo.upper(),)).fetchone()


def _setor_existe(conn, setor_id: int) -> bool:
    return conn.execute("SELECT 1 FROM setores WHERE id = ?", (setor_id,)).fetchone() is not None


def _setor_em_alarme(conn, setor_id: int) -> list[str]:
    linhas = conn.execute(
        SQL_SENSORES + " WHERE s.setor_id = ? AND s.ativo = 1 AND l.alarme = 1", (setor_id,)
    ).fetchall()
    return [r["codigo"] for r in linhas]


def _registrar_comando(conn, atuador, comando, origem, resultado, mensagem):
    conn.execute(
        "INSERT INTO comandos (atuador_codigo, comando, origem, resultado, mensagem, executado_em)"
        " VALUES (?,?,?,?,?,?)",
        (atuador, comando, origem, resultado, mensagem, db.agora()),
    )


# Atuadores acionados automaticamente quando um sensor entra em alarme
ACIONAMENTO_AUTOMATICO = {
    "fumaca": ("sirene", "exaustor"),
    "gas": ("sirene", "exaustor"),
    "temperatura": ("sirene", "sprinkler"),
    "chama": ("sirene", "sprinkler"),
}


def _acionar_automatico(conn, setor_id: int, tipo_sensor: str, sensor: str) -> list[dict]:
    tipos = ACIONAMENTO_AUTOMATICO[tipo_sensor]
    marcadores = ",".join("?" * len(tipos))
    alvos = conn.execute(
        f"SELECT codigo, tipo FROM atuadores WHERE setor_id = ? AND tipo IN ({marcadores})"
        " AND estado = 'desligado'",
        (setor_id, *tipos),
    ).fetchall()
    acoes = []
    for a in alvos:
        conn.execute("UPDATE atuadores SET estado = 'ligado', atualizado_em = ? WHERE codigo = ?",
                     (db.agora(), a["codigo"]))
        _registrar_comando(conn, a["codigo"], "ligar", f"automatico:{sensor}", "sucesso",
                           "Acionamento automático por alarme")
        acoes.append({"atuador": a["codigo"], "tipo": a["tipo"], "comando": "ligar"})
    return acoes


# ======================================================================
# Páginas e status do serviço
# ======================================================================

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
async def painel():
    return FileResponse(STATIC / "index.html")


@app.get("/api/v1/status", tags=["Serviço"], responses=RESPOSTAS_JSON_XML,
         summary="Verifica se o serviço e o banco estão no ar")
async def status_servico(request: Request, formato: Formato = None):
    with db.conectar() as conn:
        n = conn.execute("SELECT COUNT(*) FROM sensores").fetchone()[0]
    return responder(request, {"status": "online", "versao": VERSAO, "banco": "ok",
                               "sensores_cadastrados": n, "horario_utc": db.agora()})


# ======================================================================
# SETORES
# ======================================================================

@app.get("/api/v1/setores", tags=["Setores"], responses=RESPOSTAS_JSON_XML,
         summary="Lista os setores monitorados")
async def listar_setores(request: Request, formato: Formato = None):
    with db.conectar() as conn:
        setores = [dict(r) for r in conn.execute("SELECT * FROM setores ORDER BY id")]
        for s in setores:
            s["sensores_em_alarme"] = _setor_em_alarme(conn, s["id"])
            s["em_alarme"] = bool(s["sensores_em_alarme"])
    return responder(request, {"total": len(setores), "setores": setores})


@app.get("/api/v1/setores/{setor_id}/clima", tags=["Webservice de terceiro"],
         responses=RESPOSTAS_JSON_XML,
         summary="Clima atual e risco de incêndio do setor (consome a API Open-Meteo)")
async def clima_setor(setor_id: int, request: Request, formato: Formato = None):
    with db.conectar() as conn:
        setor = conn.execute("SELECT * FROM setores WHERE id = ?", (setor_id,)).fetchone()
    if setor is None:
        raise ApiError(404, f"Setor {setor_id} não encontrado")
    try:
        clima = await consultar_clima(setor["latitude"], setor["longitude"])
    except ClimaIndisponivel as e:
        raise ApiError(502, "Webservice de clima (Open-Meteo) indisponível", str(e))
    return responder(request, {"setor_id": setor_id, "setor": setor["nome"],
                               "latitude": setor["latitude"], "longitude": setor["longitude"],
                               "clima": clima})


# ======================================================================
# SENSORES  (cadastro = envio de dados com status; busca por chave; lista com filtro)
# ======================================================================

@app.get("/api/v1/sensores", tags=["Sensores"], responses=RESPOSTAS_JSON_XML,
         summary="Lista sensores com filtros (setor, tipo, ativo, em alarme, palavra-chave)")
async def listar_sensores(
    request: Request,
    setor_id: int | None = None,
    tipo: TipoSensor | None = None,
    ativo: bool | None = None,
    em_alarme: bool | None = Query(None, description="Somente sensores cuja última leitura está (ou não) em alarme"),
    busca: str | None = Query(None, description="Palavra-chave no código ou descrição"),
    formato: Formato = None,
):
    where, params = [], []
    if setor_id is not None:
        where.append("s.setor_id = ?"); params.append(setor_id)
    if tipo is not None:
        where.append("s.tipo = ?"); params.append(tipo)
    if ativo is not None:
        where.append("s.ativo = ?"); params.append(int(ativo))
    if em_alarme is not None:
        where.append("COALESCE(l.alarme, 0) = ?"); params.append(int(em_alarme))
    if busca:
        where.append("(s.codigo LIKE ? OR s.descricao LIKE ?)"); params += [f"%{busca}%"] * 2
    sql = SQL_SENSORES + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY s.codigo"
    with db.conectar() as conn:
        sensores = [_sensor_dict(r) for r in conn.execute(sql, params)]
    return responder(request, {"total": len(sensores), "sensores": sensores})


@app.get("/api/v1/sensores/{codigo}", tags=["Sensores"], responses=RESPOSTAS_JSON_XML,
         summary="Busca um sensor pelo código (chave) com suas últimas leituras")
async def buscar_sensor(codigo: str, request: Request, formato: Formato = None):
    with db.conectar() as conn:
        r = _buscar_sensor(conn, codigo)
        if r is None:
            raise ApiError(404, f"Sensor {codigo.upper()} não encontrado")
        sensor = _sensor_dict(r)
        sensor["ultimas_leituras"] = [_leitura_dict(x) for x in conn.execute(
            "SELECT * FROM leituras WHERE sensor_codigo = ? ORDER BY registrado_em DESC, id DESC LIMIT 10",
            (r["codigo"],))]
    return responder(request, sensor)


@app.post("/api/v1/sensores", tags=["Sensores"], status_code=201,
          openapi_extra=corpo_openapi(SensorCriar, "sensor"),
          summary="Inclui um novo sensor (retorna status da operação)")
async def incluir_sensor(request: Request, formato: Formato = None):
    dados = await ler_corpo(request, SensorCriar)
    codigo = dados.codigo.upper()
    unidade, limite_padrao = db.PADROES_SENSOR[dados.tipo]
    with db.conectar() as conn:
        if not _setor_existe(conn, dados.setor_id):
            raise ApiError(422, f"Setor {dados.setor_id} não existe", status="falha")
        if _buscar_sensor(conn, codigo):
            raise ApiError(409, f"Já existe um sensor com o código {codigo}", status="falha")
        conn.execute(
            "INSERT INTO sensores (codigo, tipo, setor_id, descricao, unidade, limite_alarme)"
            " VALUES (?,?,?,?,?,?)",
            (codigo, dados.tipo, dados.setor_id, dados.descricao, unidade,
             dados.limite_alarme if dados.limite_alarme is not None else limite_padrao),
        )
        sensor = _sensor_dict(_buscar_sensor(conn, codigo))
    return responder(request, {"status": "sucesso", "mensagem": f"Sensor {codigo} incluído",
                               "sensor": sensor}, 201)


@app.put("/api/v1/sensores/{codigo}", tags=["Sensores"], responses=RESPOSTAS_JSON_XML,
         openapi_extra=corpo_openapi(SensorAlterar, "sensor"),
         summary="Altera dados de um sensor (retorna status da operação)")
async def alterar_sensor(codigo: str, request: Request, formato: Formato = None):
    dados = await ler_corpo(request, SensorAlterar)
    mudancas = dados.model_dump(exclude_unset=True)
    if not mudancas:
        raise ApiError(422, "Nenhum campo para alterar", status="falha")
    with db.conectar() as conn:
        if _buscar_sensor(conn, codigo) is None:
            raise ApiError(404, f"Sensor {codigo.upper()} não encontrado", status="falha")
        if "setor_id" in mudancas and not _setor_existe(conn, mudancas["setor_id"]):
            raise ApiError(422, f"Setor {mudancas['setor_id']} não existe", status="falha")
        if "ativo" in mudancas:
            mudancas["ativo"] = int(mudancas["ativo"])
        campos = ", ".join(f"{c} = ?" for c in mudancas)  # nomes vêm do modelo, não do usuário
        conn.execute(f"UPDATE sensores SET {campos} WHERE codigo = ?",
                     (*mudancas.values(), codigo.upper()))
        sensor = _sensor_dict(_buscar_sensor(conn, codigo))
    return responder(request, {"status": "sucesso", "mensagem": f"Sensor {sensor['codigo']} alterado",
                               "campos_alterados": list(mudancas), "sensor": sensor})


@app.delete("/api/v1/sensores/{codigo}", tags=["Sensores"], responses=RESPOSTAS_JSON_XML,
            summary="Exclui um sensor e suas leituras")
async def excluir_sensor(codigo: str, request: Request, formato: Formato = None):
    with db.conectar() as conn:
        cur = conn.execute("DELETE FROM sensores WHERE codigo = ?", (codigo.upper(),))
    if cur.rowcount == 0:
        raise ApiError(404, f"Sensor {codigo.upper()} não encontrado", status="falha")
    return responder(request, {"status": "sucesso", "mensagem": f"Sensor {codigo.upper()} excluído"})


# ======================================================================
# LEITURAS  (envio de dado do sensor -> status + acionamento automático)
# ======================================================================

@app.post("/api/v1/leituras", tags=["Leituras"], status_code=201,
          openapi_extra=corpo_openapi(LeituraCriar, "leitura"),
          summary="Envia a leitura de um sensor; dispara alarme e atuadores se passar do limite")
async def registrar_leitura(request: Request, formato: Formato = None):
    dados = await ler_corpo(request, LeituraCriar)
    with db.conectar() as conn:
        sensor = _buscar_sensor(conn, dados.sensor_codigo)
        if sensor is None:
            raise ApiError(404, f"Sensor {dados.sensor_codigo.upper()} não encontrado", status="falha")
        if not sensor["ativo"]:
            raise ApiError(409, f"Sensor {sensor['codigo']} está inativo; leitura rejeitada", status="falha")
        alarme = dados.valor >= sensor["limite_alarme"]
        cur = conn.execute(
            "INSERT INTO leituras (sensor_codigo, valor, alarme, registrado_em) VALUES (?,?,?,?)",
            (sensor["codigo"], dados.valor, int(alarme), db.agora()),
        )
        leitura = _leitura_dict(conn.execute("SELECT * FROM leituras WHERE id = ?", (cur.lastrowid,)).fetchone())
        acoes = _acionar_automatico(conn, sensor["setor_id"], sensor["tipo"], sensor["codigo"]) if alarme else []
    msg = "Leitura registrada"
    if alarme:
        msg += f" - ALARME: {dados.valor} {sensor['unidade']} >= limite {sensor['limite_alarme']}"
    return responder(request, {"status": "sucesso", "mensagem": msg, "alarme": alarme,
                               "leitura": leitura, "acoes_automaticas": acoes}, 201)


@app.get("/api/v1/leituras", tags=["Leituras"], responses=RESPOSTAS_JSON_XML,
         summary="Lista leituras com filtros (sensor, setor, tipo, alarme, período)")
async def listar_leituras(
    request: Request,
    sensor: str | None = Query(None, description="Código do sensor"),
    setor_id: int | None = None,
    tipo: TipoSensor | None = None,
    alarme: bool | None = None,
    desde: str | None = Query(None, description="Data/hora ISO 8601 (UTC), ex.: 2026-11-10T00:00:00"),
    limite: int = Query(50, ge=1, le=500),
    formato: Formato = None,
):
    where, params = [], []
    if sensor:
        where.append("l.sensor_codigo = ?"); params.append(sensor.upper())
    if setor_id is not None:
        where.append("s.setor_id = ?"); params.append(setor_id)
    if tipo:
        where.append("s.tipo = ?"); params.append(tipo)
    if alarme is not None:
        where.append("l.alarme = ?"); params.append(int(alarme))
    if desde:
        where.append("l.registrado_em >= ?"); params.append(desde)
    sql = ("SELECT l.*, s.tipo, s.unidade, s.setor_id FROM leituras l "
           "JOIN sensores s ON s.codigo = l.sensor_codigo"
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY l.registrado_em DESC, l.id DESC LIMIT ?")
    with db.conectar() as conn:
        leituras = [_leitura_dict(r) for r in conn.execute(sql, (*params, limite))]
    return responder(request, {"total": len(leituras), "leituras": leituras})


# ======================================================================
# ATUADORES  (comando -> status bem/mal sucedido)
# ======================================================================

@app.get("/api/v1/atuadores", tags=["Atuadores"], responses=RESPOSTAS_JSON_XML,
         summary="Lista atuadores com filtros (setor, tipo, estado)")
async def listar_atuadores(
    request: Request,
    setor_id: int | None = None,
    tipo: TipoAtuador | None = None,
    estado: Literal["ligado", "desligado"] | None = None,
    formato: Formato = None,
):
    where, params = [], []
    if setor_id is not None:
        where.append("a.setor_id = ?"); params.append(setor_id)
    if tipo:
        where.append("a.tipo = ?"); params.append(tipo)
    if estado:
        where.append("a.estado = ?"); params.append(estado)
    sql = SQL_ATUADORES + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY a.codigo"
    with db.conectar() as conn:
        atuadores = [_atuador_dict(r) for r in conn.execute(sql, params)]
    return responder(request, {"total": len(atuadores), "atuadores": atuadores})


@app.get("/api/v1/atuadores/{codigo}", tags=["Atuadores"], responses=RESPOSTAS_JSON_XML,
         summary="Busca um atuador pelo código (chave) com o histórico de comandos")
async def buscar_atuador(codigo: str, request: Request, formato: Formato = None):
    with db.conectar() as conn:
        r = _buscar_atuador(conn, codigo)
        if r is None:
            raise ApiError(404, f"Atuador {codigo.upper()} não encontrado")
        atuador = _atuador_dict(r)
        atuador["ultimos_comandos"] = [dict(c) for c in conn.execute(
            "SELECT * FROM comandos WHERE atuador_codigo = ? ORDER BY id DESC LIMIT 10", (r["codigo"],))]
    return responder(request, atuador)


@app.post("/api/v1/atuadores/{codigo}/comando", tags=["Atuadores"],
          openapi_extra=corpo_openapi(ComandoAtuador, "comando"),
          responses=RESPOSTAS_JSON_XML,
          summary="Envia comando (ligar/desligar) a um atuador e retorna o status da operação")
async def comandar_atuador(codigo: str, request: Request, formato: Formato = None):
    dados = await ler_corpo(request, ComandoAtuador)
    codigo = codigo.upper()
    with db.conectar() as conn:
        atuador = _buscar_atuador(conn, codigo)
        # Falhas retornam (sem exceção) para que o registro no log de comandos seja gravado
        if atuador is None:
            _registrar_comando(conn, codigo, dados.comando, "api", "falha", "Atuador inexistente")
            return responder(request, {"status": "falha",
                                       "mensagem": f"Atuador {codigo} não encontrado"}, 404)

        novo_estado = "ligado" if dados.comando == "ligar" else "desligado"
        if atuador["estado"] == novo_estado:
            _registrar_comando(conn, codigo, dados.comando, "api", "sem_alteracao", "Já estava " + novo_estado)
            return responder(request, {"status": "sem_alteracao",
                                       "mensagem": f"Atuador {codigo} já está {novo_estado}",
                                       "atuador": _atuador_dict(atuador)})

        if (dados.comando == "desligar" and atuador["tipo"] == "sprinkler" and not dados.forcar):
            em_alarme = _setor_em_alarme(conn, atuador["setor_id"])
            if em_alarme:
                msg = "Setor em alarme; não é permitido desligar o sprinkler (use forcar=true)"
                _registrar_comando(conn, codigo, dados.comando, "api", "falha", msg)
                return responder(request, {"status": "falha", "mensagem": msg,
                                           "sensores_em_alarme": em_alarme}, 409)

        conn.execute("UPDATE atuadores SET estado = ?, atualizado_em = ? WHERE codigo = ?",
                     (novo_estado, db.agora(), codigo))
        _registrar_comando(conn, codigo, dados.comando, "api", "sucesso", None)
        atualizado = _atuador_dict(_buscar_atuador(conn, codigo))
    return responder(request, {"status": "sucesso",
                               "mensagem": f"Comando '{dados.comando}' executado em {codigo}",
                               "estado_anterior": atuador["estado"], "atuador": atualizado})
