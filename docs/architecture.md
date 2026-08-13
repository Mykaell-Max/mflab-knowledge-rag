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
