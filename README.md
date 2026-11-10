# FireMon API – Webservices para monitoramento de incêndio

Trabalho final da disciplina **CAC3024 – Integração de Sistemas para Automação**.

API REST (JSON **e** XML) que recebe leituras de sensores de incêndio (fumaça, temperatura,
gás CO e chama), dispara alarmes, comanda atuadores (sirene, sprinkler, exaustor) e consulta
o clima de cada unidade no webservice de terceiro **Open-Meteo** para calcular o risco de incêndio.

- Painel web: `/`
- Documentação interativa (Swagger/OpenAPI): `/docs`
- Especificação OpenAPI: `/openapi.json`

## Como os requisitos do trabalho são atendidos

| Requisito | Endpoint(s) |
|---|---|
| 2) Envio de dados/comando com status | `POST /api/v1/leituras`, `POST /api/v1/atuadores/{codigo}/comando`, `POST/PUT/DELETE /api/v1/sensores` → `status`: `sucesso`, `falha`, `sem_alteracao`, `erro` |
| 3) Busca por chave | `GET /api/v1/sensores/{codigo}`, `GET /api/v1/atuadores/{codigo}` |
| 4) Webservice de terceiro | `GET /api/v1/setores/{id}/clima` (consome Open-Meteo) |
| 5) Lista com filtro | `GET /api/v1/sensores?setor_id=&tipo=&ativo=&em_alarme=&busca=`, `GET /api/v1/leituras?sensor=&setor_id=&tipo=&alarme=&desde=&limite=`, `GET /api/v1/atuadores?setor_id=&tipo=&estado=` |
| 6) Banco de dados | SQLite (relacional): tabelas `setores`, `sensores`, `leituras`, `atuadores`, `comandos` |
| JSON e XML | Resposta em XML com `?formato=xml` ou `Accept: application/xml`; corpo em XML com `Content-Type: application/xml` |

## Executar localmente

```bash
pip install -r requirements.txt
```

```bash
python -m uvicorn app.main:app --reload --port 8000
```

Abra http://localhost:8000 (painel) e http://localhost:8000/docs (Swagger).

Testes automatizados:

```bash
python -m pytest -q
```

## Exemplos (curl)

```bash
curl https://SEU-SERVICO.onrender.com/api/v1/sensores/ST-CT01
```

```bash
curl "https://SEU-SERVICO.onrender.com/api/v1/sensores?setor_id=2&formato=xml"
```

```bash
curl -X POST https://SEU-SERVICO.onrender.com/api/v1/leituras -H "Content-Type: application/json" -d "{\"sensor_codigo\":\"ST-CT01\",\"valor\":72.5}"
```

```bash
curl -X POST https://SEU-SERVICO.onrender.com/api/v1/atuadores/SR-SP01/comando -H "Content-Type: application/xml" -H "Accept: application/xml" -d "<comando><comando>ligar</comando></comando>"
```

## Publicar na Internet (Render – gratuito)

1. Crie um repositório no GitHub e envie todos os arquivos desta pasta
   (pelo site: *Add file → Upload files*; a pasta `data/` não precisa ir).
2. Em https://render.com, faça login com o GitHub → **New → Blueprint** → escolha o repositório.
   O arquivo `render.yaml` já configura tudo (build, start e health check).
3. Após o deploy, a URL fica no formato `https://firemon-api-xxxx.onrender.com`.
   Teste `.../api/v1/status` e `.../docs`.

Observações do plano gratuito: o serviço "dorme" após 15 min sem acesso (o primeiro acesso
demora ~1 min) e o disco é temporário — ao reiniciar, o banco SQLite volta aos dados iniciais.
Para persistência definitiva, troque por PostgreSQL (o Render oferece um gratuito).

## Estrutura

```
app/
  main.py       rotas (endpoints) da API
  database.py   esquema SQLite e dados iniciais
  formatos.py   negociação JSON/XML e tratamento de erros
  clima.py      cliente do webservice Open-Meteo + índice de Angström
  static/index.html  painel web
tests/test_api.py    testes automatizados (pytest)
render.yaml          configuração de deploy
```
