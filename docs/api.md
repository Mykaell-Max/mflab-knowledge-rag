# API RAG local

## Escopo inicial

O serviço HTTP oferece recuperação e geração citável sobre o corpus PostgreSQL
já indexado. Ele não atualiza fontes, não dispara indexação e não modifica os
repositórios científicos. A geração é opcional e ocorre somente por um servidor
LLM local configurado separadamente.

Por segurança, a versão sem autenticação aceita apenas `127.0.0.1`, `::1` ou
`localhost`. A demonstração na rede do laboratório pode usar uma chave Bearer
compartilhada forte; identidade individual, HTTPS, grupos e auditoria continuam
obrigatórios antes de um uso institucional.

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

## Interface web e acesso pela LAN

A interface responsiva é servida em `/ui` pela mesma origem da API. A área de
uso começa em **Perguntar** e também oferece **Buscar**; ela não exibe métricas
operacionais. Nenhum nome de repositório ou branch está fixado: os filtros vêm
do catálogo atual do banco.

Na rede confiável do laboratório, a interface usa rotas próprias e somente
leitura sob `/ui-api`. Busca e geração ficam disponíveis sem distribuir a chave
técnica da API. A fronteira de acesso dessa área é a rede local: a porta deve
continuar liberada somente para a sub-rede autorizada. Os endpoints normais
`/repositories`, `/search`, `/context` e `/ask` preservam a autenticação Bearer
existente para integrações programáticas externas ao servidor. As rotas web
aceitam somente as classes `public` e `lab`, ainda que o processo seja iniciado
com um teto adicional para integrações autenticadas.

Detalhes da máquina, PostgreSQL, embeddings, gerador, indexador e repositórios
ficam em **Administração**. Configure no `.env` uma senha exclusiva com pelo
menos 12 caracteres e reinicie a API:

```dotenv
MFLAB_ADMIN_PASSWORD=uma-senha-local-forte
```

A senha é enviada somente no login administrativo. O servidor cria uma sessão
aleatória em cookie `HttpOnly`, `SameSite=Strict`, válida por oito horas e
perdida quando o processo reinicia. Cinco falhas do mesmo endereço em cinco
minutos suspendem temporariamente novas tentativas. A senha e o cookie não são
incluídos nas respostas, no JavaScript ou no repositório.

O modo seguro padrão continua loopback. Para preparar a chave sem exibi-la:

```bash
.venv/bin/python -m mflab_knowledge api-key-init --env-file .env
```

Ao usar `--host 0.0.0.0`, `MFLAB_API_KEY` passa a ser obrigatória para os
endpoints programáticos. Requisições vindas de fora do loopback devem enviar
`Authorization: Bearer <chave>`; `/health`, os arquivos estáticos e as rotas de
uso da interface permanecem públicos dentro da rede autorizada. Não coloque a
chave em URL, bookmark, log, JavaScript ou arquivo versionado.

Chamadas originadas no próprio servidor são aceitas sem a chave, preservando o
timer, as avaliações e os comandos administrativos existentes. Essa exceção
pressupõe acesso direto ao Uvicorn; um proxy reverso exigirá uma política própria
e não deve ser introduzido sem revisar a fronteira de confiança.

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
API. `provider.max_context_characters` define o teto de evidência enviado ao
modelo e possui padrão seguro de 8.000 caracteres; ele pode ser ajustado à
janela efetiva do servidor sem alterar código ou interface.

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
da carga. `branch_names` contém todas as branches visíveis, enquanto
`preferred_branch`, `canonical_branches`, `aliases` e `preference_status`
descrevem a política segura de navegação. A lista e suas contagens consideram
somente as classes liberadas pelo processo. Nenhum nome de fonte é codificado
no endpoint.

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

Quando `project` e `branch` não são enviados, o resolvedor consulta o catálogo
local. Nomes ou aliases de projetos mencionados explicitamente usam a branch
preferencial correspondente; branches entre aspas, após `branch`/`ref` ou com
nomes estruturados como `grupo/recurso` também podem formar o escopo. Duas
fontes mencionadas produzem buscas separadas, intercaladas antes da montagem do
contexto. Se nada for mencionado, cada repositório contribui por sua branch
preferencial. `scope_resolution` informa exatamente a decisão. Filtros
estruturados enviados pelo cliente continuam tendo precedência e são estritos.

As classes solicitadas precisam ser um subconjunto do teto definido quando o
servidor iniciou. A classe `project` também exige o filtro `project`; `pending`
nunca é recuperável.

### `POST /context`

Aceita os mesmos filtros de `/search` e acrescenta
`max_context_characters`, entre 1.000 e 100.000. O padrão é 24.000. A resposta
atribui IDs `S1`, `S2` e assim por diante às evidências, remove hashes internos e
informa explicitamente se alguma fonte ou o conjunto foi truncado pelo
orçamento.

Perguntas reconhecidas como visão geral recebem um plano de exploração
determinístico e limitado. `exploration` informa o tipo de intenção, as
consultas auxiliares e se todos os escopos precisam aparecer na resposta. As
fontes são balanceadas por projeto e branch, com preferência por documentos de
entrada e artefatos arquiteturais amplos. Nenhum termo científico é codificado
nessa classificação.

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
sem chamar o modelo. O orçamento solicitado nunca ultrapassa
`provider.max_context_characters`. Se um provedor OpenAI-compatible ainda
recusar a janela, o backend reduz o pacote de evidências preservando sua ordem e
IDs, e tenta novamente até duas vezes. Os campos `generation_attempts` e
`reduced_for_generation` tornam esse comportamento observável na resposta.
Para visões gerais com mais de um escopo, omitir um projeto ou branch produz
`incomplete_scope_coverage` e aciona no máximo uma revisão automática. O campo
`quality_retry` registra essa revisão.

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

A resposta inclui `answer`, `citations_used`, `invalid_citations`, `sources`,
`scopes`, `citation_coverage` e `scope_citation_coverage`. A cobertura é uma
verificação estrutural por
parágrafo ou bullet factual; ela não substitui uma avaliação semântica humana
de que a fonte realmente sustenta a afirmação. `grounding_status` vale `cited`,
`partial_citations`, `incomplete_scope_coverage`, `missing_citations`,
`invalid_citations` ou `no_sources`.
`scope_warning` fica verdadeiro quando as fontes abrangem mais de uma
combinação projeto/branch/commit; isso não bloqueia uma comparação intencional,
mas impede que o cliente trate versões distintas como se fossem uma só. O
texto integral das fontes não é repetido na resposta de `/ask`.

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

A interface de consulta está disponível em `http://127.0.0.1:8765/ui`. A
interface OpenAPI/Swagger fica em `http://127.0.0.1:8765/docs` durante a
execução; quando a chave está configurada, os endpoints administrativos fora do
loopback exigem o Bearer.

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
max_context_characters = 8000
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

Para habilitar a demonstração na LAN:

```bash
./scripts/install-api-systemd.sh \
  --project-dir "$PWD" \
  --user "$USER" \
  --group "$(id -gn)" \
  --host 0.0.0.0 \
  --port 8765
```

O instalador gera a chave somente se ela ainda não existir. A porta deve ser
liberada no firewall apenas para a sub-rede confiável do laboratório. O segredo
compartilhado serve para o piloto demonstrável, não como autenticação definitiva.

Comandos administrativos:

```bash
systemctl status mflab-knowledge-api.service --no-pager
journalctl -u mflab-knowledge-api.service --since today
sudo systemctl restart mflab-knowledge-api.service
```

Sem `--host`, o serviço continua preso a `127.0.0.1`. Mudar apenas a porta não
altera essa restrição.

## Avaliação ponta a ponta

`api-evaluate` envia uma suíte JSON versionada ao `/ask` local e valida por
caso: abstinência, status de grounding, número de fontes e citações, cobertura
estrutural, motivo de término, escopos, caminhos obrigatórios e limites de
latência. O monitor opcional consulta `nvidia-smi` durante cada requisição e
registra picos; sua ausência não impede a avaliação funcional.

Além das expectativas científicas declaradas, o avaliador verifica
automaticamente o contrato dos filtros enviados em cada caso. Toda fonte
devolvida deve respeitar o projeto, a branch, o prefixo de caminho e as classes
de acesso solicitadas. Essa verificação é genérica e detecta mistura indevida
entre repositórios ou versões sem conhecer nomes de projetos no motor.

```bash
.venv/bin/python -m mflab_knowledge api-evaluate \
  --suite evaluations/mfsim-ng-answer-pilot.json \
  --api-base-url http://127.0.0.1:8765 \
  --output data/mfsim-ng-answer-evaluation.generated.json \
  --color always
```

O comando retorna `0` quando todos os casos passam, `1` quando a API respondeu
mas alguma expectativa de qualidade foi reprovada e `2` para erro operacional
(por exemplo, API indisponível ou relatório inválido). Em reprovações, os logs
mostram as verificações, valores observados, estado de grounding e caminhos das
fontes; a resposta completa permanece no relatório JSON indicado por `--output`.

O endpoint deve ser loopback literal e redirecionamentos são recusados. O
relatório gerado permanece em `data/`, fora do Git, pois contém respostas e
metadados das fontes. A suíte versionada contém apenas perguntas, filtros e
expectativas deliberadamente revisadas; adicionar outro projeto não exige
alterar o avaliador.
