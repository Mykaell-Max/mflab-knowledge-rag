# API RAG local

## Escopo inicial

O serviço HTTP oferece recuperação e geração citável sobre o corpus PostgreSQL
já indexado. Ele não atualiza fontes, não dispara indexação e não modifica os
repositórios científicos. A geração é opcional e ocorre somente por um servidor
LLM local configurado separadamente.

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

Para habilitar `/ask`, copie o exemplo e informe o endpoint e o identificador do
modelo que já estiver sendo servido localmente:

```bash
cp generation.example.toml generation.toml
```

`generation.toml` não é versionado. O adaptador aceita qualquer implementação
compatível com `POST /v1/chat/completions`, mas rejeita hosts que não sejam
literalmente `127.0.0.1` ou `::1`, URLs com credenciais e redirecionamentos. Se o
servidor local exigir chave, use `MFLAB_LLM_API_KEY` no `.env`; a chave nunca
entra no TOML nem nas respostas. Depois de alterar a configuração, reinicie a
API.

## Endpoints

### `GET /health`

Confirma processo e conectividade com o banco. Retorna `503` quando o PostgreSQL
não está disponível e nunca inclui a URL ou a causa interna da conexão.

### `GET /status`

Retorna contagens do banco, perfis de embeddings, estado da última indexação
agendada, se o modelo de embeddings já foi carregado e se um gerador local está
configurado. Não retorna URL nem chave do provedor.

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
com `[S1]`; preserve projeto, branch e commit; diferencie escopos misturados; e
declare insuficiência quando as fontes não sustentarem uma resposta. Essa
instrução não substitui isolamento ou ACL.

### `POST /ask`

Aceita os campos de `/context` e, opcionalmente, `max_output_tokens` (64 a
8.192) e `temperature` (0 a 1). A recuperação e a ACL ocorrem antes de qualquer
texto chegar ao gerador. Se nenhuma fonte for encontrada, o serviço se abstém
sem chamar o modelo.

```json
{
  "query": "how are distributed particle identifiers generated?",
  "mode": "hybrid",
  "limit": 10,
  "project": "MFSim-NG",
  "branch": "diagnostic/dpm",
  "allowed_access": ["lab"],
  "max_context_characters": 12000,
  "max_output_tokens": 700
}
```

A resposta inclui `answer`, `citations_used`, `invalid_citations`, `sources` e
`scopes`. `grounding_status` vale `cited`, `missing_citations`,
`invalid_citations` ou `no_sources`. `scope_warning` fica verdadeiro quando as
fontes abrangem mais de uma combinação projeto/branch/commit; isso não bloqueia
uma comparação intencional, mas impede que o cliente trate versões distintas
como se fossem uma só. O texto integral das fontes não é repetido na resposta
de `/ask`.

Sem `generation.toml`, `/search` e `/context` continuam funcionando, enquanto
`/ask` retorna `503` com uma orientação de configuração.

## Limites operacionais

- consulta: até 2.000 caracteres;
- resultados: de 1 a 50;
- chunks por caminho: de 1 a 20;
- contexto: de 1.000 a 100.000 caracteres de evidência;
- saída gerada: de 64 a 8.192 tokens;
- um worker HTTP por processo;
- buscas que usam o modelo local são serializadas;
- o corpo das requisições não é escrito nos logs de acesso do Uvicorn.

A interface OpenAPI/Swagger está disponível em `http://127.0.0.1:8765/docs`
durante a execução.

## Servidor LLM permanente

Depois de validar um modelo local em primeiro plano, instale o runtime vLLM e
o snapshot já existente como uma unidade independente:

```bash
./scripts/install-llm-systemd.sh \
  --project-dir "$PWD" \
  --user "$USER" \
  --group "$(id -gn)" \
  --vllm-python /caminho/do/runtime/bin/python \
  --model-path /caminho/do/snapshot-local \
  --served-model-name modelo-local \
  --port 8000 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.75 \
  --max-num-seqs 2 \
  --chat-template-kwargs '{}'
```

O script não baixa modelos nem instala pacotes. Todos os valores que dependem
da máquina são argumentos e o template versionado permanece genérico. A
unidade usa somente os arquivos locais, escuta em `127.0.0.1`, valida `/health`
e oferece métricas em `http://127.0.0.1:8000/metrics`.

Use o mesmo nome e porta em `generation.toml`:

```toml
schema_version = "0.1"

[provider]
kind = "openai_compatible"
base_url = "http://127.0.0.1:8000/v1"
model = "modelo-local"
timeout_seconds = 180
max_output_tokens = 1024
temperature = 0.1
```

Depois de criar ou alterar esse arquivo, reinicie a API. Comandos
administrativos do modelo:

```bash
systemctl status mflab-knowledge-llm.service --no-pager
journalctl -u mflab-knowledge-llm.service --since today
sudo systemctl restart mflab-knowledge-llm.service
```

## API permanente

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
