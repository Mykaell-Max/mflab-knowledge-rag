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

