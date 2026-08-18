# Operação não assistida

O serviço agendado reutiliza exatamente o mesmo pipeline incremental do comando
`index-all`. Não existe uma segunda implementação para o modo automático e os
repositórios continuam definidos exclusivamente em `repositories.toml`.

## Runner gerenciado

Uma execução manual equivalente à execução do `systemd` pode ser feita com:

```bash
.venv/bin/python -m mflab_knowledge run-scheduled \
  --config repositories.toml \
  --env-file .env \
  --state-dir state \
  --batch-size 4 \
  --device cpu \
  --color always
```

O runner acrescenta ao pipeline:

- trava de processo mantida pelo sistema operacional;
- liberação automática da trava quando o processo termina ou é morto;
- estado atômico em `state/last-run.json`;
- histórico limitado em `state/runs/`;
- etapa, progresso, velocidade e ETA da operação em andamento;
- identificação de uma execução anterior interrompida;
- retorno zero quando uma segunda chamada encontra o indexador já ativo.

O estado não contém token GitLab, senha ou URL do PostgreSQL. O diretório
`state/` é privado, gerado e ignorado pelo Git.

Para consultar a última execução sem abrir o log:

```bash
.venv/bin/python -m mflab_knowledge run-status \
  --state-dir state \
  --color always
```

## Instalação do timer

No Linux com `systemd`, o instalador valida o ambiente, gera as unidades com os
caminhos absolutos locais, protege `.env` com modo `0600` e habilita o timer:

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

Nenhum usuário, diretório, repositório ou intervalo está fixado nas unidades.
O instalador usa os modelos em `deploy/systemd/` e grava as unidades renderizadas
em `/etc/systemd/system/`.

O dispositivo também é configurável. O padrão `cpu` mantém o indexador
incremental independente da GPU usada simultaneamente pela API e pelo servidor
LLM. Em máquinas com uma GPU dedicada ao indexador, use `--device cuda` ou o
identificador apropriado ao reinstalar a unidade.

O timer espera o intervalo configurado depois que a execução anterior fica
inativa. A própria unidade `systemd` e a trava do runner impedem sobreposição.
Se uma execução falhar, o timer tentará novamente no ciclo seguinte. Se a
máquina reiniciar durante embeddings, os checkpoints confirmados são preservados
e o próximo ciclo processa somente os chunks ainda ausentes.

## Inspeção administrativa

Os comandos administrativos principais são:

```bash
systemctl status mflab-knowledge-index.timer --no-pager
systemctl status mflab-knowledge-index.service --no-pager
systemctl list-timers mflab-knowledge-index.timer --no-pager
journalctl -u mflab-knowledge-index.service --since today
```

O `journald` recebe a saída do processo e aplica sua própria retenção e rotação.
Não é necessário manter um `tee` ou terminal aberto. Uma execução imediata pode
ser solicitada com:

```bash
sudo systemctl start mflab-knowledge-index.service
```

## API RAG permanente

A API usa uma unidade separada do indexador. O timer pode atualizar o banco
enquanto o processo HTTP permanece disponível; cada consulta abre conexões
curtas e enxerga as transações já confirmadas.

```bash
./scripts/install-api-systemd.sh \
  --project-dir "$PWD" \
  --user "$USER" \
  --group "$(id -gn)" \
  --port 8765
```

O serviço é habilitado no boot, reinicia após falhas e escuta somente em
`127.0.0.1`. O instalador verifica `/health` depois do restart. Inspeção:

```bash
systemctl status mflab-knowledge-api.service --no-pager
journalctl -u mflab-knowledge-api.service --since today
curl --fail http://127.0.0.1:8765/health
```

Para alterar intervalo, usuário, batch ou dispositivo do indexador, execute novamente
`install-systemd.sh`. Para alterar a porta da API, execute novamente
`install-api-systemd.sh`. Cada instalador substitui somente as unidades sob sua
responsabilidade e recarrega o `systemd`.

## Arquivos e permissões

- `.env`: credenciais locais, modo `0600`, nunca versionado;
- `repositories.toml`: catálogo de fontes autorizadas;
- `state/index.lock`: trava operacional, sem credenciais;
- `state/last-run.json`: estado atual ou resultado mais recente;
- `state/runs/*.json`: histórico limitado das execuções;
- `cache/`, `inventory/` e `data/`: artefatos incrementais já existentes.

O serviço roda como o usuário informado ao instalador. Esse usuário precisa ler
`.env`, acessar o PostgreSQL local e escrever somente nos diretórios pertencentes
ao indexador. Os repositórios científicos remotos continuam sendo acessados com
token `read_repository`.
