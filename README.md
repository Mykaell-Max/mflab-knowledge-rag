# MFLab Knowledge RAG

Serviço interno de inventário, indexação e RAG para fontes autorizadas do MFLab.

Para continuar o desenvolvimento em outra conversa, leia primeiro
[`HANDOFF.md`](HANDOFF.md). Ele registra o estado operacional, as validações já
concluídas e a próxima ação recomendada.

O projeto é separado do MFSim-NG: ele lê um clone ou snapshot fornecido por caminho, grava apenas em seu próprio armazenamento e nunca precisa alterar o solver.

## Estado atual

O piloto somente leitura já cobre inventário, sincronização multi-repositório
e multi-branch,
normalização, persistência no PostgreSQL e recuperação lexical,
semântica e híbrida, além da execução incremental agendada por `systemd`.
Uma API HTTP local expõe o estado e a recuperação citável sem duplicar o
pipeline.
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
- serve saúde, estado, cobertura por repositório, busca e interface web pela
  mesma API;
- avalia respostas reais da API, cobertura de citações, abstinência, latência e
  pico opcional de GPU por uma suíte JSON versionada;
- aplica branch canônica, escopo e filtros independentes por repositório,
  declarados em TOML e registrados por hash no manifesto agregado.

A API também pode gerar respostas citadas por meio de qualquer servidor LLM
local compatível com a API OpenAI. O provedor e o modelo ficam em um arquivo
local ignorado pelo Git; nenhum repositório, branch ou modelo é fixado no
código. Metadados colaborativos do GitLab ainda serão adicionados.

## Requisitos

- Python 3.11 ou superior;
- módulo `venv` correspondente ao Python do sistema;
- Git disponível no `PATH` para detectar branch e commit de clones reais.
- PostgreSQL local e o extra opcional `postgres` para os comandos `db-*`;
- pgvector e o extra `embeddings` somente para busca semântica/híbrida.
- FastAPI/Uvicorn pelo extra `service` somente para o serviço HTTP.

O inventário inicial não instala bibliotecas e não precisa acessar a rede.

## API RAG local

Instale o transporte HTTP junto dos backends já utilizados:

```bash
python3 -m pip install -e '.[postgres,embeddings,service]'
```

Inicie o processo de validação em primeiro plano:

```bash
.venv/bin/python -m mflab_knowledge serve \
  --env-file .env \
  --state-dir state \
  --host 127.0.0.1 \
  --port 8765 \
  --color always
```

O processo não carrega o modelo na inicialização. A GPU só é ocupada na
primeira chamada `semantic` ou `hybrid`, e a mesma instância do modelo é
reutilizada nas consultas seguintes. Os endpoints iniciais são `/health`,
`/status`, `/repositories`, `POST /structure`, `POST /search`, `POST /context`
e `POST /ask`; a
documentação interativa fica em `/docs` e a interface em `/ui`. `/context` transforma a recuperação em
um pacote limitado e citável, e `/ask` o envia somente a um gerador local
configurado. O contrato completo está em
[`docs/api.md`](docs/api.md).

Sem uma `MFLAB_API_KEY` forte, o comando recusa endereços que não sejam
loopback. Em acesso direto pela LAN, os endpoints programáticos exigem a chave
como Bearer; chamadas do próprio servidor continuam disponíveis para a
automação local. A interface em `/ui` abre diretamente em perguntas e buscas.
O painel **Administração** concentra saúde do PostgreSQL, máquina, GPU,
indexação e cobertura do corpus; ele exige `MFLAB_ADMIN_PASSWORD` no `.env`.
Respostas são apresentadas como Markdown seguro, sem aceitar HTML do modelo.
Parágrafos, listas, ênfase, código inline e blocos cercados com linguagem são
renderizados localmente. IDs como `[S1]` permanecem na resposta auditável da
API, mas a interface os transforma em referências com arquivo e linhas; cada
referência leva ao cartão completo da fonte e o destaca.
O mesmo componente de código é usado nos trechos recuperados por **Buscar** e
nas fontes exibidas abaixo de uma resposta. A linguagem vem do formato indexado,
do caminho do arquivo ou da marcação cercada e recebe realce léxico local com
paleta inspirada no editor VS Code. A implementação não usa CDN ou JavaScript
de terceiros.
Os limites de geração pertencem ao backend: a interface não fixa orçamento de
contexto ou saída, e `generation.toml` controla o teto de evidências conforme a
janela do modelo local. Respostas de excesso de contexto causam redução segura
e nova tentativa, sem remover a proveniência das fontes.
Depois da síntese, uma chamada separada ao mesmo modelo local confronta cada
afirmação com os trechos que ela própria cita. Correspondência de termos não é
tratada como prova. Uma conclusão não sustentada provoca no máximo uma revisão;
se continuar sem apoio, o serviço não entrega a resposta candidata. Esses
limites são configuráveis, mas a auditoria fica habilitada por padrão.
Na interface, perguntas são executadas por uma fila local limitada a um worker.
O navegador acompanha etapas reais de escopo, recuperação, seleção de fontes,
síntese, auditoria e eventual revisão. Essa trilha não contém prompts, raciocínio
interno ou texto integral das fontes.
As classes `public` e `lab` são o teto padrão do processo, e cada requisição
pode apenas restringir esse conjunto, nunca ampliá-lo.

O catálogo aceita `aliases` e `preferred_branch` por repositório. A interface
lista todas as branches realmente indexadas, separando a preferencial e as
canônicas. Sem filtros manuais, o serviço reconhece somente nomes de projeto,
aliases e branches com alta confiança; comparações explícitas são recuperadas
em escopos separados e intercaladas. A resolução aplicada é devolvida ao
cliente e mostrada na interface, nunca escondida.

Perguntas amplas de definição ou visão geral ativam exploração limitada. O
serviço formula um pequeno conjunto genérico de consultas sobre finalidade,
arquitetura, componentes, linguagens e capacidades, favorece documentos de
entrada em vez de rascunhos especializados e intercala as branches
preferenciais dos repositórios disponíveis. A resposta precisa representar e
citar cada escopo recuperado. Se a primeira síntese omitir algum deles, uma
única revisão automática é solicitada; a cobertura continua visível no retorno.

Antes dessa seleção textual, cada escopo recebe um mapa estrutural determinístico
calculado dos metadados autorizados já presentes no PostgreSQL. O mapa enumera
formatos, entradas de primeiro nível, volumes e documentos de entrada, tem hash
reproduzível e preserva projeto, branch e commit. Ele pode sustentar apenas
afirmações sobre a estrutura indexada; finalidade e capacidades continuam
dependendo dos trechos primários. Isso melhora a navegação sem nova indexação,
sem chamada adicional ao LLM e sem nomes científicos fixados no motor.

O roteamento local pode ser ajustado sem editar ou reconstruir manualmente o
TOML. O comando abaixo altera somente o registro indicado, preserva os demais
campos e comentários, valida o catálogo temporário e então substitui o arquivo
atomicamente:

```bash
.venv/bin/python -m mflab_knowledge configure-routing \
  --config repositories.toml \
  --repository ID \
  --preferred-branch BRANCH \
  --alias NOME \
  --color always
```

`--alias` é repetível e acrescenta aliases sem remover os existentes. Os nomes
de projetos e branches pertencem ao catálogo local, não ao motor.

Depois da validação em primeiro plano, instale a API como serviço permanente:

```bash
./scripts/install-api-systemd.sh \
  --project-dir "$PWD" \
  --user "$USER" \
  --group "$(id -gn)" \
  --port 8765
```

O instalador renderiza e verifica uma unidade genérica, protege `.env`, inicia o
processo, consulta `/health` e encerra com erro se a API não ficar saudável. A
unidade reinicia após falhas e no boot, sem depender de um terminal aberto.
Para uma demonstração na rede confiável do laboratório, repita a instalação com
`--host 0.0.0.0`. O instalador cria e preserva a chave forte em `.env`; qualquer
regra de firewall deve limitar a porta à sub-rede confiável.

O servidor OpenAI-compatible local também pode ser instalado separadamente
como serviço. O runtime, o snapshot e o nome publicado são argumentos locais;
nenhum modelo ou caminho de usuário fica gravado no template:

```bash
./scripts/install-llm-systemd.sh \
  --project-dir "$PWD" \
  --user "$USER" \
  --group "$(id -gn)" \
  --vllm-python /caminho/do/runtime/bin/python \
  --model-path /caminho/do/snapshot-local \
  --served-model-name modelo-local \
  --port 8000
```

O instalador valida o runtime, o JSON do chat template, o snapshot e a unidade
antes de usar `sudo`. O processo opera offline, escuta apenas em loopback,
reinicia após falhas e publica `/health` e `/metrics`. Parâmetros de GPU,
contexto, concorrência e template continuam configuráveis pela linha de
comando. Consulte [`docs/api.md`](docs/api.md) para conectar o endpoint a
`generation.toml`.

Avalie o fluxo completo já servido, incluindo busca híbrida, geração e
telemetria local:

```bash
.venv/bin/python -m mflab_knowledge api-evaluate \
  --suite evaluations/mfsim-ng-answer-pilot.json \
  --api-base-url http://127.0.0.1:8765 \
  --output data/mfsim-ng-answer-evaluation.generated.json \
  --color always
```

As perguntas, filtros e fontes esperadas pertencem à suíte, não ao serviço.
Outros repositórios podem adicionar arquivos de avaliação independentes. O
relatório registra cada resposta e verificação, duração do cliente, cobertura
por unidade factual e, quando `nvidia-smi` existe, pico de memória e utilização
da GPU. Use `--no-gpu-monitor` em máquinas sem NVIDIA.

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
  --profile mfsim-ng-pilot \
  --inventory-policy-file inventory-policies.toml \
  --output inventory/mfsim-ng.generated.yaml
```

Também é possível executar sem instalação:

```bash
PYTHONPATH=src python -m mflab_knowledge inventory \
  --source /caminho/para/mfsim-ng \
  --ref master \
  --project MFSim-NG \
  --access-class lab \
  --profile mfsim-ng-pilot \
  --inventory-policy-file inventory-policies.toml \
  --output inventory/mfsim-ng.generated.yaml
```

Perfis não são inferidos pelo nome do projeto. O arquivo versionado
`inventory-policies.toml` define, por globs, o escopo do perfil piloto usado
neste repositório. O perfil universal `generic` considera todos os formatos
suportados. Novos repositórios podem usar `generic` ou receber outro perfil na
configuração, sem alterar o código do indexador. O restante continua visível
como exclusão do catálogo, mas não é preparado para embeddings.

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
  --profile mfsim-ng-pilot \
  --inventory-policy-file inventory-policies.toml \
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

A ref passada em `--canonical-ref` é marcada como canônica. O argumento é
obrigatório: o indexador não presume `master`, `main` ou qualquer outro nome.
As demais branches permanecem consultáveis,
mas separadas. Branches no mesmo commit reutilizam o inventário já calculado.
Para operar temporariamente sem consultar o GitLab, acrescente `--offline`.

Além dos snapshots Git, o comando mantém em `cache/inventories/` um cache
persistente do inventário de cada commit. A chave inclui repositório, projeto,
commit, perfil, hash da política externa, classe de acesso e versão do schema.
Assim, uma nova execução sem mudanças não percorre novamente milhares de
arquivos. O resumo
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
├── semantic-map.generated.json
├── documents.jsonl
├── chunks.jsonl
├── symbols.jsonl
└── relations.jsonl
```

Cada documento é uma versão única identificada por repositório, caminho, hash e
classe de acesso. Suas `occurrences` preservam todas as branches e commits em que
ela existe. Os chunks guardam texto, título/símbolo heurístico, linhas, parser,
ACL e citações. Conteúdo textual idêntico usa a mesma `embedding_key`, preparando
a deduplicação dos embeddings.

O mapa semântico determinístico deriva símbolos das âncoras estruturais e
relações de alta confiança, como includes, imports, módulos usados e pares
fonte/header. Cada registro preserva documento, chunk de evidência quando
aplicável, ACL e ocorrências por branch/commit. Esses artefatos orientam
navegação futura; não substituem os chunks primários como evidência factual.

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

O modo híbrido também deriva contexto estrutural dos primeiros resultados:
pares fonte/header, referências a identificadores, chunks vizinhos do mesmo
documento e grupos de arquivos estruturados sob um ancestral comum. A inferência
de grupos usa apenas estrutura e formatos configurados; o algoritmo não contém
nomes de projetos, casos, arquivos ou símbolos do MFSim-NG.

No máximo dois documentos relacionados são consultados no PostgreSQL e
intercalados imediatamente depois da evidência que originou cada relação, sem
competir com ela. A consulta repete ACL, projeto, branch e caminho antes de
retornar qualquer texto. Os metadados registram a relação, a evidência de origem
e a política usada no ranking.

Os limites globais ficam em `retrieval.toml`, ignorado pelo Git. Use
`retrieval.example.toml` como base. Se o arquivo não existir, o serviço usa os
mesmos padrões documentados no exemplo. `db-search` e `db-evaluate` também aceitam
`--retrieval-config`; a política efetiva entra no fingerprint da avaliação.

## Configuração multi-repositório

O contrato está em `repositories.example.toml`. Ele descreve o MFSim-NG e
mantém exemplos adicionais desabilitados e `pending`. Copie-o para o arquivo
local ignorado pelo Git e edite somente os dados confirmados no laboratório:

```bash
cp repositories.example.toml repositories.toml
${EDITOR:-nano} repositories.toml
```

Cada bloco `[[repositories]]` exige seu próprio `id`, projeto e exatamente uma
origem: `source` para um clone local ou `remote_url` para o serviço manter seu
próprio mirror sem clone de trabalho. URLs com usuário, token ou senha embutidos
são rejeitadas. `canonical_ref`, `branch_scope`, classe de acesso, perfil e os globs opcionais
`include_branches`/`exclude_branches` também são independentes. A branch
canônica é preservada mesmo que um filtro a exclua. Nomes como `master`,
`develop` ou convenções de um projeto vivem somente em configuração, nunca no
motor genérico.

O campo `[defaults].inventory_policy_file` aponta para o catálogo versionado de
perfis de inventário. Cada perfil contém apenas globs `include_paths` e
`exclude_paths`; cada repositório escolhe seu perfil pelo campo `profile`.
Caminhos específicos de uma base ficam nesse catálogo auditável. Alterar uma
regra muda seu hash e invalida somente os caches de inventário afetados.

`canonical_ref = "remote_default"` segue explicitamente a branch padrão
informada pelo servidor Git. O nome resolvido é armazenado como ref simbólica no
mirror para que o modo `--offline` continue reproduzível. Também é possível
fixar qualquer ref, como `origin/trunk` ou `origin/release/current`, por
repositório.

`fetch_timeout_seconds` pode ser definido em `[defaults]` e sobrescrito em
qualquer repositório. Durante downloads, o serviço força o progresso do Git,
mostra as etapas e percentuais em incrementos de 5% e emite um heartbeat a cada
15 segundos quando o servidor não fornece novos dados. Assim, operações grandes
não parecem travadas e o limite efetivo fica registrado nos manifestos.

Sincronize todos os repositórios habilitados com uma execução:

```bash
.venv/bin/python -m mflab_knowledge sync-all \
  --config repositories.toml \
  --env-file .env \
  --color always
```

Para testar apenas um ou alguns IDs, repita `--repository ID`. Uma falha é
isolada: os outros repositórios continuam por padrão. `--fail-fast` muda esse
comportamento e `--offline` reutiliza apenas as refs já presentes nos mirrors.
O resultado agregado fica em `inventory/repositories/manifest.generated.yaml`;
cada repositório recebe sua própria subárvore, cache e manifesto. O hash do TOML
efetivamente usado é gravado para auditoria, mas credenciais nunca entram nele.

Durante a execução, o console separa visualmente cada repositório, identifica a
branch nas barras de inventário e mostra progresso global, cache calculado ou
reutilizado, duração e resumo agregado. A árvore completa de branches permanece
no arquivo `branches.generated.txt` em vez de poluir o log do serviço. Com
`--verbose`, cada branch e reutilização de cache ganha detalhes adicionais. Sem
essa opção, repositórios grandes mostram marcos de progresso a cada 5%. Com
`--quiet`, somente erros e o JSON final são emitidos, preservando uma interface
estável para automação.

### Pipeline completo e incremental

Depois que `repositories.toml` e `.env` estiverem configurados, um único comando
executa sincronização, normalização, carga no PostgreSQL e embeddings locais:

```bash
.venv/bin/python -m mflab_knowledge index-all \
  --config repositories.toml \
  --env-file .env \
  --color always
```

O pipeline é idempotente em todos os estágios: mirrors, snapshots, inventários,
parses, corpora PostgreSQL e embeddings existentes são reutilizados. Cada corpus
é substituído apenas dentro de seu `repository_id`; projetos diferentes
permanecem simultaneamente no banco. Falhas são isoladas por repositório e o
resultado auditável fica em `data/repositories/index-all.generated.yaml`, sem a
URL do banco ou credenciais.

Embeddings novos são persistidos em checkpoints transacionais de tamanho
limitado. Uma interrupção preserva checkpoints anteriores e a execução seguinte
retoma somente os chunks ainda ausentes, sem abrir uma conexão por minibatch.

`--repository ID` restringe um teste, `--offline` usa os mirrors existentes e
`--no-embeddings` valida somente até a carga no banco. A operação normal do
serviço deve omitir `--no-embeddings`, para manter o RAG integralmente
atualizado.

### Execução automática no servidor

O comando `run-scheduled` envolve o mesmo pipeline com trava contra concorrência,
estado atômico da última execução, histórico limitado e progresso persistente.
No servidor Linux, as unidades `systemd` podem ser geradas e instaladas sem
caminhos fixos:

```bash
./scripts/install-systemd.sh \
  --project-dir "$PWD" \
  --user "$USER" \
  --group "$(id -gn)" \
  --interval 5min \
  --batch-size 4 \
  --device cpu \
  --run-now
```

O timer consulta os remotes depois de cada intervalo, reutiliza todo conteúdo
inalterado e processa somente branches, documentos, chunks e embeddings novos.
O dispositivo do processo agendado é configurável; `cpu` é o padrão seguro
quando a mesma GPU atende simultaneamente a API de embeddings e o servidor LLM.
Uma máquina com GPU dedicada ao indexador pode selecionar `--device cuda`.
Não é necessário manter um terminal aberto. Instalação, estado, logs, retomada
e comandos administrativos estão descritos em
[`docs/operations.md`](docs/operations.md).

Um repositório `pending` não pode ser habilitado. Antes de ativá-lo, confirme
caminho, branch canônica, perfil e autorização de acesso.

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
2. Acrescentar símbolos e relações de código ao mapa estrutural determinístico.
3. Expor `/sources/{id}` e `/index/status`.
4. Receber webhooks do GitLab como aceleração da reconciliação agendada.

As decisões gerais e os limites de segurança estão documentados no `HANDOFF.md` do projeto de continuidade que originou este repositório.
