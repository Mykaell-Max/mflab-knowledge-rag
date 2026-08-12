# MFLab Knowledge RAG

Serviço interno de inventário, indexação e RAG para fontes autorizadas do MFLab.

O projeto é separado do MFSim-NG: ele lê um clone ou snapshot fornecido por caminho, grava apenas em seu próprio armazenamento e nunca precisa alterar o solver.

## Estado atual

A primeira entrega é o inventário piloto somente leitura. Ela:

- descobre arquivos automaticamente;
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

Gere o inventário apontando para o clone atualizado do MFSim-NG:

```bash
mflab-knowledge inventory \
  --source /caminho/para/mfsim-ng \
  --project MFSim-NG \
  --access-class lab \
  --output inventory/mfsim-ng.generated.yaml
```

Também é possível executar sem instalação:

```bash
PYTHONPATH=src python -m mflab_knowledge inventory \
  --source /caminho/para/mfsim-ng \
  --project MFSim-NG \
  --access-class lab \
  --output inventory/mfsim-ng.generated.yaml
```

O relatório gerado fica ignorado pelo Git porque pode conter caminhos e metadados internos.

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

1. Revisar o inventário do MFSim-NG atualizado.
2. Adicionar parsing estrutural de C++, Fortran, CMake, Markdown e casos.
3. Persistir documentos normalizados no PostgreSQL.
4. Adicionar busca lexical e pgvector.
5. Expor `/search`, `/ask`, `/sources/{id}` e `/index/status`.
6. Conectar GitLab em modo somente leitura.
7. Receber webhooks e manter reconciliação agendada.

As decisões gerais e os limites de segurança estão documentados no `HANDOFF.md` do projeto de continuidade que originou este repositório.

