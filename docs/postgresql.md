# PostgreSQL local no Morgoth

Esta etapa mantém o banco restrito ao próprio computador do laboratório. Para o
piloto, a conexão usa o socket Unix e autenticação `peer`: o usuário Linux `max`
acessa uma role PostgreSQL com o mesmo nome, sem senha e sem porta TCP exposta.

## 1. Instalar e iniciar

```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib python3-venv
sudo systemctl enable --now postgresql
sudo systemctl status postgresql --no-pager
```

## 2. Criar role e banco locais

Os testes abaixo tornam os comandos repetíveis:

```bash
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='max'" \
  | grep -q 1 || sudo -u postgres createuser max

sudo -u postgres psql -tAc \
  "SELECT 1 FROM pg_database WHERE datname='mflab_knowledge'" \
  | grep -q 1 || sudo -u postgres createdb --owner=max mflab_knowledge
```

Nenhuma alteração é feita no MFSim-NG. O banco pertence somente ao serviço de
conhecimento.

## 3. Configurar o projeto

Instale o driver opcional dentro do ambiente virtual do projeto:

```bash
cd ~/Desktop/mflab-knowledge-rag
python3 -m venv --clear .venv
.venv/bin/python -m pip install -e '.[postgres]'
```

No `.env` local, acrescente ou preencha:

```dotenv
MFLAB_DATABASE_URL=postgresql:///mflab_knowledge
```

O arquivo já é ignorado pelo Git. Se a variável ainda não existir, o primeiro
comando `db-*` acrescenta um campo vazio e informa o que falta. A URL acima não
contém senha: ela seleciona o socket local e a role correspondente ao usuário
Linux atual.

## 4. Criar o schema e carregar o corpus

```bash
.venv/bin/python -m mflab_knowledge db-init --color always

.venv/bin/python -m mflab_knowledge db-load \
  --documents data/mfsim-ng-zero-flow/documents.jsonl \
  --chunks data/mfsim-ng-zero-flow/chunks.jsonl \
  --color always

.venv/bin/python -m mflab_knowledge db-status --color always
```

Uma segunda execução de `db-load` com os mesmos arquivos deve informar que o
corpus foi reutilizado.

## 5. Buscar e avaliar

```bash
.venv/bin/python -m mflab_knowledge db-search \
  --query DPMManager \
  --project MFSim-NG \
  --branch master \
  --limit 5 \
  --color always

.venv/bin/python -m mflab_knowledge db-evaluate \
  --suite evaluations/mfsim-ng-pilot.json \
  --output data/mfsim-ng-zero-flow/postgres-evaluation.generated.json \
  --color always
```

O alvo inicial é manter 5/5 casos e recall de 100%. O MRR pode mudar porque o
ranking nativo do PostgreSQL não é idêntico ao ranking Python; essa diferença é
medida, não escondida.

## 6. Limites desta etapa

- ainda não cria a extensão `vector` nem calcula embeddings;
- ainda não expõe API ou porta de rede;
- a ACL é aplicada pela camada de busca antes de retornar o texto;
- o schema pode ser reconstruído a partir dos JSONLs e das fontes autorizadas;
- para um serviço executado por outro usuário Linux, será criada depois uma role
  dedicada em vez de reutilizar `max`.
