# MFLab Knowledge RAG

Serviço interno de inventário, indexação e RAG para fontes autorizadas do MFLab.

O projeto é separado do MFSim-NG: ele lê um clone ou snapshot fornecido por caminho, grava apenas em seu próprio armazenamento e nunca precisa alterar o solver.

## Estado atual

A primeira entrega é o inventário piloto somente leitura. Ela:

- descobre arquivos automaticamente;
- em clones Git, considera apenas arquivos versionados no commit atual;
- detecta branch e commit quando a fonte é um clone Git;
- calcula hashes para futuras atualizações incrementais;
- classifica formatos;
- exclui builds, documentação gerada, binários, resultados volumosos e possíveis segredos;
- gera um relatório YAML sem dependências Python externas.

Busca híbrida, PostgreSQL/pgvector, API RAG, modelo local e integração GitLab serão adicionados depois da validação deste inventário.

## Requisitos

- Python 3.11 ou superior;
- Git disponível no `PATH` para detectar branch e commit de clones reais.

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

O acesso ao remote usa a configuração segura de credenciais do Git. Tokens não
devem ser colocados na linha de comando, URL do repositório ou arquivos deste
projeto.

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

1. Revisar o manifesto e a árvore multi-branch do MFSim-NG atualizado.
2. Adicionar parsing estrutural de C++, Fortran, CMake, Markdown e casos.
3. Persistir documentos normalizados no PostgreSQL.
4. Adicionar busca lexical e pgvector.
5. Expor `/search`, `/ask`, `/sources/{id}` e `/index/status`.
6. Conectar GitLab em modo somente leitura.
7. Receber webhooks e manter reconciliação agendada.

As decisões gerais e os limites de segurança estão documentados no `HANDOFF.md` do projeto de continuidade que originou este repositório.
