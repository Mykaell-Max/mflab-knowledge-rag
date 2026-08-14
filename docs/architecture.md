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
       API RAG local com citações
```

## Isolamento de repositórios

O indexador não opera sobre o worktree de um pesquisador. Para cada fonte Git,
mantém um mirror privado no próprio cache. Cada branch ou commit é materializado
por `git archive` em um snapshot imutável, sem diretório `.git`, arquivos locais,
builds não versionados ou mudanças ainda não commitadas.

Uma consulta pode, portanto, usar `master` enquanto o clone fornecido está em uma
branch de trabalho. Conteúdos idênticos entre refs serão deduplicados por hash na
etapa de normalização e embeddings.

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

Uma expansão contextual conservadora deriva hints explícitos dos primeiros
candidatos: pares fonte/header, identificadores suficientemente específicos e
arquivos estruturais do mesmo bundle em `tests/`. Uma segunda consulta vetorial
busca somente esses hints e repete ACL, projeto, branch e prefixo de caminho no
SQL. No máximo dois documentos são intercalados imediatamente depois da
evidência que originou o hint, preservando a ordem do ranking principal. O
resultado registra `context_relation`, `context_rank` e a posição da evidência.
Nenhum caminho ou texto é lido fora das fontes autorizadas.

Para aumentar diversidade, o ranking piloto limita chunks por caminho, colapsa
conteúdos idênticos e comprime o ganho de frequência lexical. Esses limites são
configuráveis para auditorias. Palavras de controle não são aceitas como símbolos
pelas âncoras heurísticas C++, mas um parser sintático ainda será necessário para
relações e assinaturas exatas.

O catálogo futuro de fontes é multi-repositório. Identificadores estáveis de
repositório impedem colisões entre MFSim-NG, MFSim legado, MFGUI e outros
projetos, mesmo quando possuem caminhos ou símbolos iguais. Novas entradas ficam
desabilitadas/pending até a política ser confirmada.

Para remotes HTTPS privados, as credenciais são lidas de variáveis de ambiente
ou de `.env` local ignorado pelo Git. O token é limitado a `read_repository` e
entregue ao Git por `askpass` temporário, nunca pela URL ou argumentos. Prompts
interativos são desativados para que falhas de autenticação encerrem a execução
em vez de bloquear o serviço.

A árvore usa os componentes do nome da branch para organização visual e calcula
`ahead`, `behind`, `merge_base` e estado de merge contra a canônica. Ela não
inventa uma relação de filiação entre branches, pois o Git não preserva
formalmente de qual branch outra foi criada.

## Sincronização futura

1. Um webhook recebe eventos de push, issue, merge request e comentário.
2. O evento é autenticado, deduplicado e colocado numa fila.
3. O worker executa `git fetch` no clone de cache.
4. O worker compara o último SHA indexado com o novo SHA.
5. Arquivos adicionados e modificados são processados novamente.
6. Arquivos removidos recebem tombstones.
7. Alterações no catálogo, texto, vetores e relações são confirmadas numa transação.
8. Uma reconciliação agendada verifica eventos perdidos.

O webhook apenas indica que algo mudou. A fonte canônica continua sendo o GitLab consultado com credenciais somente leitura.

## Política para novas fontes

- Arquivo novo em projeto e branch autorizados: automático.
- Modificação ou remoção: automática.
- Branch nova: segue a política de branches do projeto.
- Projeto ou coleção nova: descoberto como `pending` até existir autorização explícita.
