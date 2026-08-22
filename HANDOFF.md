# Handoff operacional — MFLab Knowledge RAG

> Fonte de continuidade para novas conversas e colaboradores.
>
> Estado atualizado em **22 de agosto de 2026**, na versão candidata **0.44.10**. Antes de
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
- API local com `/health`, `/status`, `/repositories`, `/structure`, `/search`,
  `/context` e `/ask`.
- Interface web integrada em `/ui`, com painel operacional, busca, perguntas e
  fontes, preenchida dinamicamente pela API.
- Bind LAN opt-in protegido por chave Bearer forte criada em `.env`; loopback
  continua sendo o padrão e a automação local não precisa distribuir a chave.

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

- RAG API: `0.0.0.0:8765` na instalação LAN validada;
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
- A suíte híbrida do MFSim CMake passou em 3/3 casos e 6/6 expectativas, com
  recall de 100% e MRR 1,000.
- A avaliação de respostas do MFSim CMake passou em 4/4 casos, incluindo
  abstenção, com cobertura média de citações de 100% e todas as fontes restritas
  a `MFSim CMake master@3cdbff4811a9`.
- O pico observado nessa avaliação foi 12.969 MiB de VRAM, restando
  aproximadamente 2.879 MiB na RTX 5060 Ti.
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
- O painel web inicial foi integrado depois da validação científica dos dois
  corpora; ele consome os contratos existentes e não conhece projetos fixos.
- A suíte híbrida e a suíte de respostas do MFSim CMake já passaram integralmente.
- Gabaritos específicos de cada projeto não são hardcode do motor. Eles são
  artefatos de teste versionados e precisam representar evidência verificada.

## 11. Próxima ação recomendada

A avaliação real da 0.44.9 confirmou que o grafo encontrou os caminhos corretos
e que a montagem seccional preservou mais conteúdo que a composição global. Os
dois casos falharam apenas em completude. Na malha, uma seção e sua continuação
terminaram por limite, deixando 1.212 caracteres. No DPM, 12 afirmações foram
sustentadas e a resposta chegou a 4.074 caracteres, mas a auditoria global não
associou a seção de domínio à faceta correspondente. O caderno ainda atribuía
somente uma fonte a cada seção de malha.

A 0.44.10 corrige essas interfaces sem alterar o grafo. Seções comuns recebem
até 2.048 tokens e seções detalhadas até 3.072, com no máximo duas continuações
locais. O assunto explícito da pergunta permanece como guarda mesmo quando é
frequente nas fontes, permitindo incluir um complemento estrutural de fábrica
ou coordenação sem admitir subsistemas vizinhos que só compartilham verbos
genéricos. Fontes diretamente observadas ficam separadas dos complementos para
que facetas distintas não sejam fundidas acidentalmente.

Quando uma resposta precisa do salvamento determinístico, os títulos Markdown
originais das seções passam a ser preservados. A auditoria de completude recebe,
para cada faceta, somente as afirmações sustentadas pelas fontes primárias de sua
seção. Isso reduz confusão do modelo local sem transformar planejamento em
prova: toda afirmação continua precisando passar pela auditoria fonte a fonte.

A 0.44.8 confirmou que a composição global não é adequada ao Qwen 8B deste
piloto. Ela concluiu em ambos os casos, mas reduziu a malha de 19 para 7
afirmações sustentadas. No DPM, precisou de três tentativas, terminou por limite,
produziu sete blocos de código rejeitados e deixou apenas 747 caracteres úteis.
Uma continuação global apenas ampliaria esse comportamento. O grafo permaneceu
correto; a regressão ocorreu exclusivamente na reescrita final.

A 0.44.9 retira a composição global do caminho padrão e volta à montagem
determinística das seções, sem uma chamada que reescreva fatos já apurados. O
caderno passa a atribuir fontes por aspecto e pelo assunto da pergunta. A
comparação usa apenas metadados autorizados de caminho, título, formato e tipo
de fonte; prefixos morfológicos relacionam formas como `configuration` e
`configure`. Termos presentes na maior parte das fontes são ignorados
dinamicamente, sem lista de projetos. Um verbo genérico como `configure` não
pode puxar um subsistema vizinho se esse arquivo não corresponder ao assunto ou
a um termo específico da faceta.

Lacunas podem reutilizar uma fonte já atribuída a outra seção quando seu caminho
ou símbolo é o melhor candidato para a faceta. Isso corrige o caso em que fontes
de `Domain` já existiam no pacote, mas ficavam presas à seção errada. A
distribuição round-robin de todos os nós restantes foi removida; cada seção
recebe no máximo um complemento com correspondência real. A regra é genérica e
não contém projetos, branches, caminhos ou símbolos científicos.

A próxima validação deve observar o caderno antes da nota final. Para uma
pergunta ampla, as seções precisam representar papéis distintos e não carregar
componentes incidentais. O critério principal passa a ser a resposta completa:
se configuração, avanço e integração forem explicados com fontes próprias, a
arquitetura seccional estará pronta para uma etapa posterior de auditoria e
complemento localizado, nunca uma reescrita global.

A validação real da 0.44.7 não chegou a avaliar o compositor. O serviço tentou a
nova etapa nos dois casos, mas o vLLM recusou a soma de fontes reidratadas,
rascunhos seccionais e uma reserva de 3.072 tokens. O fallback preservou
integralmente as respostas da 0.44.6; por isso os textos e as métricas ficaram
idênticos. O journal confirmou `contexto excedeu a janela do gerador local` e
também mostrou a mesma pressão na descoberta posterior de suporte.

A 0.44.8 torna a composição adaptativa sem reduzir antecipadamente a resposta a
um texto curto. Rascunhos intermediários, que não são evidência, são limitados a
uma janela com início e fim preservados. A primeira composição reserva até
2.048 tokens, ainda suficiente para uma explicação técnica longa. Se o provedor
recusar a janela, até duas novas tentativas compactam progressivamente rascunhos
e evidências; somente na última tentativa a reserva de saída cai para 1.536
tokens. Todas as fontes originais continuam disponíveis para a auditoria final,
mesmo quando o compositor recebe uma janela textual menor. A resposta registra
número de tentativas, redução e limite efetivamente reservado.

A próxima ação é repetir a suíte e confirmar primeiro
`section_composition=True`. Somente depois deve ser julgada a qualidade do novo
texto. Se a composição concluir, não voltar a alterar o grafo antes de examinar
a resposta completa, as afirmações removidas e a cobertura por aspecto.

A validação real da 0.44.6 encerrou o ajuste da recuperação estrutural para o
caso de malha: `domain.cpp`, `mesh_manager.cpp` e
`mtree_domain_filling.cpp` chegaram juntos às dez fontes finais. A resposta
preservou 19 afirmações sustentadas, sem incerteza ou afirmação rejeitada. No
DPM, `Domain::setup`, `Domain::advance`, `DPMManager::advance` e
`DPMParticle::updatePosition` também chegaram à fronteira. Portanto, aumentar
ações, fontes ou raio do grafo deixou de ser a intervenção indicada.

O relatório expôs o gargalo seguinte. O caderno gerava de duas a três respostas
locais e apenas as concatenava; depois, a remoção segura de afirmações fracas
deixava um conjunto sustentado, porém curto e sem uma nova organização global.
A resposta do DPM terminou com 2.295 caracteres e se concentrou na atualização
de uma partícula, mesmo possuindo quase oito mil caracteres de evidência sobre
configuração, coordenação e integração com o domínio.

A 0.44.7 acrescenta uma composição final fundada depois das sínteses locais.
Os rascunhos de seção são explicitamente tratados como texto não confiável e
nunca como evidência. O compositor recebe novamente um pacote limitado das
fontes autorizadas e os aspectos da pergunta, elimina repetições e componentes
incidentais e organiza um fluxo único somente quando chamadas ou código mostram
as transições. Em seguida, o resultado completo passa pelas mesmas verificações
de código literal, citações, sustentação e cobertura. Se a composição exceder a
janela ou ficar indisponível, as seções fundadas anteriores são preservadas.

A próxima ação é executar a mesma suíte na Morgoth e verificar principalmente:

1. `context.section_composition` deve ser `true`;
2. os três caminhos da malha devem permanecer presentes;
3. a resposta do DPM deve usar configuração, `DPMManager::advance`, avanço das
   partículas e integração em `Domain`, sem desviar para componentes incidentais;
4. nenhuma melhora de completude pode reduzir a cobertura de citações ou admitir
   afirmações não sustentadas.

A validação da 0.44.5 repetiu exatamente o conjunto da 0.44.4 na pergunta de
malha. A reserva upstream foi ocupada por `mesh_factory.hpp::getInstance`, pois
o seletor considerava o cabeçalho e a implementação `mesh_factory.cpp` como
caminhos diversos. O coordenador continuou fora, embora os métodos do
`MeshManager` e a implementação adaptativa estivessem presentes. O DPM manteve
seus caminhos esperados, reforçando que o problema é a última escolha de fontes
da malha e não ausência no índice.

A 0.44.6 mede diversidade por família de arquivo sem extensão. Um par de
cabeçalho/implementação compartilha uma família para fins de reserva, embora os
chunks e suas citações continuem independentes. Ao escolher o papel upstream, o
seletor prefere outra família antes de repetir a âncora. A regra é aplicável a
qualquer extensão reconhecida por `PurePosixPath` e não conhece linguagem,
projeto, branch ou símbolo. A próxima ação é repetir a suíte. Se os três caminhos
esperados entrarem juntos, congelar a recuperação e extrair as respostas e as
auditorias detalhadas do relatório para tratar completude.

A validação real da 0.44.4 comprovou a navegação por ciclo de vida. A fronteira
de malha passou a conter `MeshManager::initialize`, `configure` e
`runInitialRemesh`, o caminho `mesh_manager.cpp` entrou no pacote e a resposta
teve 3.639 caracteres com 19 afirmações sustentadas. Entretanto, o chamador
`domain.cpp` foi expulso pelas dez fontes finais. O DPM continuou recuperando
seus três caminhos obrigatórios, embora a geração tenha oscilado para 4.492
caracteres. Portanto, ampliar vocabulário, ações ou contexto já não é a próxima
medida: os dois lados da aresta existem, mas competem entre si na seleção final.

A 0.44.5 reserva papéis estruturais genéricos. O candidato lexical mais forte é
mantido como âncora e, quando observados, o melhor `agent_callers_evidence` e o
melhor `agent_callees_evidence` recebem uma vaga antes da diversidade adicional.
Assim, uma explicação de fluxo pode conservar simultaneamente o coordenador e
sua implementação. A regra opera apenas sobre arestas resolvidas e chunks já
autorizados; não contém caminhos, símbolos ou projetos. A próxima validação deve
confirmar os três caminhos da malha juntos. Se isso ocorrer, o próximo trabalho
deve analisar a resposta e a auditoria de cobertura, não continuar alterando a
recuperação.

A validação real da 0.44.3 mostrou que a coerência do DPM melhorou: os três
caminhos obrigatórios foram preservados, a fronteira reuniu configuração,
movimento, partícula e integração com `Domain::setup`/`Domain::advance`, e a
resposta manteve 7.363 caracteres e 23 afirmações sustentadas. A incompletude
restante é da síntese/cobertura, não da presença dos arquivos esperados. A malha
continuou sem `mesh_manager.cpp`. A inspeção do motor encontrou a causa: os
termos genéricos `initialization`, `initialize` e `setup` eram removidos antes do
ranqueamento estrutural. Assim, o chunk `Domain::setup`, que contém a ponte real
para o gerenciador, recebia menos relevância que símbolos laterais cujo nome
continha apenas o substantivo da pergunta.

A 0.44.4 mantém essas palavras como stopwords lexicais, mas deriva marcadores
estruturais genéricos para início/configuração, execução/avanço e
finalização. As famílias aceitam variações comuns em inglês e português. Esses
marcadores são usados somente para ordenar observações e fronteiras já
autorizadas; não criam símbolos, caminhos ou fatos e não ampliam a ACL. A próxima
ação é repetir a suíte e verificar se `Domain::setup` origina a aresta resolvida
até a implementação do gerenciador. Depois disso, a resposta real deve ser
inspecionada antes de qualquer relaxamento do critério de completude.

A validação real da 0.44.2 confirmou a recuperação de código: na resposta de
DPM, sete cercas receberam uma citação determinística, apenas três foram
removidas e a saída cresceu de 1.918 para 7.389 caracteres. A pergunta de malha
produziu 3.144 caracteres e perdeu somente uma cerca. Ambas terminaram com
`finish_reason=stop`, todas as afirmações finais foram sustentadas e a API e o
timer permaneceram saudáveis. A suíte ainda marcou 0/2 porque as duas respostas
foram corretamente declaradas como subconjuntos sustentados: a malha não reteve
`mesh_manager.cpp`, e o DPM perdeu o chunk da partícula e não comprovou toda a
integração de domínio. A fronteira mostrou o motivo: a diversidade de caminhos
promoveu operações laterais, como saída, temporização e outro solucionador,
antes de métodos repetidos do subsistema perguntado.

A 0.44.3 transforma a continuação terminal em uma pequena busca em feixe
determinística. Vizinhanças locais e arestas de chamada dividem igualmente cada
rodada, ambas amostradas do início ao fim da fronteira; a leitura local usa um
raio maior e há uma terceira rodada somente de banco. Na seleção, diversidade
é aplicada primeiro apenas entre candidatos que compartilham vocabulário com a
pergunta. Métodos relevantes repetidos de um mesmo coordenador precedem nós
apenas conectados. Não há nome de projeto, arquivo, símbolo ou mecanismo nessa
política. A próxima ação é repetir a suíte real e comparar os caminhos, a
cobertura e o tamanho das respostas com a execução da 0.44.2.

A validação real da 0.44.1 preservou dez fontes e fez ambos os casos terminarem
com `finish_reason=stop`, mas ainda removeu nove blocos de código. A resposta de
DPM ficou com 1.918 caracteres e a de malha com 1.965; todas as afirmações finais
foram sustentadas, mas a cobertura permaneceu parcial. O grafo passou a reter
`Domain::setup`, `Domain::advance` e outra operação do gerenciador. Na pergunta
de malha, a implementação do gerenciador continuou ausente apesar de ser parte
real do fluxo: a busca chegou a um método vizinho no coordenador, mas a continuação
terminal percorria somente chamadas e não seus chunks locais adjacentes.

A 0.44.2 trata código literal como descoberta determinística de proveniência.
Quando linhas completas correspondem a exatamente uma fonte autorizada, o
backend anexa sua citação mesmo que o modelo a tenha colocado longe da cerca.
Trechos ambíguos entre fontes, alterados ou cortados continuam removidos. O
resumo passa a mostrar separadamente blocos removidos e citações anexadas.
Na exploração, cada rodada terminal intercala leituras de vizinhanças locais,
incluindo a cauda da fronteira, e chamadas resolvidas. Chunks vizinhos voltam a participar da
seleção e podem originar o salto estrutural seguinte. Isso é genérico por
identidade de chunk e ordem de relevância; não conhece nomes científicos. A
próxima ação é executar a mesma suíte na Morgoth dentro do `tmux`.

A validação real da 0.44.0 passou operacionalmente, manteve a API saudável e
restaurou a automação, mas reprovou 0/2 expectativas científicas. A checagem de
linhas atomizadas funcionou: nenhuma afirmação rejeitada sobreviveu. A política
de código, entretanto, removeu oito blocos somente porque seus chunks continham
alguma redução, mesmo quando as linhas citadas estavam integralmente visíveis.
Isso consumiu a geração com exemplos que depois foram descartados, reduziu a
resposta de DPM a 1.095 caracteres e deixou sua continuação com
`finish_reason=length`. A fronteira encontrou `Domain::setup`, `Domain::advance`
e outra operação do gerenciador, mas o pacote final de oito fontes ainda perdeu
parte dessa cauda; na pergunta de malha também perdeu um resultado-base relevante.

A 0.44.1 aceita cercas de código provenientes de fontes reduzidas somente quando
o trecho corresponde a linhas completas, contíguas e dedentadas do texto
realmente fornecido. Linhas cortadas, reconstruções e trechos que atravessam o
marcador de omissão continuam removidos deterministicamente. A janela agentiva
passa de oito para dez fontes e reserva, em ordem, evidência por faceta,
resultados-base independentes e toda a fronteira estrutural selecionada. Seções
detalhadas recebem até 2.048 tokens e sua única continuação até 1.536. Essas
regras são orientadas por proveniência, canal de recuperação e limites; não
contêm projeto, branch, caminho, símbolo ou mecanismo científico. A próxima
ação é repetir a mesma suíte na Morgoth dentro do `tmux` e comparar blocos
removidos, `finish_reason`, fontes preservadas e cobertura final.

A validação real da 0.43.3 confirmou que a infraestrutura de respostas longas
permanece estável. Os dois casos terminaram com HTTP 200, `finish_reason=stop`,
100% de cobertura de citações e sem queda da API. A pergunta de malha produziu
2.530 caracteres em 231,6 segundos; a explicação de DPM produziu 9.473
caracteres, uma continuação e terminou em 353,5 segundos. A execução também
revelou que a auditoria ainda agrupava frases independentes do mesmo parágrafo.
Uma frase correta podia, portanto, fazer uma afirmação vizinha excessiva parecer
sustentada. As seções também reutilizavam o texto já reduzido pelo orçamento
global, o que escondia saídas e transições no fim de chunks longos.

A 0.44.0 divide a prosa em afirmações auditáveis por sentença, preservando a
citação final do parágrafo quando ela se aplica às frases anteriores. Cada
seção revalida no PostgreSQL os chunks já autorizados e recebe um orçamento
local de evidência; não há busca nova nem ampliação de ACL nessa leitura. Quando
um chunk ainda precisa ser reduzido, o empacotador preserva sua entrada e sua
saída com uma omissão explícita no meio. O grafo reserva espaço para mais de uma
operação de um mesmo coordenador, evitando que a diversidade de caminhos elimine
métodos de ciclo de vida. Blocos cercados de código só permanecem na resposta se
forem reprodução literal de uma fonte citada e não truncada, inclusive depois de
uma revisão automática. A próxima ação é validar a 0.44.0 na Morgoth e conferir
se integração com o domínio e ciclo de avanço aparecem sem recuperar como fatos
as antigas generalizações sobre colisões, forças ou precisão.

A primeira execução real da 0.43.2 não revelou queda da API: o serviço permaneceu
`active`, com zero reinícios, cerca de 1,3 GiB de RAM e sem eventos de OOM ou
falhas da GPU. O primeiro caso retornou HTTP 200 em 209,6 segundos. O segundo
continuou sendo processado normalmente pelo vLLM quando o cliente de avaliação
atingiu seu limite de 300 segundos, que foi relatado incorretamente como API
indisponível. Durante essa mesma execução, a auditoria repetiu a resposta completa
em cada lote e uma requisição excedeu a janela do gerador local.

A 0.43.3 passa para cada lote de auditoria somente as afirmações daquele lote e
as fontes citadas por elas. Isso elimina a repetição da resposta longa sem reduzir
o texto entregue ou relaxar a validação. A suíte real passa a aceitar até 720
segundos por caso, enquanto os limites normais da API permanecem inalterados. O
avaliador agora distingue timeout de indisponibilidade e grava um checkpoint após
cada caso, de modo que uma falha operacional posterior não descarte respostas e
métricas já obtidas. A próxima ação é repetir a suíte real e examinar o relatório
completo ou parcial produzido automaticamente.

A validação real da 0.43.1 ativou o caminho novo: a pergunta de malha usou duas
seções e cresceu de 1.616 para 3.199 caracteres; a de DPM usou três seções e
cresceu de 3.303 para 6.785 caracteres. Ambas terminaram naturalmente, tiveram
100% de cobertura de citações e todas as 33 afirmações finais foram aprovadas.
O resultado ainda expôs três problemas: uma faceta transversal criou uma seção
redundante, a lacuna de integração com o domínio não recebeu a fonte `Domain`
já autorizada e um chunk truncado foi reproduzido como código incompleto.

A 0.43.2 incorpora facetas transversais às seções que já possuem suas fontes,
eliminando a geração repetida. Lacunas podem receber uma janela candidata quando
os termos da própria faceta coincidem com caminho, título ou papel estrutural de
uma fonte final ainda não atribuída. Lacunas integrativas remanescentes viram
apenas uma orientação local em cada seção existente, sem nova chamada e sem
serem promovidas a fato. Fontes marcadas como textualmente truncadas ficam
proibidas de originar blocos de código. A auditoria de completude também passa a
julgar cada faceta de forma independente, evitando `partial` apenas porque outra
parte da pergunta continua incompleta.

A primeira validação real da 0.43.0 não executou a síntese incremental. O agente
forneceu chunks observados com cobertura conservadora `partial`, mas o caderno
aceitava somente o estado literal `covered`; por isso os dois casos registraram
zero seções e usaram novamente a passagem única. A 0.43.1 aceita evidência
parcial com proveniência para redação limitada, sem promovê-la a cobertura
completa. A auditoria posterior continua sendo a única autoridade sobre a
resposta final. Quando várias facetas reutilizam o mesmo coordenador e ainda há
fontes autorizadas, o caderno abre uma segunda janela de contexto e distribui
as fontes restantes de forma limitada. Isso permite investigar o entorno sem
afirmar previamente que existe uma relação entre os trechos.

A versão 0.43.0 troca a passagem única de geração por uma primeira síntese
incremental para perguntas complexas. O backend cria um caderno de evidências a
partir das facetas da investigação e da proveniência dos chunks que chegaram ao
pacote final. Facetas comprovadas pela mesma evidência local são agrupadas;
facetas independentes recebem seções distintas; lacunas permanecem registradas
sem serem transformadas em fatos. Nós estruturais já observados podem ser
distribuídos entre as seções para sustentar transições. O algoritmo opera sobre
IDs opacos, relações persistidas e proveniência, sem conhecer projeto, branch,
caminho, símbolo ou conceito científico.

Cada uma das até quatro seções recebe no máximo quatro fontes e uma reserva
própria de saída. Uma seção interrompida pelo teto recebe uma única continuação
limitada com as mesmas fontes. O modelo redige somente aquela parte, com citações globais e
sem inventar conexões entre definições vizinhas. As seções são reunidas e então
passam pelas mesmas auditorias de citação, afirmação e cobertura existentes. Na
interface, o progresso mostra a organização do caderno e a elaboração de cada
seção dentro do painel recolhível de investigação. O rótulo editorial “Pontos
sustentados pela investigação” foi removido da resposta; a condição continua
disponível como metadado interno. A próxima ação é validar na Morgoth as duas
perguntas longas da suíte e comparar extensão, etapas cobertas, citações e
latência com a 0.42.9.

A versão 0.42.9 corrige uma perda de evidência observada em explicações longas.
Quando o servidor local recusa a soma entre entrada e reserva máxima de saída, a
API reduz primeiro a reserva de saída até o teto normal de 2.048 tokens e mantém
o pacote de evidências; somente uma nova recusa permite reduzir o contexto. A
redução de contexto preserva as instruções produzidas pela investigação. O
empacotamento também reserva uma amostra limitada dos nós de chamada resolvidos
pelo grafo, além dos trechos associados às facetas da pergunta. A auditoria de
completude cruza cada faceta com a proveniência de suas próprias afirmações, de
modo que uma citação de um estágio não possa declarar outro estágio como
coberto. Essas regras são derivadas da pergunta, do grafo e dos identificadores
de chunks; não contêm nomes de projeto, branch, arquivo, símbolo ou mecanismo.

O piloto de recuperação e respostas dos dois repositórios foi validado. A
interface web foi reorganizada para abrir diretamente em **Perguntar**, com
**Buscar** como segunda função pública. Métricas da máquina e do pipeline foram
retiradas da área principal e concentradas em **Administração**, protegida por
`MFLAB_ADMIN_PASSWORD` e sessão `HttpOnly`. A chave técnica `MFLAB_API_KEY`
permanece separada e protege os endpoints programáticos; ela não é pedida nem
armazenada pela interface. O visual segue o site institucional: fundo claro,
azul, bordas discretas e texto funcional, sem eyebrows ou slogans. A implantação
LAN, a senha administrativa, a busca e o painel privado foram validados na
Morgoth. A correção de contexto foi validada na interface. A versão 0.30.0
inicia a evolução para assistente técnico: expõe todas as branches, adiciona
aliases e branch preferencial por repositório, resolve menções explícitas e
intercala comparações em escopos separados. A decisão aparece em
`scope_resolution`. Confirmar na Morgoth:

A versão 0.31.0 acrescenta a primeira camada de exploração qualitativa para
visões gerais: consultas genéricas limitadas, preferência por documentos de
entrada, balanceamento entre projeto/branch e verificação de citações por
escopo. Uma primeira resposta incompleta recebe no máximo uma revisão. O caso
real está em `evaluations/mfsim-overview-answer-pilot.json`.

A validação inicial da 0.31.0 passou, mas revelou que o catálogo efetivamente
carregado na Morgoth ainda usava fallback canônico e não continha aliases. A
0.31.1 adiciona `configure-routing`, que atualiza somente esses metadados no
TOML local, valida a configuração e grava atomicamente. A próxima validação deve
confirmar a branch preferencial desejada após reiniciar a API. A instrução de
visão geral também deixou de permitir que os escopos indexados sejam descritos
como os únicos ou principais sem evidência explícita.

O primeiro reteste da 0.31.1 confirmou que o TOML foi atualizado corretamente,
mas a API ainda mostrou `canonical_fallback`: o corpus usa um ID estável
derivado da origem, diferente do ID operacional do catálogo. A 0.31.2 associa
essas camadas por ID exato ou por projeto unívoco, nessa ordem, e expõe o método
em `configuration_match`. Projetos duplicados permanecem sem associação
automática.

O reteste da 0.31.2 confirmou a associação, os aliases e a branch `base`, mas o
Qwen produziu citações agrupadas como `[S1, S2]` e repetiu a extrapolação “dois
projetos principais”. A 0.31.3 aceita grupos estritos de IDs e amplia a revisão
única de visões gerais: qualquer falha de grounding, cobertura ou qualificação
dos escopos provoca nova síntese. Se a extrapolação persistir, o retorno usa
`scope_overclaim` em vez de aprovar silenciosamente.

O reteste da 0.31.3 confirmou duas tentativas, citações agrupadas reconhecidas,
cobertura integral dos dois escopos e MFSim-NG em `base`. O status final foi
`partial_citations` porque o parágrafo de limitação não terminou em citação. A
0.31.4 permite que a suíte aceite `cited` ou `partial_citations` somente com
cobertura textual mínima de 80%, cobertura de escopos em 100%, zero IDs
inválidos e nenhuma extrapolação proibida. A resposta permaneceu superficial e
motivou o índice qualitativo hierárquico descrito em
`docs/assistant-roadmap.md`.

A 0.32.0 inicia esse índice qualitativo com um mapa estrutural genérico por
projeto, branch e ACL. Ele é derivado sob demanda dos metadados já carregados no
PostgreSQL, possui fingerprint determinístico e enumera formatos, entradas de
primeiro nível, commits e âncoras documentais. Perguntas amplas recebem uma
fonte `derived_structure`, limitada explicitamente a layout e formatos, seguida
das fontes primárias. `/structure` torna o mapa auditável sem nova carga ou uso
do LLM. Ainda faltam símbolos, relações e resumos hierárquicos incrementais.

A validação real da 0.32.0 passou nos dois corpora: dois mapas, fontes derivadas
e primárias em ambos os escopos e ausência do mapa em consulta direta. A
0.33.0 melhora somente apresentação e contrato de saída. O gerador passa a
produzir Markdown e blocos de código com linguagem; a interface usa um
renderizador seguro sem `innerHTML` e apresenta citações como referências
clicáveis com arquivo e linhas. Os IDs `[Sx]` continuam intactos na API para
grounding e avaliações.

A 0.33.1 aplica um realce léxico seguro com paleta inspirada no VS Code aos
blocos cercados da resposta, aos resultados de busca e aos cartões de fonte. A
linguagem é derivada do campo genérico `format`, do caminho ou da cerca
Markdown. O realce cria somente nós de texto e `span`, não usa CDN, `innerHTML`
ou nomes de projetos.

A 0.34.0 separa presença de citação de sustentação semântica. Cada unidade
factual da resposta é auditada pelo mesmo modelo local contra somente as fontes
que ela cita. O retorno estruturado é validado pelo servidor; IDs inventados e
afirmações omitidas não são aprovados. Uma falha permite uma única síntese
revisada e, se persistir, a resposta candidata não é entregue. A interface usa
uma fila efêmera limitada a um worker e acompanha eventos reais da investigação
por polling: escopo, recuperação, evidências, geração, auditoria e revisão. Essa
trilha não expõe prompts, raciocínio interno ou texto integral das fontes.
O teste real correspondente está concentrado em
`scripts/validate-investigation.py`; RunBlocks devem chamá-lo em vez de repetir
Python extenso dentro de heredocs aninhados.

A 0.34.1 tornou a auditoria tolerante a um objeto JSON válido envolvido por
texto auxiliar do provedor e adicionou repetição estruturada limitada,
configurável por `provider.verification_max_attempts`. A repetição não relaxa a
validação nem cria nova resposta; se nenhum resultado válido for obtido, a
resposta permanece bloqueada.

A validação real da 0.34.1 confirmou a trilha completa e o bloqueio seguro de
uma resposta cuja premissa não permaneceu sustentada após a revisão.

A 0.35.0 inicia o mapa qualitativo com `symbols.jsonl`, `relations.jsonl` e um
resumo versionado. A extração usa somente construções genéricas das linguagens,
preserva ACL e proveniência e não altera chunks ou embeddings. A próxima
subetapa foi validada na Morgoth com 8.235 símbolos e 8.355 relações no corpus
de 17 branches do MFSim-NG; referências ambíguas permaneceram não resolvidas.

A 0.36.0 projeta o mapa no PostgreSQL em uma transação separada, idempotente e
restrita ao `repository_id`. Símbolos apontam para chunks, relações preservam
ocorrências próprias e o fingerprint cobre também ACL e proveniência. O mapa
ainda não altera busca ou respostas. A validação na Morgoth carregou 8.235
símbolos e 8.355 relações, resolveu 1.408 destinos e confirmou reutilização
integral pelo mesmo fingerprint em uma segunda execução.

A 0.37.0 acrescenta `db-map-search`, uma consulta somente leitura de símbolos e
relações com filtros de ACL, projeto, branch, prefixo de caminho e tipo aplicados
no SQL. Ela retorna proveniência e o chunk de evidência, mas não retorna texto e
não foi conectada ao `/ask`. A próxima ação é validar consultas estruturais no
corpus real, incluindo uma branch de trabalho, antes de projetar ferramentas do
planejador.

A 0.38.0 conecta o mapa estrutural ao `/ask` para perguntas de localização e
mecanismo. O modelo local atua somente como planejador de vocabulário, com
saída JSON limitada; projeto e branch continuam sendo resolvidos pela pergunta
original e pelo catálogo. As consultas híbridas alimentam uma navegação por
símbolos e relações, e cada resultado volta ao chunk primário com ACL e escopo
reaplicados no PostgreSQL. A interface mostra consultas, termos, nós e trechos
selecionados sem expor raciocínio oculto. A arquitetura e suas fontes estão em
`docs/code-investigation.md`; a regressão específica fica em
`evaluations/mfsim-ng-investigation-pilot.json`. A próxima camada é enriquecer o
mapa com chamadas e usos de símbolos extraídos por parsing sintático genérico.

A validação real da 0.38.0 mostrou o limite do plano em uma única etapa: oito
identificadores plausíveis propostos pelo modelo não existiam no mapa, a
navegação retornou zero nós e a auditoria bloqueou 9 de 11 afirmações como
incertas ou não sustentadas. A 0.39.0 substitui esse beco sem saída por até três
ciclos de observação e ferramentas somente leitura. O modelo pode escolher
nova busca, consulta de símbolo ou vizinhança de um chunk observado; cada
resultado volta ao ciclo seguinte. Ações, escopo, ACL, repetições e orçamentos
continuam controlados pelo servidor. A síntese recebe um caderno de cobertura,
e uma falha da auditoria de reparo não apaga mais o primeiro laudo válido.
Hipóteses com zero resultados voltam ao ciclo como observações, e respostas
longas são auditadas em lotes limitados para evitar truncamento do JSON sem
relaxar a conferência de nenhuma afirmação.
A interface recebe as etapas durante a execução em tempo real. O painel de
investigação abre no início, pode ser recolhido pelo usuário sem interromper o
job e não volta a abrir sozinho durante as atualizações seguintes.
A primeira execução real da 0.39.0 encontrou quatro defeitos distintos: uma
decisão sem ação ficou parada após confundir menções a `Mesh` com a operação
qualificada, uma pergunta “como funciona” caiu no modo direto, o auditor não
conseguiu produzir o JSON esperado em lotes de cinco afirmações e o resumo do
RunBlock continha escape Python inválido. A 0.39.1 generaliza a classificação
de mecanismo, reconsulta decisões inconclusivas, preserva toda evidência do
caderno de cobertura e reduz a auditoria para lotes de três com contrato mais
explícito. Os termos científicos continuam restritos ao handoff e às avaliações.
A regressão agora inclui tanto a malha adaptativa quanto a pergunta ampla sobre
o fluxo atual de um subsistema; os nomes científicos permanecem apenas na
suíte de avaliação.

A validação real da 0.39.1 confirmou que o runtime já consegue investigar um
fluxo amplo: no caso exercitado, escolheu quatro ferramentas de leitura e a
primeira auditoria aprovou sete de nove afirmações. A resposta ainda foi
bloqueada porque duas frases amplas sem citação sobreviveram à revisão. No caso
de localização, três decisões sem ferramenta esgotaram o orçamento mesmo com
um candidato relevante entre os resultados.

A 0.40.0 trata esses dois pontos sem acrescentar conhecimento científico ao
motor. Depois de uma primeira oportunidade de replanejamento, decisões vazias
repetidas — e decisões JSON inválidas — acionam uma contingência determinística
que ranqueia somente a pergunta, as hipóteses de busca e metadados de chunks já
observados. Ela pode abrir a vizinhança do chunk ou pesquisar o título/caminho
real encontrado, sempre dentro do escopo e ACL já resolvidos. A revisão de
resposta passa a receber o rascunho anterior e os achados exatos da auditoria,
preserva o conteúdo aprovado e remove especificamente alegações rejeitadas. A
segunda auditoria continua obrigatória e nenhuma resposta parcialmente
sustentada é liberada.

A validação real da 0.40.0 confirmou a contingência: ela selecionou
genericamente a definição correta, executou vizinhança, símbolo e busca textual,
e elevou a cobertura média de 51% para 85%. Ainda faltaram os pontos de
integração e operações relacionadas esperados pelas duas avaliações. A revisão
textual preservou a maior parte do conteúdo válido, mas acrescentou novamente
uma a três afirmações amplas, que foram corretamente bloqueadas.

A 0.40.1 amplia o orçamento limitado de três para quatro ciclos para que os
resultados da última busca possam orientar uma nova leitura. A contingência é
executada também no último ciclo, transforma identificadores observados em
termos pesquisáveis e avança para o próximo candidato quando as ações do
primeiro já foram usadas. O contrato de cobertura de perguntas amplas passa a
procurar papéis genéricos de integração, coordenação e efeitos a jusante. Depois
da correção pelo modelo, uma consolidação determinística pode remover somente
as unidades rejeitadas e preservar as aprovadas; o resultado é obrigatoriamente
auditado outra vez antes de ser entregue.

A validação real da 0.40.1 manteve a infraestrutura saudável e elevou a
cobertura média da suíte para 97,9%, mas os dois casos ainda reprovaram. Na
consulta de localização, uma expansão sobre um método genérico ligado à
fronteira imersa tomou a janela de observações e desviou a investigação dos
arquivos centrais da malha. Na consulta de fluxo, 22 afirmações foram aprovadas
e apenas duas frases amplas foram rejeitadas, mas a consolidação não executou
porque estava acoplada à opção local de reescrita pelo modelo.

A 0.40.2 corrige as causas sem codificar nenhum projeto ou subsistema. Grupos de
resultados passam a ser intercalados na janela limitada, impedindo que uma única
busca expulse todas as hipóteses anteriores. O servidor preserva uma hipótese
estrutural independente por ciclo e oferece `open_related`, que percorre
relações já indexadas entre arquivos companheiros, dependências e dependentes;
projeto, branch e ACL são reaplicados no SQL antes de buscar os chunks. A
consolidação de afirmações aprovadas torna-se independente da reescrita pelo
modelo e continua exigindo uma auditoria final. A próxima execução real deve
confirmar se a navegação recupera os pontos de integração esperados e se o caso
de fluxo deixa de abster sem relaxar a sustentação.

A validação real da 0.40.2 confirmou a salvaguarda central: os dois casos
produziram respostas citadas e a auditoria final marcou todas as afirmações
liberadas como sustentadas. A navegação recuperou arquivos centrais, cabeçalhos,
coordenação e saída, mas a suíte estrita permaneceu em 0/2 porque faltaram
pontos de integração esperados em ambos os fluxos. O achado separou claramente
qualidade de síntese de cobertura de navegação: `open_related` funciona, porém
relações somente entre arquivos não revelam o fluxo de chamadas entre símbolos.

A 0.41.0 implementa a primeira resposta genérica a esse gargalo. O mapa v2
extrai chamadas dentro das unidades estruturais reconhecidas em C, C++, headers,
Fortran e Python. O destino é resolvido somente no mesmo repositório e em
ocorrências compartilhadas de branch/commit; qualificação explícita, nome único,
unicidade na branch e indício de receptor permanecem categorias distintas.
Ambiguidades recebem `unresolved_symbol` e não viram conexões factuais. O agente
ganha `find_callers` e `find_callees`, limitados a chunks já observados, com
projeto, branch e ACL reaplicados no PostgreSQL. Não existem nomes de projetos,
branches, arquivos ou mecanismos científicos nessa implementação. A próxima
execução na Morgoth deve reconstruir somente o mapa do MFSim-NG, inspecionar as
contagens de resolução e repetir a suíte de investigação antes de promover a
mesma migração aos demais corpora.

A validação real da 0.41.0 construiu 8.235 símbolos e 54.351 relações no
MFSim-NG, das quais 45.996 são chamadas. Artefato e PostgreSQL tiveram o mesmo
fingerprint e a travessia chamada→destino→chamador passou. A avaliação de
respostas continuou em 0/2, embora com cobertura de citações de 100%. No fluxo
de partículas, `find_callees` devolveu 12 chunks e a resposta passou a incluir a
integração em `Domain`, mas a fronteira foi descartada antes de chegar à
operação a jusante esperada. Na malha, a resposta final citou apenas
inicializações adjacentes e terminou por limite de geração, sem localizar o
ponto de preenchimento esperado. Todas as afirmações entregues foram auditadas
como sustentadas; portanto, o defeito é de relevância e cobertura da navegação,
não de fabricação de fatos.

A 0.41.1 prioriza genericamente a fronteira nova do grafo. Chamados e chamadores
recém-observados recebem uma oportunidade limitada de expansão antes de o agente
voltar ao coordenador. Um chamador é aberto por suas chamadas para revelar
operações irmãs; um chamado pode continuar a jusante. Duas evidências por ação
são preservadas em uma fila de no máximo oito chunks e intercaladas com as
escolhas do modelo no contexto final. O SQL ordena a travessia pela linha de
chamada e a instrução de síntese passa a rejeitar detalhes adjacentes que não
respondam à operação pedida. Essa versão não exige reconstruir o mapa nem os
embeddings: somente atualizar o pacote, reiniciar a API e repetir a avaliação.

A validação real da 0.41.1 confirmou que a fronteira deixou de ser descartada:
seis chunks estruturais foram preservados no caso de localização e três no caso
de fluxo. As duas respostas terminaram normalmente, tiveram cobertura de
citações de 100% e liberaram somente afirmações sustentadas. A suíte permaneceu
em 0/2 porque a exploração aprofundou conexões laterais pouco relevantes: a
consulta de malha seguiu a inicialização de fronteira imersa, enquanto o fluxo
de partículas priorizou monitoramento e rastreamento antes das operações sobre
partículas. O log também revelou que a hipótese determinística suplementar era
anunciada, mas podia ser truncada antes da deduplicação das ações do modelo.

A 0.41.2 corrige esses dois mecanismos genericamente. A fronteira de chamadas é
amostrada e ordenada pelo vocabulário da pergunta, dando mais peso a caminho e
símbolo que a menções incidentais no corpo; quando não há sobreposição, são
preservados pontos espaçados do fluxo em vez de somente as primeiras chamadas.
Uma conexão estrutural recebe bônus limitado, incapaz de vencer por si só uma
evidência muito mais relevante. Uma das três ações por ciclo é efetivamente
reservada para a hipótese independente antes da deduplicação. Por fim, a
auditoria de afirmações passa a rejeitar uma alegação verdadeira sobre código
adjacente quando ela é apresentada como se respondesse à operação solicitada.
Mapa, corpus e embeddings existentes continuam reutilizáveis.

A validação real da 0.41.2 confirmou as duas salvaguardas: hipóteses
independentes foram efetivamente executadas nos ciclos seguintes e a resposta
que confundia inicialização de fronteira imersa com malha adaptativa foi
bloqueada pela auditoria de relevância. A suíte continuou em 0/2 porque a busca
ainda permaneceu na hipótese errada e o fluxo de partículas não trouxe a
implementação a jusante esperada. O contexto mostrou outro gargalo: oito
fronteiras eram descobertas, mas as primeiras acumuladas ocupavam a cota e a
janela final comportava somente três fontes no caso de fluxo.

A 0.41.3 permite que o planejador use conhecimento científico e de engenharia
de software exclusivamente para formular hipóteses de busca: terminologia
convencional, siglas, formas expandidas, sinônimos de implementação, papéis de
ciclo de vida e estruturas de dados. Nada disso pode provar um fato do
repositório; cobertura e resposta continuam exigindo código observado. O plano
passa a aceitar até cinco hipóteses distintas além da pergunta original e doze
identificadores. As fronteiras de todos os ciclos são acumuladas até um limite
seguro, reranqueadas globalmente pela pergunta e intercaladas na proporção de
duas conexões estruturais para cada evidência mantida pelo modelo. Não há
reindexação nem cálculo de embeddings nessa mudança.

A validação real da 0.41.3 mostrou que a formulação conceitual de hipóteses
funcionou: no caso da malha foram encontrados o ponto de configuração do
domínio e as operações do gerenciador, e no caso do subsistema foram encontrados
o avanço do domínio, a gerência e o estado da entidade. A suíte continuou em
0/2 porque a composição final privilegiou vários métodos do mesmo arquivo e um
arquivo de construção mantido incidentalmente. Assim, conexões já descobertas e
associadas explicitamente à cobertura ficaram fora da janela entregue ao
gerador. O primeiro caso também esgotou os quatro ciclos antes de seguir a
operação intermediária até sua implementação a jusante.

A 0.41.4 corrige essa perda sem conhecimento específico do domínio. A fronteira
estrutural preserva primeiro caminhos distintos e somente depois completa a
cota com métodos irmãos; evidências apontadas pelo próprio relatório de
cobertura precedem seleções incidentais; seis posições da janela de observação
ficam reservadas para o resultado de ferramentas mais recente, sem eliminar as
hipóteses anteriores; e existe um quinto ciclo limitado quando a cobertura
ainda não é suficiente. Projeto, branch, commit e ACL continuam sendo aplicados
antes de qualquer leitura. A mudança reutiliza mapa semântico, corpus e
embeddings existentes.

A validação real da 0.41.4 confirmou a diversidade da fronteira, mas revelou
três gargalos de composição. Resultados recentes do grafo podiam ficar atrás de
vizinhanças na janela observável; o empacotamento do contexto podia consumir o
orçamento com os primeiros arquivos e excluir uma integração já descoberta; e
uma cobertura completa idêntica podia provocar ciclos desnecessários. Também
foi observado que uma revisão textual malsucedida podia substituir afirmações
úteis já aprovadas por uma recapitulação redundante.

A 0.41.5 corrige esses pontos de forma genérica. Arestas recentes do grafo são
observadas antes de resultados lexicais, fontes distintas compartilham o
orçamento de evidências em vez de serem descartadas pela primeira fonte longa,
e duas coberturas completas consecutivas encerram a exploração. Falhas do canal
de progresso não interferem no resultado. Uma revisão rejeitada preserva
preferencialmente as unidades aprovadas da resposta original e recebe instrução
explícita para não criar segunda síntese ou rótulos factuais isolados.

Orçamento de evidências, teto de saída e janela do provedor passam a ser
tratados como grandezas diferentes. O teto local padrão de resposta sobe para
2.048 tokens e pode ser alterado atomicamente por configuração; ele não obriga
respostas longas. Perguntas diretas podem terminar cedo, enquanto perguntas de
mecanismo, fluxo ou comparação são orientadas a explicar todas as etapas
sustentadas, mesmo quando distribuídas por vários arquivos. O teto efetivamente
aplicado é devolvido pela API. O limite de 8.192 tokens do runtime atual ainda é
compartilhado por instruções, evidências e resposta.

Explicações maiores que uma única janela exigirão síntese hierárquica, não um
número fixo cada vez maior. A evolução prevista divide a pergunta em aspectos,
investiga e audita cada aspecto, produz resumos intermediários citados e então
compõe a resposta final mantendo os vínculos com as fontes primárias. Os
orçamentos dessa camada deverão derivar da configuração do provedor e da
complexidade observada da pergunta, sem nomes de repositório, branch, arquivo ou
domínio codificados no motor.

A execução real da 0.41.5 manteve a infraestrutura saudável e reduziu o tempo
dos dois casos, mas a suíte continuou em 0/2. A explicação do subsistema foi
entregue com dez afirmações sustentadas e quatro citações; falhou somente por
não incluir o ponto de integração a montante exigido pelo gabarito. O agente
havia encerrado em dois ciclos sem executar qualquer ferramenta do grafo. No
caso de localização, a fronteira já continha a operação intermediária correta,
mas o quinto ciclo seguiu outra hipótese e não atravessou essa operação. A
síntese produziu nove unidades e citou apenas uma, sendo corretamente bloqueada
pela auditoria. A suíte ainda solicitava tetos locais de 1.000 e 1.400 tokens,
portanto não exercitava o novo teto configurado de 2.048.

A 0.41.6 impede que uma pergunta de mecanismo encerre apenas com cobertura
local: ao menos uma travessia de chamada precisa produzir evidência. Quando
isso ainda não ocorreu, os próprios chunks que o agente declarou cobertos são
sondados em ambas as direções. Fronteiras observadas no último ciclo recebem
uma continuação final, somente leitura e limitada, antes da síntese. A janela
final passa a conter no máximo seis fontes ordenadas, deixando espaço para uma
resposta mais longa dentro da janela total do provedor. A suíte deixa de
sobrescrever o teto local e passa a testar os 2.048 tokens configurados.

Afirmações sem citação passam por uma etapa distinta de descoberta de suporte.
Ela não reescreve a resposta e não aceita conhecimento externo: apenas propõe
IDs de fontes para unidades exatas que sejam integralmente sustentadas. O
backend valida os IDs, anexa somente associações aprovadas e então executa a
auditoria semântica normal novamente. Unidades sem suporte permanecem sem
citação e continuam sujeitas a remoção ou abstinência. Assim, corrigir uma falha
de formatação não reduz a exigência de sustentação factual.

A validação real da 0.41.6 mostrou avanço substancial, apesar de a suíte estrita
permanecer em 0/2. A localização da malha foi respondida, terminou normalmente e
teve duas afirmações integralmente sustentadas; faltou somente a implementação
concreta exigida pelo gabarito. O fluxo do subsistema recuperou construção,
avanço e estado, mas a sondagem estrutural se concentrou apenas no construtor e
não trouxe o chamador do método de avanço. A resposta acabou bloqueada por uma
única unidade: um rótulo isolado de símbolo foi interpretado pelo auditor como
se afirmasse que o método era chamado. A cobertura média de citações foi 100% e
nenhuma afirmação não sustentada foi liberada.

A 0.41.7 separa esses defeitos sem inserir conhecimento científico no motor.
Quando cobertura local ainda precisa ser conectada ao fluxo, o servidor sonda
um chunk observável distinto de até três aspectos declarados no caderno, em vez
de usar as duas direções somente no primeiro símbolo. A direção inicial procura
chamadores, favorecendo pontos de integração a montante; outras travessias
continuam disponíveis nos ciclos seguintes. O planejador e o investigador
também passam a tratar construção ou seleção por fábrica, implementação
concreta, configuração posterior e uso em runtime como hipóteses separadas em
perguntas de inicialização. Essas hipóteses apenas guiam buscas: nenhum estágio
é aceito sem fonte primária observada. Por fim, um item de lista formado somente
por um símbolo ou caminho em código inline e sua citação é tratado como rótulo
de apresentação, não como alegação de que houve chamada ou uso. Qualquer texto
factual ao redor dele continua passando pela auditoria semântica normal.

A validação real da 0.41.7 confirmou a correção multiaspect no caso de fluxo. O
agente sondou separadamente construção, avanço e configuração, recuperou os
pontos de integração em `Domain`, entregou a resposta e terminou com 12/12
afirmações sustentadas. A suíte ainda reprovou esse caso porque o contexto final
repetiu chunks de gerência e domínio e excluiu um arquivo de estado que já havia
sido recuperado. No caso de localização, a classificação permaneceu como
`location`; por isso, a exigência de travessia usada em `mechanism` não foi
aplicada. O agente encerrou em dois ciclos e zero ferramentas, aceitando como
cobertura uma inicialização adjacente que não explicava a construção concreta.

A 0.41.8 reserva uma das seis consultas para uma hipótese determinística de
construção, fábrica, criação e implementação concreta quando a pergunta de
localização contém linguagem de inicialização ou construção. No máximo quatro
hipóteses do modelo ocupam as demais posições; a hipótese determinística é uma
busca, nunca uma afirmação factual. Localizações com mais de um aspecto coberto
também exigem ao menos uma conexão estrutural antes de encerrar. Na composição,
até quatro candidatos relevantes da recuperação original permanecem ao lado das
escolhas do agente e da fronteira do grafo. O empacotamento reserva até quatro
caminhos distintos antes de repetir chunks do mesmo arquivo, mantendo duas
posições disponíveis para métodos complementares de um coordenador. Assim,
implementações e objetos de estado já encontrados não são expulsos apenas
porque um coordenador possui várias unidades relevantes.

A execução real da 0.41.8 manteve cobertura textual de citações em 100% e
entregou respostas auditadas nos dois casos, mas a suíte científica estrita
permaneceu em 0/2. Na localização da malha, a recuperação trouxe fábrica e
gerenciador, porém a síntese escolheu uma inicialização adjacente de fronteira
imersa e deixou de fora integração no domínio e preenchimento concreto. No fluxo
do subsistema, construção, avanço, estado e integração no domínio apareceram e
sete afirmações foram aprovadas, mas a resposta utilizou duas citações distintas
quando o gabarito exigia três. O resultado reforça que uma execução mede
capacidade, não estabilidade: cobertura de navegação, qualidade da resposta e
critérios científicos precisam ser reportados separadamente e avaliados em
múltiplas tentativas.

A 0.42.0 acrescenta profundidade de resposta como preferência explícita e
genérica. A interface oferece `Automática`, `Direta` e `Detalhada`; o backend
continua sendo o único responsável pelos tetos de contexto e saída. No modo
detalhado, a síntese deve explicar as etapas sustentadas e, em perguntas sobre
código, pode distribuir pequenos excertos literais das evidências ao longo da
explicação. O modelo não pode reconstruir código ausente, alterar identificadores
ou apresentar linhas não contíguas como um único trecho. Markdown e citações
continuam passando pelo mesmo renderizador seguro e pela auditoria semântica.

A primeira execução detalhada da 0.42.0 sobre o fluxo de um subsistema expôs
uma resposta apenas aparentemente truncada. O provedor terminou normalmente,
mas a auditoria aprovou duas de doze unidades; a recuperação determinística
removeu as demais e apresentou os dois fragmentos restantes como se formassem
uma explicação completa. Ao mesmo tempo, todos os ciclos da exploração
registraram zero aspectos cobertos, parciais ou ausentes, mostrando que o
caderno de cobertura não havia sobrevivido ao planejamento.

A 0.42.1 corrige os dois problemas de forma genérica. O planejador local passa a
decompor a solicitação em até seis aspectos organizacionais, sem afirmar que
qualquer mecanismo exista no repositório. Esses aspectos entram como lacunas no
primeiro ciclo, permanecem no caderno mesmo quando uma decisão do modelo os
omite e só mudam de estado mediante chunks observados. O encerramento exige que
todo o contrato de cobertura esteja sustentado. Se a auditoria ainda precisar
preservar apenas um subconjunto de afirmações no modo detalhado, a API e a
interface identificam o resultado como pontos sustentados com limitações, em
vez de anunciar uma resposta completa. Isso não relaxa a auditoria nem cria
prosa científica nova durante a recuperação de segurança.

A validação real da 0.42.1 confirmou que o contrato passou a sobreviver aos
cinco ciclos: no caso detalhado foram registrados quatro aspectos parciais e
duas lacunas. Também revelou três problemas subsequentes. Evidências já
encontradas para avanço e integração perderam espaço para preâmbulos e funções
secundárias na janela final; o último lote de ferramentas não era observado por
uma nova decisão; e uma afirmação aprovada em uma auditoria podia ser rejeitada
na conferência seguinte, enquanto a poda determinística executava apenas uma
rodada. O caso de localização ainda chamou de completa uma resposta reduzida de
oito para duas afirmações porque a marcação de subconjunto estava restrita ao
modo detalhado.

A 0.42.2 reserva primeiro uma evidência por aspecto do caderno, reconcilia a
cobertura uma vez após a última leitura sem executar ferramentas adicionais e
só então preenche a janela com recuperação-base e fronteiras do grafo. A poda
de afirmações passa a ser monotônica: cada nova reprovação remove unidades e é
auditada novamente até aprovação, ausência de progresso ou ausência de texto.
`answer_completeness` distingue resposta completa, cobertura limitada,
subconjunto sustentado e resposta não entregue em qualquer profundidade. O
planejador também deixa de transformar testes, documentação, saída ou limpeza
em aspectos obrigatórios quando a pergunta não os solicita. Nenhuma dessas
regras contém nomes de projeto, branch, arquivo, símbolo ou mecanismo.

A validação real da 0.42.2 mostrou avanço material: a explicação detalhada
preservou configuração, inicialização, avanço e integração com quatro
afirmações sustentadas, mas foi reprovada porque o planejador havia promovido
`state evolution` e `boundary handling` a obrigações não pedidas e classificou
evidências diretas como parciais. A 0.42.3 separa hipótese de busca de contrato
de resposta. Cada aspecto obrigatório precisa apontar para um trecho literal da
pergunta; aspectos inventados ou apenas adjacentes são descartados pelo
servidor. Depois da auditoria factual, uma segunda auditoria de cobertura usa
somente afirmações já sustentadas para decidir se cada aspecto pedido foi
atendido. Uma poda de sobreafirmações pode, portanto, terminar como resposta
completa quando o conteúdo restante ainda satisfaz toda a pergunta. A resposta
e o resumo de laboratório expõem essa cobertura final. A implementação é
genérica e não contém nomes de projeto, branch, arquivos ou mecanismos.

O teste real da 0.42.3 revelou duas perdas posteriores à recuperação. O modelo
local podia traduzir os rótulos de aspectos e, apesar de reconhecer afirmações
sustentadas, fazer a validação estrita convertê-los todos em lacunas. Além
disso, a redução determinística mantinha a prosa aprovada, mas removia blocos de
código por eles não serem afirmações. Na 0.42.4, a auditoria usa IDs estáveis de
aspecto e o servidor restaura sempre o rótulo original validado. A redução
preserva somente blocos que sejam trechos exatos de uma fonte autorizada citada
por uma afirmação aprovada; código modificado, inventado ou sem esse vínculo é
descartado. A correção não contém nomes de projeto, branch, caminho, símbolo ou
conceito científico.

O teste real da 0.42.4 confirmou a preservação de blocos C++ exatos e citados,
mas o modelo ainda classificou todos os aspectos como lacuna em uma única
decisão, além de não associar ao caderno evidências que já apareciam nas
observações. Na 0.42.5, os IDs estáveis passam a valer também durante todos os
ciclos do agente e na reconciliação final. A cobertura da resposta é auditada
uma faceta por vez, para que reformulação ou falha de um item não invalide os
demais, e o progresso dessas conferências é emitido separadamente. A janela
final reserva cinco caminhos distintos e uma posição repetida, preservando
diversidade sem eliminar métodos complementares. Todas as regras continuam
independentes de repositório, branch, arquivo, símbolo e domínio científico.

O teste real da 0.42.5 confirmou ganho na navegação: os cinco aspectos da
consulta de malha receberam evidências parciais, a implementação concreta
chegou à janela e a resposta trouxe código exato. A auditoria final, porém,
continuou zerando todos os aspectos porque o modelo não devolveu a identidade
no protocolo esperado. Também foi observada oscilação ao reavaliar exatamente
a mesma afirmação depois da poda.

Na 0.42.6, chamadas de cobertura com uma única faceta são reconciliadas pela
posição controlada pelo servidor. Veredictos factuais idênticos são armazenados
somente durante a pergunta e reutilizados quando texto e fontes citadas não
mudaram; qualquer modificação exige nova auditoria. Chamadas estruturadas usam
semente fixa no provedor local. Uma amostra ranqueada das observações iniciais
também permanece candidata à composição, evitando que descobertas de consultas
distintas sejam deslocadas por vizinhos tardios. Nada disso contém nomes de
projeto, branch, arquivo, símbolo ou conceito científico.

O teste real dessa versão chegou a 100% de cobertura de citações e mostrou
reutilização dos veredictos, mas as duas respostas permaneceram parciais. A
exploração já havia encontrado os trechos esperados; eles foram perdidos ou
receberam pouco espaço na passagem para o pacote final. Na 0.42.7, uma evidência
por faceta é reservada antes da diversidade por caminho, a recuperação base é
intercalada com a fronteira do grafo e o orçamento de caracteres é dividido de
forma equilibrada. O caderno exploratório é tratado como provisório: uma lacuna
anterior não bloqueia uma fonte final diretamente probatória. A reconciliação
por proveniência pode elevar `gap` somente a `partial`, mantendo a declaração de
cobertura completa exclusivamente na auditoria semântica. Âncoras distintas
ligadas ao mesmo trecho literal da pergunta são deduplicadas. Toda essa lógica é
genérica e usa apenas estrutura, facetas e proveniência da consulta atual.

A validação da 0.42.7 aumentou a explicação de fluxo de três para dezoito
afirmações factualmente aprovadas, com quatro fontes citadas e blocos exatos para
configuração e avanço. A consulta curta de localização, porém, ainda sintetizou
somente a primeira das fontes disponíveis; em ambos os casos, uma evidência
estrutural útil ficou logo depois do limite de seis fontes. Na 0.42.8,
investigações agentivas podem levar até oito fontes ao mesmo orçamento total de
caracteres, preservando a divisão equilibrada. O papel original de uma fronteira
de chamadas é copiado ao registrá-la, impedindo que uma leitura posterior do
mesmo resultado o desclassifique antes da continuação terminal. Essa continuação
pode percorrer duas rodadas limitadas de arestas persistidas, permitindo ligar
um chamador ao coordenador e então às operações downstream sem busca livre. A
instrução de síntese exige percorrer todas as facetas que possuem fontes, sem
aceitar a primeira etapa como resposta completa. Os limites permanecem
genéricos e independentes de qualquer corpus.

Como a mesma execução mostrou que uma definição local podia ser citada para uma
frase global como “o fluxo começa aqui”, a geração e a auditoria agora exigem
evidência da própria relação para afirmações de ordem, chamada ou causalidade.
Encontrar ou citar separadamente os dois extremos não comprova a conexão.

Foi registrado como requisito de interface visualizar o grafo usado na
investigação. Depois da resposta e das citações, a interface deverá poder exibir
um painel recolhível com o subgrafo efetivamente percorrido naquela consulta:
arquivos, símbolos e relações como chamadas, dependências, companheiros e
chamadores. A visualização não deve despejar o grafo inteiro do repositório nem
expor raciocínio interno do modelo. Cada nó e aresta deve vir de dados
estruturais persistidos, respeitar projeto, branch, commit e ACL, e permitir
abrir a fonte correspondente. Relações propostas pelo modelo não podem aparecer
como fatos; conexões não resolvidas devem ser identificadas como tais. O objetivo
é ajudar o usuário a compreender o fluxo técnico e também tornar auditável o
caminho de evidências utilizado na resposta.

O modo padrão permanece em `127.0.0.1`. A exposição à rede local é opt-in e a
porta deve ser limitada à sub-rede confiável. Busca e pergunta da interface usam
rotas web somente leitura; administração usa senha separada. Um segredo
compartilhado atende apenas à demonstração inicial; usuários individuais, HTTPS,
grupos e auditoria serão necessários antes do uso institucional.

Depois da interface demonstrável, a ordem recomendada é:

1. autenticação individual e política de acesso multiusuário;
2. ampliar avaliações multi-repositório e multi-branch;
3. substituir âncoras heurísticas por parsing estrutural genérico para C, C++,
   Fortran, CMake e casos configurados, incluindo chamadas e usos de símbolos;
4. visualizar na interface o subgrafo comprovado e percorrido em cada resposta;
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
- A Morgoth não possui `rg` garantido. Auditorias em RunBlocks devem usar Python
  ou verificar explicitamente a disponibilidade da ferramenta; comando ausente
  nunca pode ser interpretado como auditoria aprovada.

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
