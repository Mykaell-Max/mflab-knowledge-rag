# Regras do repositório

Este projeto implementa o inventário, a indexação e o RAG do MFLab. Os repositórios científicos são fontes externas e devem permanecer somente leitura.

- Nunca modificar, formatar, compilar ou executar MFSim, MFSim-NG ou MFGUI como parte da indexação.
- Nunca copiar credenciais para configuração, catálogo, logs, prompts ou commits.
- Não enviar conteúdo interno a serviços externos sem autorização explícita.
- Preservar projeto, repositório, branch, commit, caminho, linhas ou página, classe de acesso e tipo de evidência.
- Tratar snapshots sem metadados Git como `unversioned_snapshot`; não inventar branch ou commit.
- Arquivos novos de fontes autorizadas podem ser descobertos automaticamente. Novos projetos ou coleções sem política devem permanecer `pending`.
- Toda recuperação deve filtrar a classe de acesso antes de entregar contexto a um modelo.
- Resultados gerados, caches, bancos e clones de fontes não devem ser versionados neste repositório.

