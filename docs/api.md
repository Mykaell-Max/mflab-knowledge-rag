# API RAG local

## Escopo inicial

O serviço HTTP oferece recuperação citável sobre o corpus PostgreSQL já
indexado. Ele não atualiza fontes, não dispara indexação, não modifica os
repositórios científicos e ainda não gera respostas com um LLM.

Por segurança, a versão sem autenticação aceita apenas `127.0.0.1`, `::1` ou
`localhost`. A publicação na rede do laboratório exige primeiro identidade,
autorização e auditoria.

## Execução

```bash
.venv/bin/python -m pip install -e '.[postgres,embeddings,service]'

.venv/bin/python -m mflab_knowledge serve \
  --env-file .env \
  --state-dir state \
  --host 127.0.0.1 \
  --port 8765 \
  --color always
```

O processo usa `retrieval.toml` quando o arquivo existe. Outro arquivo pode ser
selecionado com `--retrieval-config`. O teto padrão de acesso é `public` e `lab`;
`--allow-access` pode ser repetido para definir outro teto operacional.

## Endpoints

### `GET /health`

Confirma processo e conectividade com o banco. Retorna `503` quando o PostgreSQL
não está disponível e nunca inclui a URL ou a causa interna da conexão.

### `GET /status`

Retorna contagens do banco, perfis de embeddings, estado da última indexação
agendada e se o modelo já foi carregado no processo HTTP.

### `GET /repositories`

Lista dinamicamente os repositórios presentes no banco, com documentos,
ocorrências, branches, branch canônica, chunks, cobertura de embeddings e data
da carga. A lista e suas contagens consideram somente as classes liberadas pelo
processo. Nenhum nome de fonte é codificado no endpoint.

### `POST /search`

Exemplo híbrido:

```json
{
  "query": "como as partículas recebem IDs distribuídos?",
  "mode": "hybrid",
  "limit": 5,
  "project": "MFSim-NG",
  "branch": "diagnostic/dpm",
  "allowed_access": ["lab"]
}
```

`mode` aceita `lexical`, `semantic` e `hybrid`. `project`, `branch` e
`path_prefix` são opcionais e não possuem valores predefinidos. Cada resultado
preserva projeto, branch, commit, caminho, linhas, classe de acesso, texto e
citação.

As classes solicitadas precisam ser um subconjunto do teto definido quando o
servidor iniciou. A classe `project` também exige o filtro `project`; `pending`
nunca é recuperável.

### `POST /context`

Aceita os mesmos filtros de `/search` e acrescenta
`max_context_characters`, entre 1.000 e 100.000. O padrão é 24.000. A resposta
atribui IDs `S1`, `S2` e assim por diante às evidências, remove hashes internos e
informa explicitamente se alguma fonte ou o conjunto foi truncado pelo
orçamento.

```json
{
  "query": "how are distributed particle identifiers generated?",
  "mode": "hybrid",
  "limit": 10,
  "allowed_access": ["lab"],
  "max_context_characters": 24000
}
```

O pacote inclui uma instrução estável para que o consumidor trate o conteúdo
recuperado como evidência não confiável, nunca como comandos; cite afirmações
com `[S1]`; preserve branch e commit; e declare insuficiência quando as fontes
não sustentarem uma resposta. Essa instrução não substitui isolamento ou ACL,
mas estabelece o contrato compartilhado do futuro `/ask` e MCP.

## Limites operacionais

- consulta: até 2.000 caracteres;
- resultados: de 1 a 50;
- chunks por caminho: de 1 a 20;
- contexto: de 1.000 a 100.000 caracteres de evidência;
- um worker HTTP por processo;
- buscas que usam o modelo local são serializadas;
- o corpo das requisições não é escrito nos logs de acesso do Uvicorn.

A interface OpenAPI/Swagger está disponível em `http://127.0.0.1:8765/docs`
durante a execução.

## Serviço permanente

Após validar o processo em primeiro plano, instale a unidade `systemd`:

```bash
./scripts/install-api-systemd.sh \
  --project-dir "$PWD" \
  --user "$USER" \
  --group "$(id -gn)" \
  --port 8765
```

O instalador verifica dependências, placeholders e sintaxe da unidade antes de
usar `sudo`. Em seguida, habilita e reinicia `mflab-knowledge-api.service`,
espera `/health` responder e mostra o estado final. Uma reinstalação com outra
porta substitui somente essa unidade e reinicia a API.

Comandos administrativos:

```bash
systemctl status mflab-knowledge-api.service --no-pager
journalctl -u mflab-knowledge-api.service --since today
sudo systemctl restart mflab-knowledge-api.service
```

O serviço continua preso a `127.0.0.1`. Acesso por outra máquina deverá passar
posteriormente por um proxy autenticado; mudar apenas a porta não altera essa
restrição.
