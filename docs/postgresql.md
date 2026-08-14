# PostgreSQL local no Morgoth

Esta etapa mantém o banco restrito ao próprio computador do laboratório. Para o
piloto, a conexão usa o socket Unix e autenticação `peer`: o usuário Linux `max`
acessa uma role PostgreSQL com o mesmo nome, sem senha e sem porta TCP exposta.

## 1. Instalar e iniciar

```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib postgresql-18-pgvector \
  python3-venv python3-dev
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
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[postgres,embeddings]'
```

No `.env` local, acrescente ou preencha:

```dotenv
MFLAB_DATABASE_URL=postgresql:///mflab_knowledge
```

O arquivo já é ignorado pelo Git. Se a variável ainda não existir, o primeiro
comando `db-*` acrescenta um campo vazio e informa o que falta. A URL acima não
contém senha: ela seleciona o socket local e a role correspondente ao usuário
Linux atual.

O extra de embeddings baixa as bibliotecas e, na primeira inferência, os pesos
do modelo público. Nenhum arquivo do MFSim-NG é enviado ao provedor do modelo.
O pacote de headers deve corresponder exatamente ao Python do ambiente. Se o
Python for 3.14 e a inferência CUDA reclamar de `Python.h`, instale
`python3.14-dev`.

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

## 6. Ativar o pgvector

A instalação da extensão é a única operação administrativa desta etapa. Ela
ocorre uma vez e não torna o usuário `max` superusuário:

```bash
sudo -u postgres psql -d mflab_knowledge \
  -c 'CREATE EXTENSION IF NOT EXISTS vector;'

.venv/bin/python -m mflab_knowledge db-vector-init --color always
```

O segundo comando valida a extensão e cria as tabelas do serviço com vetores de
1.024 dimensões. Se o pacote da sua distribuição tiver outro nome, confira a
versão ativa com `pg_config --version` e procure o pacote `pgvector`
correspondente no repositório PostgreSQL configurado.

## 7. Medir a linha de base conceitual

Antes dos embeddings, rode a nova suíte em modo lexical. Ela usa perguntas em
português sem depender dos identificadores exatos; falhas aqui são esperadas e
formam a linha de base, não um erro de instalação:

```bash
.venv/bin/python -m mflab_knowledge db-evaluate \
  --mode lexical \
  --suite evaluations/mfsim-ng-semantic-pilot.json \
  --output data/mfsim-ng-zero-flow/semantic-lexical-baseline.generated.json \
  --color always
```

O comando pode retornar status 1 se alguma expectativa não for encontrada.

## 8. Calcular os embeddings

```bash
.venv/bin/python -m mflab_knowledge db-embed \
  --batch-size 4 \
  --color always

.venv/bin/python -m mflab_knowledge db-embedding-status --color always
```

O modelo padrão é `Qwen/Qwen3-Embedding-0.6B`, fixado por revisão, com 1.024
dimensões e sequências de até 4.096 tokens neste piloto. `--device auto` usa GPU
quando disponível e CPU caso contrário. Se ocorrer falta de memória na GPU,
repita com `--batch-size 2`; os lotes confirmados são reutilizados. Para forçar o
fallback, acrescente `--device cpu`.

Uma segunda execução sem mudanças deve carregar apenas metadados do banco,
informar todos os vetores como reutilizados e não carregar o modelo na memória.

## 9. Buscar e avaliar os três modos

Opcionalmente, copie `retrieval.example.toml` para `retrieval.toml` e ajuste os
limites globais do serviço. O arquivo local é ignorado pelo Git. Sem ele, os
mesmos padrões do exemplo são utilizados. Não inclua nomes de casos ou símbolos
nessa política; expectativas específicas pertencem às suítes de avaliação.

```bash
.venv/bin/python -m mflab_knowledge db-search \
  --mode hybrid \
  --query "Onde o gerenciador de partículas é inicializado?" \
  --project MFSim-NG \
  --branch master \
  --limit 5 \
  --color always

.venv/bin/python -m mflab_knowledge db-evaluate \
  --mode semantic \
  --suite evaluations/mfsim-ng-semantic-pilot.json \
  --output data/mfsim-ng-zero-flow/semantic-evaluation.generated.json \
  --color always

.venv/bin/python -m mflab_knowledge db-evaluate \
  --mode hybrid \
  --suite evaluations/mfsim-ng-semantic-pilot.json \
  --output data/mfsim-ng-zero-flow/hybrid-semantic-evaluation.generated.json \
  --color always

.venv/bin/python -m mflab_knowledge db-evaluate \
  --mode hybrid \
  --suite evaluations/mfsim-ng-pilot.json \
  --output data/mfsim-ng-zero-flow/hybrid-exact-evaluation.generated.json \
  --color always
```

O objetivo é melhorar as perguntas conceituais sem regredir a suíte de símbolos
exatos. O modo semântico usa distância cosseno exata; o modo híbrido combina os
rankings lexical e vetorial por RRF. Relações genéricas de pares fonte/header,
símbolos, vizinhança de chunks e bundles inferidos por ancestral comum alimentam
uma terceira consulta sob os mesmos filtros SQL. No máximo dois documentos
relacionados são intercalados logo depois da evidência de origem, sem substituir
sua posição. Essa etapa reutiliza os embeddings existentes.

## 10. Limites desta etapa

- ainda não expõe API ou porta de rede;
- a ACL é aplicada no SQL antes de retornar o texto;
- a inferência ocorre localmente, mas o primeiro uso precisa baixar o modelo;
- não existe ainda um worker permanente para recalcular após webhooks;
- o schema pode ser reconstruído a partir dos JSONLs e das fontes autorizadas;
- para um serviço executado por outro usuário Linux, será criada depois uma role
  dedicada em vez de reutilizar `max`.
