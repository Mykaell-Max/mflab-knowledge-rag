# Arquitetura incremental

## Limites

O repositório do MFSim-NG é uma fonte externa somente leitura. O indexador escreve apenas no banco, cache e diretórios gerados pertencentes a este serviço.

## Fluxo planejado

```text
GitLab / clones / documentos autorizados
                  |
                  v
       descoberta e inventário
                  |
                  v
       parsing por tipo de fonte
                  |
                  v
       documento normalizado + ACL
                  |
                  v
     PostgreSQL textual + pgvector
                  |
                  v
       recuperação híbrida + RRF
                  |
                  v
       contexto limitado + citações
                  |
                  v
   gerador local configurável + API
```

## Isolamento de repositórios

O indexador não opera sobre o worktree de um pesquisador. Cada origem pode ser
um clone local somente leitura ou uma URL Git configurada. Em ambos os casos o
serviço mantém um mirror privado no próprio cache; uma URL não exige clone de
trabalho. Cada branch ou commit é materializado
por `git archive` em um snapshot imutável, sem diretório `.git`, arquivos locais,
builds não versionados ou mudanças ainda não commitadas.

Uma consulta pode, portanto, usar a ref canônica configurada enquanto o clone
fornecido está em outra branch de trabalho. Conteúdos idênticos entre refs serão
deduplicados por hash na etapa de normalização e embeddings.

O comando `sync` consulta o remote a partir do mirror privado, descobre as
branches remotas e produz uma árvore versionada de catálogos. A branch canônica é
marcada explicitamente; branches de trabalho não competem com ela por padrão na
recuperação. Duas branches no mesmo commit compartilham o processamento do
inventário.

O cache incremental possui duas camadas independentes: o snapshot imutável do
commit e o inventário derivado. O inventário só é reutilizado quando repositório,
projeto, commit, perfil, classe de acesso, schema e versão da política coincidem.
Essa chave impede que uma mudança de regras reutilize resultados semanticamente
obsoletos. Escritas são atômicas e entradas ausentes ou inválidas são refeitas.

## Corpus normalizado

O piloto materializa primeiro um corpus JSONL auditável antes de escolher o
schema definitivo do PostgreSQL. Um documento representa uma versão única por
repositório, caminho, hash e ACL. A lista de ocorrências liga essa versão às
branches e commits correspondentes. Chunks preservam linhas, estratégia de
parser, hash próprio e uma `embedding_key` baseada no texto, permitindo calcular
um embedding uma vez e reutilizá-lo sem perder as citações.

A busca lexical local aplica o filtro de acesso e filtros estruturados antes de
retornar texto. Ela serve para avaliar corpus, metadados e perguntas reais e
continua sendo uma das entradas da recuperação híbrida.

A suíte de avaliação versionada transforma perguntas reais em critérios
reprodutíveis de arquivo e posição. Ela registra pass rate, recall das
expectativas e MRR, e retorna falha ao processo quando qualquer caso regride.
Esse mesmo contrato será aplicado à busca textual do PostgreSQL e, depois, à
recuperação híbrida, permitindo comparar os mecanismos sem mudar o conjunto de
referência.

## Persistência PostgreSQL

O corpus JSONL continua sendo um artefato auditável e reconstruível. A carga no
PostgreSQL o projeta em quatro entidades: repositórios, documentos deduplicados,
ocorrências por branch/commit e chunks. Assim, um chunk compartilhado por várias
branches guarda o texto uma vez e continua citável em cada ocorrência.

A carga é transacional e idempotente. Hashes iguais evitam reprocessamento;
identificadores estáveis atualizam registros existentes; chunks e documentos que
sumiram do corpus corrente recebem remoção em cascata. Cada carga concluída
registra os hashes e contagens que formam o fingerprint usado na avaliação.

A busca textual usa `tsvector` armazenado e índice GIN com configuração
`simple`, complementados por correspondência literal para caminhos e
identificadores. ACL, projeto, branch e prefixo de caminho são predicados da
consulta SQL, anteriores ao retorno do texto. Diversidade por arquivo e
deduplicação por hash continuam fazendo parte do contrato de recuperação.

## Embeddings e recuperação híbrida

O schema vetorial é uma migração opcional sobre o backend textual. Cada perfil
registra modelo, revisão imutável, dimensionalidade, comprimento máximo e prompt
de consulta. A chave do perfil deriva de todos esses valores; portanto, uma
mudança incompatível produz um conjunto novo em vez de reutilizar vetores antigos.

Os embeddings são calculados localmente e gravados incrementalmente por lote.
Chunks `pending` são excluídos antes da inferência. Na consulta semântica, ACL,
projeto, branch e prefixo de caminho são predicados SQL anteriores ao ranking e
ao retorno de texto. A proveniência continua vindo das ocorrências por branch.

O corpus piloto usa distância cosseno exata no pgvector. Um índice aproximado
HNSW somente será considerado quando volume e medições justificarem a troca;
nesse caso, recall e latência deverão ser avaliados explicitamente.

O modo híbrido busca candidatos de forma independente no PostgreSQL FTS e no
pgvector. Em seguida, RRF combina apenas as posições nos dois rankings, evitando
comparar escalas de score incompatíveis. O resultado final volta a aplicar
diversidade por arquivo e deduplicação por hash.

Uma expansão contextual conservadora deriva relações dos primeiros candidatos:
pares fonte/header, identificadores suficientemente específicos, vizinhança de
linhas no mesmo documento e diretórios sustentados por múltiplos documentos
estruturados. O ancestral comum mais específico define cada bundle; nomes de
repositórios, diretórios, arquivos, casos e símbolos não fazem parte do
algoritmo.

Uma segunda consulta busca somente essas relações e repete ACL, projeto, branch
e prefixo de caminho no SQL. Chunks do mesmo documento são ordenados primeiro
pela distância estrutural até as evidências já recuperadas e depois pela
similaridade vetorial. Esses documentos também têm prioridade na janela de
candidatos SQL, antes de pares de arquivos e bundles, para que seus vizinhos não
sejam perdidos por truncamento. Pares fonte/header cujo caminho já está
representado no ranking-base não são expandidos novamente, preservando as vagas
contextuais para complementos ausentes. A janela SQL reserva o melhor candidato
de cada caminho estrutural explícito antes de distribuir as vagas restantes por
similaridade; o limite efetivo nunca fica abaixo da quantidade desses caminhos.
Cada bundle pode fornecer no máximo um
complemento. O resultado registra `context_relation`, `context_group`,
`context_rank` e a posição da evidência. Nenhum caminho ou texto é lido fora das
fontes autorizadas.

O horizonte usado para derivar relações reserva antecipadamente o número máximo
de vagas contextuais configurado. Assim, somente a parte do ranking-base que
continuaria presente após todas as promoções é tratada como já representada;
pares localizados na cauda que seria deslocada permanecem elegíveis para
promoção. O cálculo depende apenas do limite da consulta e da política local.

Os limites e formatos que podem formar bundles são carregados de uma política
TOML local e incorporados ao fingerprint da recuperação. Assim, clientes CLI,
API e MCP podem compartilhar a mesma política auditável quando o processo se
tornar um serviço persistente.

Para aumentar diversidade, o ranking piloto limita chunks por caminho, colapsa
conteúdos idênticos e comprime o ganho de frequência lexical. Esses limites são
configuráveis para auditorias. Palavras de controle não são aceitas como símbolos
pelas âncoras heurísticas C++, mas um parser sintático ainda será necessário para
relações e assinaturas exatas.

O catálogo de fontes é multi-repositório. Identificadores estáveis impedem
colisões mesmo quando projetos possuem caminhos ou símbolos iguais. Cada entrada
define explicitamente fonte, ref canônica, escopo, ACL, perfil e filtros de
branch; nenhum nome de branch é presumido pelo orquestrador. O `sync-all` isola
cache, saída e falhas por repositório e grava um manifesto agregado com o hash da
configuração. Novas entradas ficam desabilitadas/`pending` até a política ser
confirmada.

Perfis de inventário são dados, não condicionais no código. Um catálogo TOML
separado associa nomes de perfil a globs de inclusão e exclusão; o catálogo de
repositórios escolhe explicitamente um desses nomes. `generic` é neutro e não
depende do nome do projeto. O hash das regras selecionadas acompanha o catálogo
gerado e a identidade do cache, portanto qualquer mudança de escopo força a
reavaliação dos commits sem apagar caches manualmente.

Quando a política canônica é `remote_default`, o Git informa simbolicamente o
nome da branch padrão. O serviço persiste essa relação no mirror e registra no
manifesto tanto a política quanto a branch efetivamente resolvida; não presume
nomes como `master` ou `main`.

Fetches de mirrors remotos possuem timeout configurável por fonte. O subprocesso
Git é acompanhado incrementalmente: percentuais de enumeração, recebimento e
resolução são encaminhados aos logs com limitação de frequência, enquanto um
heartbeat confirma atividade mesmo quando o servidor permanece silencioso.
Credenciais continuam somente no ambiente temporário do `askpass` e não fazem
parte das linhas de comando ou mensagens.

Para remotes HTTPS privados, as credenciais são lidas de variáveis de ambiente
ou de `.env` local ignorado pelo Git. O token é limitado a `read_repository` e
entregue ao Git por `askpass` temporário, nunca pela URL ou argumentos. Prompts
interativos são desativados para que falhas de autenticação encerrem a execução
em vez de bloquear o serviço.

A árvore usa os componentes do nome da branch para organização visual e calcula
`ahead`, `behind`, `merge_base` e estado de merge contra a canônica. Ela não
inventa uma relação de filiação entre branches, pois o Git não preserva
formalmente de qual branch outra foi criada.

Branches importadas ou criadas como históricos órfãos podem não possuir um
ancestral comum com a canônica. Elas permanecem indexáveis com relação
`unrelated` e `merge_base` nulo; essa condição válida do Git não interrompe a
sincronização das demais branches.

## Sincronização contínua

O comando `index-all` é a unidade idempotente de atualização do serviço. Ele
consome exclusivamente o catálogo de repositórios e encadeia mirror, inventário,
normalização, carga transacional por `repository_id` e embeddings incrementais.
O comando `run-scheduled` aplica trava de processo, estado persistente e histórico
sobre esse mesmo contrato. Um timer `systemd` já fornece a reconciliação periódica
sem terminal aberto. O futuro listener de eventos deverá chamar o mesmo contrato,
opcionalmente limitado ao repositório afetado, em vez de reimplementar as etapas.

1. Um webhook recebe eventos de push, issue, merge request e comentário.
2. O evento é autenticado, deduplicado e colocado numa fila.
3. O worker executa `git fetch` no clone de cache.
4. O worker compara o último SHA indexado com o novo SHA.
5. Arquivos adicionados e modificados são processados novamente.
6. Arquivos removidos recebem tombstones.
7. Alterações no catálogo, texto, vetores e relações são confirmadas numa transação.
8. Uma reconciliação agendada verifica eventos perdidos.

O webhook apenas indica que algo mudou. A fonte canônica continua sendo o GitLab consultado com credenciais somente leitura.

## Serviço de recuperação

A API HTTP é uma camada fina e somente leitura sobre as mesmas funções de
estado e recuperação usadas pelo CLI. Ela não conhece nomes de repositórios,
projetos, branches, caminhos ou símbolos. Esses valores continuam vindo do banco
e dos filtros da requisição.

O processo define um teto de classes de acesso no startup. Uma requisição pode
selecionar somente um subconjunto desse teto, e o predicado de ACL continua sendo
aplicado no PostgreSQL antes do retorno do texto. Sem autenticação, o servidor
aceita exclusivamente endereços loopback. Um bind direto na LAN é opt-in e
depende de chave Bearer forte local; requisições originadas no próprio servidor
permanecem liberadas para não distribuir esse segredo à automação interna.

A interface web descobre repositórios e branches pelo banco e compartilha a
origem da API, sem CORS. Perguntas e buscas usam rotas somente leitura sob
`/ui-api`, liberadas apenas porque o bind e o firewall limitam o serviço à rede
confiável do laboratório; a chave Bearer técnica continua restrita às
integrações programáticas. Essas rotas web aplicam um teto fixo de classes
`public` e `lab`, além do teto configurado no processo. Dados operacionais usam uma rota administrativa
separada. A senha vem de `MFLAB_ADMIN_PASSWORD`, é comparada no servidor e cria
uma sessão aleatória em cookie `HttpOnly` e `SameSite=Strict`; o navegador não
armazena a senha. Essa fronteira é adequada ao piloto, mas não substitui
identidade individual, TLS, grupos, limites por usuário e auditoria.

Buscas lexicais não inicializam o runtime de embeddings. Na primeira busca
semântica ou híbrida, uma única instância local do modelo é carregada e
reutilizada. O acesso a ela é serializado, preservando a GPU e evitando uma
cópia por requisição. Esse contrato inicial favorece previsibilidade; fila,
limites por usuário e concorrência medida entram junto da autenticação.

Antes da geração, o montador de contexto aplica um orçamento total sobre os
resultados já filtrados, atribui identificadores locais de fonte e preserva
citação, projeto, caminho, linhas e ocorrências. O pacote marca truncamentos e
instrui o consumidor a tratar todo conteúdo recuperado como evidência não
confiável. Essa etapa é independente do fornecedor do modelo; `/ask`, MCP e a
interface devem consumi-la em vez de remontar prompts por conta própria.

O gerador é definido por um arquivo local e acessado por um adaptador compatível
com a API OpenAI. O código não conhece nomes de repositórios, branches, modelos
ou produtos de inferência. Somente endpoints literais de loopback são aceitos;
hosts externos, credenciais na URL, proxies e redirecionamentos são recusados.

O endpoint `/ask` aplica ACL e filtros antes da geração, preserva a ocorrência
selecionada de cada evidência e valida os identificadores `[S<n>]` mencionados
na resposta. Escopos distintos são devolvidos separadamente e produzem um aviso
explícito. Ausência de evidência causa abstenção sem inferência; ausência do
gerador não afeta os endpoints de recuperação.

O resolvedor de escopo opera antes da recuperação e usa somente metadados do
catálogo autorizado. `aliases` e `preferred_branch` são definidos por
repositório. Menções de múltiplos projetos ou branches viram escopos paralelos;
os resultados são intercalados para que uma fonte não elimine a outra. A
decisão é devolvida em `scope_resolution` e filtros explícitos do cliente nunca
são substituídos. Correspondências incertas permanecem amplas em vez de aplicar
uma restrição silenciosa.

Perguntas amplas passam por uma primeira camada de exploração qualitativa
determinística. Consultas auxiliares genéricas procuram finalidade, arquitetura,
componentes, linguagens e capacidades; documentos de entrada recebem prioridade
sobre artefatos provisórios. A montagem intercala os escopos disponíveis e o
gerador deve citar cada um. A cobertura entre projeto e branch é medida
separadamente da cobertura textual e pode provocar uma única revisão da
síntese. Essa navegação não substitui a evidência primária nem cria fatos.

## Política para novas fontes

- Arquivo novo em projeto e branch autorizados: automático.
- Modificação ou remoção: automática.
- Branch nova: segue a política de branches do projeto.
- Projeto ou coleção nova: descoberto como `pending` até existir autorização explícita.
