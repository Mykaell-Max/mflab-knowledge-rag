# Handoff operacional — MFLab Knowledge RAG

> Fonte de continuidade para novas conversas e colaboradores.
>
> Estado atualizado em **18 de agosto de 2026**, na versão **0.27.1**. Antes de
> agir, confirme o estado atual com `git status` e `git log -1 --oneline`, pois o
> repositório pode ter avançado.

## 1. Instrução para uma nova conversa

Use a seguinte instrução ao abrir outra conversa:

> Leia `HANDOFF.md` por completo antes de agir. Continue a partir de “Próxima
> ação recomendada” e não refaça instalações ou migrações já concluídas. Trate
> os repositórios científicos como somente leitura. Não faça push, não coloque
> credenciais em arquivos versionados e não introduza nomes de projetos,
> branches, símbolos ou caminhos científicos no motor genérico. Dados
> específicos são permitidos apenas em configuração e suítes de avaliação.

Também são referências obrigatórias:

- `README.md`: uso e contratos públicos do projeto;
- `docs/architecture.md`: decisões de arquitetura e segurança;
- `docs/operations.md`: operação não assistida e unidades `systemd`;
- `repositories.example.toml`: contrato genérico multi-repositório;
- `inventory-policies.toml`: perfis de inventário declarativos;
- `evaluations/`: gabaritos versionados, separados do motor.

O `HANDOFF.md` localizado na pasta pai registra a visão original do ecossistema.
Este arquivo é a fonte atual para o estado de implementação e operação.

## 2. Objetivo do projeto

O projeto fornece um serviço interno de conhecimento para o MFLab com:

- inventário automático e somente leitura de repositórios autorizados;
- preservação de repositório, branch, commit, caminho, linhas e classe de acesso;
- normalização e deduplicação entre branches e commits;
- busca lexical e semântica combinadas;
- respostas produzidas por modelo local, sustentadas por fontes citáveis;
- atualização incremental e não assistida;
- arquitetura genérica para múltiplos repositórios com estruturas distintas.

RAG foi adotado antes de qualquer fine-tuning porque o conhecimento muda, exige
citações e precisa permitir atualização, remoção e controle de acesso sem novo
treinamento.

## 3. Princípios que não podem regredir

1. **Fontes científicas são somente leitura.** O indexador usa mirror e snapshots
   imutáveis; não executa checkout nem altera o worktree fornecido.
2. **Nada científico fica hardcoded no motor.** Repositórios, branches canônicas,
   inclusões e exclusões ficam em TOML. Perguntas e caminhos esperados ficam em
   suítes de avaliação.
3. **Credenciais permanecem locais.** `.env` e `repositories.toml` são ignorados
   pelo Git. Tokens não aparecem em URL, argumentos, logs ou manifestos.
4. **O GitLab é a fonte canônica.** Um evento ou timer apenas solicita nova
   reconciliação; o conteúdo sempre é relido pela origem autorizada.
5. **Branches não são misturadas.** Ocorrências compartilhadas são deduplicadas,
   mas cada resposta preserva branch e commit.
6. **Projetos não são misturados silenciosamente.** O pacote de contexto marca
   escopos diferentes e exige citações por fonte.
7. **A automação é idempotente.** Mirrors, inventários, parses, banco e embeddings
   são reutilizados quando seu fingerprint não mudou.
8. **O assistente não deve fazer push.** Alterações podem ser implementadas,
   testadas e commitadas localmente; o push é realizado pelo usuário.

## 4. Estado implementado

### 4.1 Catálogo e inventário

- Configuração multi-repositório por `repositories.toml`.
- Origem por clone local ou URL HTTPS, sem exigir worktree permanente.
- Descoberta automática de todas as branches remotas.
- Branch canônica explícita ou derivada de `remote_default`.
- Filtros opcionais de branches por configuração.
- Mirror isolado e snapshot imutável por commit.
- Cache de inventário por commit, perfil, política e classe de acesso.
- Árvore de branches com relação contra a canônica.
- Logs coloridos, progresso, heartbeat, duração e resumos operacionais.
- Perfis de inventário declarativos e externos ao motor.

### 4.2 Normalização e armazenamento

- Normalização incremental de C, C++, headers, Fortran, CMake, Markdown, JSON,
  shell e outros formatos textuais autorizados.
- Preservação de títulos ou símbolos heurísticos, intervalo de linhas e
  ocorrências em cada branch e commit.
- Deduplicação de conteúdo idêntico e reutilização de embeddings.
- PostgreSQL como catálogo transacional e índice lexical.
- pgvector com vetores locais de 1.024 dimensões.
- Carga isolada por `repository_id`, sem apagar os demais corpora.

### 4.3 Recuperação e geração

- Busca lexical, semântica e híbrida.
- Filtros de projeto, branch, prefixo de caminho e classe de acesso aplicados
  antes do retorno do conteúdo.
- Reranking e expansão de contexto estrutural genéricos.
- Pacote de contexto com orçamento, fontes `S1`, `S2`, etc. e instrução contra
  prompt injection presente no conteúdo indexado.
- Geração local via API compatível com OpenAI.
- Verificação de citações, cobertura, abstenção sem evidência e aviso de múltiplos
  escopos.
- API local com `/health`, `/status`, `/repositories`, `/search`, `/context` e
  `/ask`.

### 4.4 Operação automática

- `index-all` executa sincronização, normalização, carga e embeddings.
- `run-scheduled` acrescenta trava, estado atômico, histórico e retomada.
- Timer `systemd` executa a reconciliação aproximadamente a cada cinco minutos.
- O polling foi considerado suficiente para o piloto; webhook não é prioridade.
- API, servidor LLM e indexador possuem unidades `systemd` separadas.
- O processamento incremental de embeddings usa checkpoints transacionais.

## 5. Estado observado no servidor Morgoth

Ambiente do piloto:

- Ubuntu Linux;
- AMD Ryzen 7 5800X, 8 núcleos e 16 threads;
- 128 GB de RAM;
- NVIDIA GeForce RTX 5060 Ti com 16 GB de VRAM;
- PostgreSQL com pgvector;
- modelo de embeddings `Qwen/Qwen3-Embedding-0.6B`;
- servidor vLLM com `Qwen/Qwen3-8B-FP8`.

Serviços locais:

- RAG API: `127.0.0.1:8765`;
- vLLM: `127.0.0.1:8000`;
- indexação: `mflab-knowledge-index.service` e timer associado;
- API: `mflab-knowledge-api.service`;
- modelo gerador: `mflab-knowledge-llm.service`.

Configuração operacional validada na máquina:

- vLLM limitado a `0.68` da memória da GPU;
- indexador em `cuda`, batch de embeddings `4`;
- aproximadamente 2,9 GiB de VRAM livres ao final da validação;
- 411 embeddings novos processados em aproximadamente 63 segundos, sem OOM.

Esses valores pertencem à configuração local da Morgoth. O instalador genérico
mantém CPU como padrão seguro e não presume modelo, GPU ou capacidade.

## 6. Corpora atualmente indexados

O catálogo local contém dois repositórios configurados, sem nomes fixados no
motor:

| Projeto | Branches | Commits únicos | Chunks com embeddings |
|---|---:|---:|---:|
| MFSim-NG | 17 | 17 | 12.332 |
| MFSim CMake | 107 | 104 | 83.468 |
| **Total** | **124** | **121** | **95.800** |

Os números são um snapshot observado em 18 de agosto de 2026 e podem crescer
quando o GitLab receber novos commits ou branches. O estado atual deve ser
consultado pela API e pelos manifestos gerados, não presumido pelo código.

## 7. Validações concluídas

- Testes automatizados do pacote aprovados.
- Inventários das branches canônica e de trabalho gerados sem modificar o clone.
- Reutilização integral de inventários, documentos e embeddings quando não há
  mudanças.
- Atualização incremental detectou novo conteúdo do MFSim CMake e calculou
  somente os embeddings pendentes.
- Busca canônica do MFSim CMake preservou projeto, branch, commit e ocorrências.
- Suíte conceitual corrigida do MFSim-NG passou em 5/5 casos e 10/10
  expectativas.
- Regressão de símbolos passou em 5/5 casos.
- Avaliação ponta a ponta de `/ask` para o MFSim-NG passou em 4/4 casos, com
  cobertura média de citações de 100%.
- O avaliador ponta a ponta confirma automaticamente que todas as fontes
  respeitam os filtros de projeto, branch, prefixo de caminho e classe de acesso.
- Falta de evidência indexada produz abstenção em vez de resposta inventada.
- Endpoint externo de geração é rejeitado; o modelo autorizado é local.
- O timer e os serviços permanecem ativos sem exigir terminal aberto.

## 8. Problemas encontrados e respectivas soluções

### Inventário inicialmente excessivo

O primeiro inventário incluiu builds, documentação gerada e dezenas de milhares
de arquivos desconhecidos. A enumeração foi trocada por arquivos versionados e
perfis declarativos, reduzindo o piloto do MFSim-NG para o conteúdo indexável.

### Consulta SQL com tipo ambíguo

Uma consulta filtrada falhou porque o PostgreSQL não conseguia inferir o tipo de
um parâmetro nulo. O SQL foi corrigido e coberto por testes.

### Embeddings e compatibilidade CUDA

O ambiente inicialmente não possuía headers Python exigidos pelo Triton. Os
headers compatíveis foram instalados e o cálculo em CUDA passou a funcionar.

### Recuperação contextual

Versões iniciais deslocavam evidências relevantes ou usavam expectativas
científicas incorretas. O contexto estrutural foi tornado genérico, a expansão
foi limitada e o gabarito foi corrigido com base no mecanismo realmente presente
no código.

### Falta de memória na GPU durante a indexação agendada

vLLM, API de embeddings e indexador carregados simultaneamente esgotaram a VRAM.
O modo CPU evitou OOM, mas era lento. A configuração local final reduziu a fração
do vLLM para `0.68` e manteve o indexador em CUDA com batch `4`, recuperando a
velocidade com margem de memória.

## 9. Configurações locais que não estão no Git

Uma nova conversa não deve recriar estes arquivos sem antes verificar se já
existem no servidor:

- `.env`: GitLab, PostgreSQL e chave opcional do LLM;
- `repositories.toml`: repositórios autorizados e suas branches canônicas;
- `retrieval.toml`: limites da recuperação;
- `generation.toml`: endpoint e modelo gerador locais;
- `cache/`, `inventory/`, `data/`, `state/` e `logs/`: artefatos operacionais.

Caminhos usados durante o piloto:

- projeto no laboratório: `/home/max/Desktop/mflab-knowledge-rag`;
- clone científico do NG: `/home/max/Desktop/mfsim-ng`;
- workspace de desenvolvimento no Windows: pasta `mflab-knowledge-rag` dentro
  do workspace do handoff.

## 10. Decisões recentes

- A reconciliação a cada cinco minutos atende o piloto; webhooks podem ser
  revisitados apenas se a latência se tornar um problema real.
- O painel web é desejável, mas não deve interromper a validação da qualidade do
  corpus e da recuperação.
- A próxima validação científica deve cobrir o MFSim CMake, pois sua indexação e
  busca básica passaram, mas ainda não existe uma suíte de respostas equivalente
  à do MFSim-NG.
- Gabaritos específicos de cada projeto não são hardcode do motor. Eles são
  artefatos de teste versionados e precisam representar evidência verificada.

## 11. Próxima ação recomendada

Criar e validar uma suíte de avaliação do **MFSim CMake** com perguntas reais e
fontes esperadas verificadas no código. A suíte deve avaliar:

1. recuperação lexical de identificadores e arquivos;
2. recuperação conceitual em português e inglês;
3. respostas de `/ask` com citações;
4. preservação da branch canônica e do commit;
5. ausência de mistura silenciosa com MFSim-NG;
6. abstenção quando a evidência não existir.

As perguntas e os caminhos esperados devem ficar em `evaluations/`. Nenhum deles
deve ser incorporado às regras do recuperador.

Uma primeira suíte piloto foi preparada para ciclo do DPM, pressão/Poisson,
refinamento adaptativo e abstenção. Ela ainda precisa ser executada na Morgoth.
Comunicação MPI do DPM e saída Lagrangiana não foram promovidas porque a busca
inicial recuperou mecanismos de IB, VOF ou HDF5 genérico, sem confirmação
suficiente do mecanismo pretendido.

Depois dessa validação, a ordem recomendada é:

1. ampliar avaliações multi-repositório e multi-branch;
2. substituir âncoras heurísticas por parsing estrutural genérico para C, C++,
   Fortran, CMake e casos configurados;
3. adicionar autenticação e política de acesso multiusuário antes de expor a API
   fora do loopback;
4. construir o painel web de operação e busca;
5. adicionar conectores autorizados para issues, merge requests, documentos e
   demais fontes do laboratório;
6. avaliar MCP e ferramentas controladas somente após consolidar a camada de
   leitura.

## 12. Forma de colaboração adotada

### 12.1 Separação entre as máquinas

- O Windows do usuário é o workspace de colaboração e edição do código. Ele não
  é o ambiente de execução do serviço.
- PostgreSQL, pgvector, CUDA, vLLM, modelos, indexações reais, unidades `systemd`
  e testes integrados com os corpora devem ser executados na **Morgoth**, a
  máquina Ubuntu do laboratório.
- Testes unitários pequenos e independentes da infraestrutura podem ser
  executados no workspace de desenvolvimento quando forem compatíveis. Isso não
  substitui a validação final na Morgoth.
- Não tentar instalar ou reproduzir a stack Linux/GPU no Windows apenas para
  validar uma alteração.
- O acesso remoto costuma ser realizado pelo RustDesk. Uma conexão SSH direta a
  partir de fora da rede da UFU não deve ser presumida, pois pode ser bloqueada
  pela rede institucional.
- Os comandos destinados ao laboratório devem considerar como raiz do projeto
  `/home/max/Desktop/mflab-knowledge-rag`, mas scripts e código versionados
  continuam genéricos e não podem fixar esse caminho.

### 12.2 Formato preferido para comandos de teste

- Quando o usuário precisar executar comandos na Morgoth, entregar **um único
  bloco Bash completo e copiável**, chamado de RunBlock na conversa, em vez de
  vários blocos ou comandos soltos.
- O bloco deve poder ser colado inteiro no terminal e executar as etapas na ordem
  correta, incluindo atualização do repositório, instalação editável quando
  necessária, validações e resumo final.
- Blocos longos devem mostrar títulos de etapa, cores, progresso, sucessos,
  avisos, erros e o caminho do log. Operações demoradas devem fornecer heartbeat,
  porcentagem ou outra evidência clara de que continuam ativas.
- O tempo esperado deve ser informado antes da execução quando houver download,
  carregamento de modelo, inventário amplo ou cálculo de embeddings.
- O terminal deve permanecer aberto ao final do bloco, inclusive quando uma
  etapa falhar, para que o resultado possa ser copiado. A falha deve ser
  preservada no resumo e no código de status interno, sem encerrar a janela antes
  da pausa final.
- O bloco não deve imprimir tokens, senhas, URLs de banco com credenciais ou o
  conteúdo integral de `.env`.
- Sempre que possível, o bloco deve salvar uma cópia do resultado em `logs/`,
  sem depender exclusivamente do histórico visível do terminal.

### 12.3 Fluxo Git e estilo de colaboração

- As alterações são implementadas e verificadas no workspace compartilhado.
- Um commit identificável é criado depois da validação local.
- O assistente **não faz push**. O usuário realiza o push e informa quando o
  commit estiver disponível.
- Na Morgoth, o RunBlock começa atualizando o clone e confirma o commit realmente
  testado.
- Não apagar mudanças do usuário nem executar operações Git destrutivas.
- As explicações e os logs destinados ao usuário devem ser apresentados em
  português claro.
- Evitar trabalho operacional manual recorrente. Configurações locais podem
  exigir uma preparação inicial, mas sincronização, indexação, retomada e
  monitoramento devem ser automatizados.
