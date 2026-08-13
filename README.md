# MFLab Knowledge RAG

Serviço interno de inventário, indexação e RAG para fontes autorizadas do MFLab.

O projeto é separado do MFSim-NG: ele lê um clone ou snapshot fornecido por caminho, grava apenas em seu próprio armazenamento e nunca precisa alterar o solver.

## Estado atual

O piloto somente leitura já cobre inventário, sincronização multi-branch,
normalização, persistência no PostgreSQL e recuperação lexical,
semântica e híbrida.
Ele:

- descobre arquivos automaticamente;
- em clones Git, considera apenas arquivos versionados no commit atual;
- detecta branch e commit quando a fonte é um clone Git;
- calcula hashes para futuras atualizações incrementais;
- classifica formatos;
- exclui builds, documentação gerada, binários, resultados volumosos e possíveis segredos;
- gera um relatório YAML sem dependências Python externas.
- produz documentos e chunks JSONL com linhas e proveniência por branch/commit;
- deduplica versões idênticas compartilhadas entre branches;
- oferece uma busca lexical local com filtros de branch, caminho e acesso.
- carrega o mesmo corpus no PostgreSQL de forma transacional e idempotente;
- calcula embeddings incrementais com modelo local e armazena-os no pgvector;
- combina os rankings lexical e semântico por Reciprocal Rank Fusion (RRF);
- executa as mesmas suítes versionadas de regressão em cada modo.

A API RAG e os metadados colaborativos do GitLab ainda serão adicionados.

## Requisitos

- Python 3.11 ou superior;
- módulo `venv` correspondente ao Python do sistema;
- Git disponível no `PATH` para detectar branch e commit de clones reais.
- PostgreSQL local e o extra opcional `postgres` para os comandos `db-*`;
- pgvector e o extra `embeddings` somente para busca semântica/híbrida.

O inventário inicial não instala bibliotecas e não precisa acessar a rede.

## Uso no computador do laboratório

Crie um ambiente e instale o projeto localmente:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Gere o inventário apontando para o clone atualizado do MFSim-NG. Não é
necessário trocar a branch ativa: `--ref` seleciona a branch no mirror privado
do indexador.

```bash
mflab-knowledge inventory \
  --source /caminho/para/mfsim-ng \
  --ref master \
  --project MFSim-NG \
  --access-class lab \
  --profile auto \
  --output inventory/mfsim-ng.generated.yaml
```

Também é possível executar sem instalação:

```bash
PYTHONPATH=src python -m mflab_knowledge inventory \
  --source /caminho/para/mfsim-ng \
  --ref master \
  --project MFSim-NG \
  --access-class lab \
  --profile auto \
  --output inventory/mfsim-ng.generated.yaml
```

Para `MFSim-NG`, o perfil `auto` seleciona o piloto: código primário, CMake,
configurações, scripts, documentação Markdown original, análises derivadas e os
casos `dpm_ram*`. O restante continua visível como exclusão do catálogo, mas não
é preparado para embeddings.

O relatório gerado fica ignorado pelo Git porque pode conter caminhos e metadados internos.

## Isolamento da fonte

Para fontes Git, o modo padrão nunca inventaria diretamente o worktree indicado:

1. cria ou atualiza um mirror em `cache/repositories/` por meio de um bundle
   somente leitura;
2. resolve a branch, tag ou commit solicitado por `--ref`;
3. materializa apenas os arquivos versionados daquele commit com `git archive`;
4. inventaria o snapshot imutável em `cache/snapshots/`.

Arquivos modificados, builds e resultados não commitados na fonte não entram na
cópia. O comando não executa `checkout`, `fetch` nem qualquer escrita no
repositório científico. Snapshots iguais são reutilizados por commit.

Durante a execução, etapas e progresso são mostrados no terminal. Para automação
silenciosa, use `--quiet`; o resumo JSON continua sendo emitido em `stdout`.
Em um terminal interativo, os níveis `INFO`, `OK`, `AVISO`, `ERRO` e `RESULTADO`
recebem cores automaticamente. Pipes, redirecionamentos e serviços recebem texto
sem sequências ANSI. Use `--color always` para forçar, `--color never` para
desativar, ou a variável padrão `NO_COLOR` para desativação global.

## Sincronizar todas as branches

O comando `sync` atualiza o mirror diretamente do `origin`, descobre as branches
remotas e gera todos os inventários numa única execução. A branch ativa no clone
fornecido não importa e não é alterada.

```bash
PYTHONPATH=src python -m mflab_knowledge sync \
  --source /caminho/para/mfsim-ng \
  --project MFSim-NG \
  --canonical-ref origin/master \
  --branch-scope remote \
  --access-class lab \
  --profile auto \
  --cache-dir cache \
  --output-dir inventory/mfsim-ng
```

O resultado é organizado assim:

```text
inventory/mfsim-ng/
├── manifest.generated.yaml
├── branches.generated.txt
└── branches/
    ├── master.generated.yaml
    └── diagnostic/
        └── dpm.generated.yaml
```

`master` é marcada como canônica. As demais branches permanecem consultáveis,
mas separadas. Branches no mesmo commit reutilizam o inventário já calculado.
Para operar temporariamente sem consultar o GitLab, acrescente `--offline`.

Além dos snapshots Git, o comando mantém em `cache/inventories/` um cache
persistente do inventário de cada commit. A chave inclui repositório, projeto,
commit, perfil, classe de acesso e versões da política/schema. Assim, uma nova
execução sem mudanças não percorre novamente milhares de arquivos. O resumo
informa separadamente `inventories_built` e `inventories_reused`. Um commit novo
ou uma mudança de política causa somente os recálculos necessários; cache
ausente, incompatível ou corrompido é reconstruído automaticamente.

O acesso HTTPS ao remote usa credenciais locais protegidas. Tokens não devem ser
colocados na linha de comando, URL do repositório ou arquivos versionados deste
projeto.

### Credencial HTTPS somente leitura

Na primeira sincronização HTTPS, o comando cria `.env` vazio e encerra com uma
mensagem de configuração. Crie no GitLab um token com **somente** o escopo
`read_repository` e preencha:

```dotenv
MFLAB_GIT_USERNAME=seu_usuario_ou_usuario_do_deploy_token
MFLAB_GIT_READ_TOKEN=cole_o_token_aqui
```

O `.env` é ignorado pelo Git e criado com permissão `0600` no Linux. O token não
é acrescentado à URL, aos argumentos do processo, logs, manifests ou catálogos.
O fetch usa um `askpass` temporário, remove-o ao terminar e desativa prompts
interativos. As mesmas variáveis podem ser fornecidas diretamente pelo ambiente
de um serviço; elas têm precedência sobre o arquivo.

Para outro caminho, use `--env-file /caminho/protegido/mflab.env`. O modo
`--offline` não carrega nem exige credenciais.

## Normalizar e testar a busca

O `sync` também produz `manifest.generated.json` e catálogos JSON equivalentes
aos YAMLs, usados como contrato interno sem acrescentar uma dependência de YAML.
Após sincronizar, gere o corpus normalizado:

```bash
PYTHONPATH=src python -m mflab_knowledge normalize \
  --manifest inventory/mfsim-ng/manifest.generated.json \
  --cache-dir cache \
  --output-dir data/mfsim-ng \
  --color always
```

Os resultados são:

```text
data/mfsim-ng/
├── normalization.generated.json
├── documents.jsonl
└── chunks.jsonl
```

Cada documento é uma versão única identificada por repositório, caminho, hash e
classe de acesso. Suas `occurrences` preservam todas as branches e commits em que
ela existe. Os chunks guardam texto, título/símbolo heurístico, linhas, parser,
ACL e citações. Conteúdo textual idêntico usa a mesma `embedding_key`, preparando
a deduplicação dos embeddings.

O parser piloto respeita seções Markdown e reconhece âncoras básicas de
C/C++/headers, Fortran, CMake e shell; arquivos sem estrutura reconhecida usam
janelas por linha com sobreposição. Tree-sitter/Clang ainda será incorporado para
símbolos e relações de código exatas.

Teste uma busca lexical com citações:

```bash
PYTHONPATH=src python -m mflab_knowledge search \
  --chunks data/mfsim-ng/chunks.jsonl \
  --query DPMManager \
  --branch master \
  --limit 5 \
  --color always
```

Por padrão, a busca libera somente `public` e `lab`. Classes adicionais exigem
`--allow-access` explícito; `project` também exige `--project`. Conteúdo `pending`
nunca é recuperável. Esse filtro acontece antes de o texto ser retornado.

O ranking limita por padrão cada arquivo a dois chunks e elimina textos
idênticos, evitando que um caso ou lista repetitiva ocupe todos os resultados.
Use `--max-per-path 20` para investigar profundamente um arquivo e
`--include-duplicate-content` quando a auditoria exigir ocorrências textualmente
iguais. A frequência lexical usa crescimento logarítmico para que repetição
excessiva não domine sozinha o ranking.

### Avaliação de regressão

As consultas reais validadas no MFSim-NG estão registradas em
`evaluations/mfsim-ng-pilot.json`. Execute a suíte após normalizar o corpus:

```bash
PYTHONPATH=src python3 -m mflab_knowledge evaluate \
  --suite evaluations/mfsim-ng-pilot.json \
  --chunks data/mfsim-ng-zero-flow/chunks.jsonl \
  --output data/mfsim-ng-zero-flow/evaluation.generated.json \
  --color always
```

O relatório mede casos aprovados, recall das expectativas e MRR (posição do
primeiro resultado relevante). Cada expectativa fixa arquivo, título opcional e
posição máxima aceitável. O comando retorna código zero somente quando todos os
casos passam; assim, ele pode bloquear automaticamente uma mudança que piore a
recuperação. O relatório inclui hashes da suíte e do corpus, além de citações e
métricas, mas não inclui o texto dos chunks.

## Persistência e busca no PostgreSQL

O backend PostgreSQL é opcional: inventário, sincronização, normalização e busca
JSONL continuam sem dependências externas. Para habilitá-lo:

```bash
python3 -m pip install -e '.[postgres]'
```

O procedimento completo de instalação local está em
[`docs/postgresql.md`](docs/postgresql.md). Depois da configuração inicial, a
carga do corpus atual é:

```bash
PYTHONPATH=src python3 -m mflab_knowledge db-init --color always

PYTHONPATH=src python3 -m mflab_knowledge db-load \
  --documents data/mfsim-ng-zero-flow/documents.jsonl \
  --chunks data/mfsim-ng-zero-flow/chunks.jsonl \
  --color always
```

A carga inteira ocorre numa transação. Documentos, chunks e ocorrências por
branch são atualizados por identificadores estáveis; itens obsoletos daquele
repositório são removidos. Se os hashes dos dois JSONLs não mudaram, uma nova
execução apenas reutiliza a carga anterior.

Consulte o banco com os mesmos filtros e política de acesso:

```bash
PYTHONPATH=src python3 -m mflab_knowledge db-search \
  --query DPMManager \
  --project MFSim-NG \
  --branch master \
  --limit 5 \
  --color always
```

`pending` nunca é recuperável. A ACL, projeto, branch e caminho são filtrados no
SQL antes de o texto entrar no resultado. O índice usa `tsvector` com
configuração `simple`, adequada a identificadores técnicos e conteúdo misto, e
GIN para acelerar a busca textual. A consulta também preserva correspondência
literal em caminhos, títulos e texto.

Por fim, compare o novo backend com a linha de base versionada:

```bash
PYTHONPATH=src python3 -m mflab_knowledge db-evaluate \
  --suite evaluations/mfsim-ng-pilot.json \
  --output data/mfsim-ng-zero-flow/postgres-evaluation.generated.json \
  --color always
```

O comando retorna código diferente de zero se qualquer caso regredir. Use
`db-status` para conferir contagens e o horário da última carga.

### Embeddings locais e recuperação híbrida

O perfil padrão usa `Qwen/Qwen3-Embedding-0.6B`, fixado por commit, com vetores
normalizados de 1.024 dimensões. O download inicial dos pesos é feito uma vez;
o código e os documentos do laboratório permanecem no Morgoth. O dispositivo é
selecionado automaticamente entre GPU e CPU.

Depois de instalar o pgvector e o extra `embeddings`, inicialize a migração e
calcule apenas os chunks ainda ausentes:

```bash
.venv/bin/python -m mflab_knowledge db-vector-init --color always

.venv/bin/python -m mflab_knowledge db-embed \
  --batch-size 4 \
  --color always

.venv/bin/python -m mflab_knowledge db-embedding-status --color always
```

Cada lote é confirmado separadamente. Se o processo for interrompido, executar
`db-embed` novamente retoma o restante e reutiliza os vetores já gravados. Caso
a GPU não comporte um lote, use `--batch-size 2`; `--device cpu` fornece um
fallback mais lento.

Uma consulta híbrida combina correspondências exatas de identificadores com a
proximidade conceitual:

```bash
.venv/bin/python -m mflab_knowledge db-search \
  --mode hybrid \
  --query "Onde o gerenciador de partículas é inicializado?" \
  --project MFSim-NG \
  --branch master \
  --limit 5 \
  --color always
```

ACL, projeto, branch e caminho são aplicados antes de o texto participar dos
resultados semânticos. O piloto usa busca vetorial exata, sem HNSW, porque o
corpus atual tem aproximadamente 12 mil chunks e assim a avaliação não perde
recall por aproximação. A suíte conceitual em português fica em
`evaluations/mfsim-ng-semantic-pilot.json`; instruções completas estão em
[`docs/postgresql.md`](docs/postgresql.md).

## Configuração multi-repositório

O contrato inicial está em `repositories.example.toml`. Ele já descreve o
MFSim-NG e mantém MFSim legado/MFGUI desabilitados e `pending`. Copie para
`repositories.toml` apenas no servidor; esse arquivo local é ignorado pelo Git.
Um próximo comando `sync-all` consumirá essa lista, mas nenhum repositório novo
será ativado sem confirmação de caminho, branch canônica e classe de acesso.

## Política inicial de exclusão

São excluídos automaticamente:

- `.git`, `build`, `install`, caches e ambientes virtuais;
- `docs/html` gerado;
- binários e bibliotecas compiladas;
- arquivos HDF5/H5Part e resultados semelhantes;
- caminhos com nomes que indiquem tokens, credenciais, chaves ou arquivos `.env`;
- links simbólicos, para evitar leitura fora da raiz autorizada.

As regras serão transformadas em política configurável depois que o inventário real do laboratório for revisado.

## Próximas entregas

1. Executar e ampliar as suítes lexical e semântica com perguntas reais.
2. Substituir âncoras heurísticas por parsing estrutural de código e casos.
3. Expor `/search`, `/ask`, `/sources/{id}` e `/index/status`.
4. Adicionar o catálogo multi-repositório e o comando `sync-all`.
5. Receber webhooks do GitLab e manter reconciliação agendada.

As decisões gerais e os limites de segurança estão documentados no `HANDOFF.md` do projeto de continuidade que originou este repositório.
