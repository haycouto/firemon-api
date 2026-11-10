"""Camada de banco de dados (SQLite - banco relacional).

O caminho do arquivo pode ser alterado pela variável de ambiente DB_PATH.
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "firemon.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS setores (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    nome       TEXT    NOT NULL UNIQUE,
    descricao  TEXT,
    latitude   REAL    NOT NULL,
    longitude  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS sensores (
    codigo        TEXT    PRIMARY KEY,
    tipo          TEXT    NOT NULL CHECK (tipo IN ('fumaca','temperatura','gas','chama')),
    setor_id      INTEGER NOT NULL REFERENCES setores(id),
    descricao     TEXT,
    unidade       TEXT    NOT NULL,
    limite_alarme REAL    NOT NULL,
    ativo         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS leituras (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sensor_codigo TEXT    NOT NULL REFERENCES sensores(codigo) ON DELETE CASCADE,
    valor         REAL    NOT NULL,
    alarme        INTEGER NOT NULL,
    registrado_em TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_leituras_sensor ON leituras(sensor_codigo, registrado_em);

CREATE TABLE IF NOT EXISTS atuadores (
    codigo        TEXT    PRIMARY KEY,
    tipo          TEXT    NOT NULL CHECK (tipo IN ('sirene','sprinkler','exaustor')),
    setor_id      INTEGER NOT NULL REFERENCES setores(id),
    descricao     TEXT,
    estado        TEXT    NOT NULL DEFAULT 'desligado' CHECK (estado IN ('ligado','desligado')),
    atualizado_em TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS comandos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    atuador_codigo TEXT   NOT NULL,
    comando       TEXT    NOT NULL,
    origem        TEXT    NOT NULL,
    resultado     TEXT    NOT NULL,
    mensagem      TEXT,
    executado_em  TEXT    NOT NULL
);
"""

# Unidade e limite padrão de alarme por tipo de sensor
PADROES_SENSOR = {
    "fumaca": ("%obs/m", 10.0),      # obscurecimento por metro
    "temperatura": ("°C", 57.0),     # detector térmico de temperatura fixa
    "gas": ("ppm", 50.0),            # monóxido de carbono
    "chama": ("%", 50.0),            # intensidade de radiação IR/UV
}


def agora(delta_min: int = 0) -> str:
    t = datetime.now(timezone.utc) - timedelta(minutes=delta_min)
    return t.isoformat(timespec="seconds")


@contextmanager
def conectar():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def inicializar():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with conectar() as conn:
        conn.executescript(SCHEMA)
        vazio = conn.execute("SELECT COUNT(*) FROM setores").fetchone()[0] == 0
        if vazio:
            _popular(conn)


def _popular(conn: sqlite3.Connection):
    setores = [
        ("Galpão São Paulo", "Armazenagem de produtos inflamáveis", -23.5505, -46.6333),
        ("Fábrica Curitiba", "Linha de produção e caldeiras", -25.4284, -49.2733),
        ("Escritório Campinas", "Área administrativa e data center", -22.9056, -47.0608),
    ]
    conn.executemany(
        "INSERT INTO setores (nome, descricao, latitude, longitude) VALUES (?,?,?,?)", setores
    )

    sensores = [
        ("SF-SP01", "fumaca", 1, "Fumaça - corredor A"),
        ("ST-SP01", "temperatura", 1, "Temperatura - doca de carga"),
        ("SG-SP01", "gas", 1, "CO - área de empilhadeiras"),
        ("SF-CT01", "fumaca", 2, "Fumaça - linha 1"),
        ("ST-CT01", "temperatura", 2, "Temperatura - casa de caldeiras"),
        ("SC-CT01", "chama", 2, "Chama - tanque de solvente"),
        ("SF-CP01", "fumaca", 3, "Fumaça - data center"),
        ("ST-CP01", "temperatura", 3, "Temperatura - data center"),
    ]
    for codigo, tipo, setor, desc in sensores:
        unidade, limite = PADROES_SENSOR[tipo]
        conn.execute(
            "INSERT INTO sensores (codigo, tipo, setor_id, descricao, unidade, limite_alarme)"
            " VALUES (?,?,?,?,?,?)",
            (codigo, tipo, setor, desc, unidade, limite),
        )

    atuadores = [
        ("SR-SP01", "sirene", 1, "Sirene geral"),
        ("SP-SP01", "sprinkler", 1, "Válvula de sprinklers - zona 1"),
        ("EX-SP01", "exaustor", 1, "Exaustor de fumaça"),
        ("SR-CT01", "sirene", 2, "Sirene geral"),
        ("SP-CT01", "sprinkler", 2, "Válvula de sprinklers - caldeiras"),
        ("SR-CP01", "sirene", 3, "Sirene geral"),
        ("EX-CP01", "exaustor", 3, "Exaustor do data center"),
    ]
    for codigo, tipo, setor, desc in atuadores:
        conn.execute(
            "INSERT INTO atuadores (codigo, tipo, setor_id, descricao, atualizado_em)"
            " VALUES (?,?,?,?,?)",
            (codigo, tipo, setor, desc, agora()),
        )

    # Leituras históricas normais (sem alarme)
    normais = {"fumaca": 1.2, "temperatura": 24.0, "gas": 8.0, "chama": 3.0}
    for codigo, tipo, _, _ in sensores:
        for i, min_atras in enumerate((30, 20, 10)):
            valor = round(normais[tipo] * (1 + 0.05 * i), 2)
            conn.execute(
                "INSERT INTO leituras (sensor_codigo, valor, alarme, registrado_em) VALUES (?,?,0,?)",
                (codigo, valor, agora(min_atras)),
            )
