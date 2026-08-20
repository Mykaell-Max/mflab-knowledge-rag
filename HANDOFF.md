# Handoff operacional — MFLab Knowledge RAG

> Fonte de continuidade para novas conversas e colaboradores.
>
> Estado atualizado em **19 de agosto de 2026**, na versão candidata **0.37.0**. Antes de
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
