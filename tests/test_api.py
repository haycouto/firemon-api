import os
import tempfile
import xml.etree.ElementTree as ET

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "teste.db")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402


@pytest.fixture(scope="module")
def cli():
    with TestClient(main.app) as c:
        yield c


def test_status(cli):
    r = cli.get("/api/v1/status")
    assert r.status_code == 200 and r.json()["status"] == "online"


def test_status_xml_por_parametro_e_accept(cli):
    r1 = cli.get("/api/v1/status?formato=xml")
    r2 = cli.get("/api/v1/status", headers={"Accept": "application/xml"})
    for r in (r1, r2):
        assert r.headers["content-type"].startswith("application/xml")
        assert ET.fromstring(r.content).findtext("status") == "online"


# ---- item 3: busca por chave ----
def test_busca_sensor_por_codigo(cli):
    r = cli.get("/api/v1/sensores/st-ct01")
    assert r.status_code == 200
    d = r.json()
    assert d["codigo"] == "ST-CT01" and len(d["ultimas_leituras"]) == 3


def test_busca_sensor_inexistente(cli):
    r = cli.get("/api/v1/sensores/XX-000")
    assert r.status_code == 404 and r.json()["status"] == "erro"


# ---- item 5: lista com filtro ----
def test_lista_sensores_filtrada(cli):
    d = cli.get("/api/v1/sensores?setor_id=2&tipo=temperatura").json()
    assert d["total"] == 1 and d["sensores"][0]["codigo"] == "ST-CT01"
    d = cli.get("/api/v1/sensores?busca=data center").json()
    assert {s["codigo"] for s in d["sensores"]} == {"SF-CP01", "ST-CP01"}


# ---- item 2: envio de dados / comando com status ----
def test_inclusao_alteracao_exclusao_sensor_json_e_xml(cli):
    xml = "<sensor><codigo>SF-SP99</codigo><tipo>fumaca</tipo><setor_id>1</setor_id></sensor>"
    r = cli.post("/api/v1/sensores", content=xml,
                 headers={"Content-Type": "application/xml", "Accept": "application/xml"})
    assert r.status_code == 201
    assert ET.fromstring(r.content).findtext("status") == "sucesso"

    r = cli.post("/api/v1/sensores", json={"codigo": "SF-SP99", "tipo": "fumaca", "setor_id": 1})
    assert r.status_code == 409 and r.json()["status"] == "falha"

    r = cli.put("/api/v1/sensores/SF-SP99", json={"limite_alarme": 8, "ativo": False})
    assert r.status_code == 200 and r.json()["sensor"]["limite_alarme"] == 8

    r = cli.post("/api/v1/leituras", json={"sensor_codigo": "SF-SP99", "valor": 1})
    assert r.status_code == 409  # sensor inativo

    assert cli.delete("/api/v1/sensores/SF-SP99").json()["status"] == "sucesso"
    assert cli.delete("/api/v1/sensores/SF-SP99").status_code == 404


def test_dados_invalidos(cli):
    r = cli.post("/api/v1/sensores", json={"codigo": "X", "tipo": "radiacao", "setor_id": 1})
    assert r.status_code == 422 and len(r.json()["detalhes"]) == 2
    r = cli.post("/api/v1/leituras", content="<leitura><valor>", headers={"Content-Type": "application/xml"})
    assert r.status_code == 400


def test_leitura_alarme_aciona_atuadores_e_bloqueia_sprinkler(cli):
    r = cli.post("/api/v1/leituras", json={"sensor_codigo": "ST-CT01", "valor": 80})
    d = r.json()
    assert r.status_code == 201 and d["alarme"] is True
    assert {a["atuador"] for a in d["acoes_automaticas"]} == {"SR-CT01", "SP-CT01"}

    alarmes = cli.get("/api/v1/leituras?alarme=true&setor_id=2").json()
    assert alarmes["total"] == 1

    r = cli.post("/api/v1/atuadores/SP-CT01/comando", json={"comando": "desligar"})
    assert r.status_code == 409 and r.json()["status"] == "falha"

    r = cli.post("/api/v1/atuadores/SP-CT01/comando", json={"comando": "ligar"})
    assert r.json()["status"] == "sem_alteracao"

    cli.post("/api/v1/leituras", json={"sensor_codigo": "ST-CT01", "valor": 30})
    r = cli.post("/api/v1/atuadores/SP-CT01/comando", json={"comando": "desligar"})
    assert r.status_code == 200 and r.json()["status"] == "sucesso"

    hist = cli.get("/api/v1/atuadores/SP-CT01").json()["ultimos_comandos"]
    assert [c["resultado"] for c in hist[:4]] == ["sucesso", "sem_alteracao", "falha", "sucesso"]


def test_comando_atuador_inexistente(cli):
    r = cli.post("/api/v1/atuadores/ZZ-01/comando", json={"comando": "ligar"})
    assert r.status_code == 404 and r.json()["status"] == "falha"


# ---- item 4: webservice de terceiro (simulado no teste) ----
def test_clima(cli, monkeypatch):
    async def falso(lat, lon):
        return {"temperatura_c": 33, "umidade_relativa": 25, "indice_angstrom": 0.65,
                "risco_incendio": "muito_alto"}
    monkeypatch.setattr(main, "consultar_clima", falso)
    d = cli.get("/api/v1/setores/1/clima").json()
    assert d["clima"]["risco_incendio"] == "muito_alto"
    assert cli.get("/api/v1/setores/99/clima").status_code == 404


def test_indice_angstrom():
    from app.clima import indice_angstrom
    assert indice_angstrom(20, 80) == (4.7, "baixo")
    assert indice_angstrom(35, 20) == (0.2, "muito_alto")
