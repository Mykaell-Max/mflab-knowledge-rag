# Investigação de código com recuperação hierárquica

Perguntas sobre responsabilidade e fluxo não podem ser tratadas como uma
busca por similaridade seguida de geração. Um trecho pode conter as palavras
da pergunta e ainda não implementar a operação procurada. A investigação
adota, portanto, uma localização hierárquica e limitada:

1. o escopo de projeto e branch é resolvido apenas pela configuração e pela
   pergunta original;
2. o modelo local propõe somente consultas e identificadores possíveis, sem
   produzir fatos, caminhos tidos como verdadeiros ou respostas;
3. a busca híbrida encontra candidatos dentro do escopo e da ACL;
4. nomes encontrados ou propostos são consultados no mapa de símbolos e
   relações;
5. cada nó estrutural é convertido novamente no chunk primário correspondente,
   com projeto, branch, commit, caminho, linhas e ACL validados no SQL;
6. somente os chunks primários seguem para a síntese e para a auditoria de
   afirmações.

O planejador não pode alterar escopo, executar comandos, consultar a rede ou
entregar conteúdo ao gerador. São aceitas no máximo quatro consultas e oito
identificadores, com até quatro escopos e 24 nós estruturais por pergunta. Se o
planejamento ou o mapa falhar, a recuperação híbrida permanece disponível; a
verificação semântica continua podendo bloquear a resposta.

A interface mostra a trilha observável — consultas realizadas, escopos, nós
navegados, evidências selecionadas e resultado da auditoria. Essa trilha não é
o raciocínio interno do modelo e não expõe prompts ocultos.

## Fundamentação

- O Agentless separa localização por arquivo, classe/função e posição fina:
  <https://github.com/OpenAutoCoder/Agentless>.
- O mapa de repositório do Aider combina definições, referências e ranking em
  um orçamento limitado: <https://aider.chat/2023/10/22/repomap.html>.
- O RepoCoder mostra a vantagem e também o limite de poucas iterações de
  recuperação guiadas pelo contexto anterior: <https://arxiv.org/abs/2303.12570>.
- O IRCoT demonstra que recuperação única é insuficiente em perguntas
  multi-hop: <https://arxiv.org/abs/2212.10509>.
- O GraphRAG local combina estruturas do grafo com chunks textuais primários:
  <https://microsoft.github.io/graphrag/query/local_search/>.
- O CodeRAG-Bench recomenda medir separadamente recuperação e resposta final:
  <https://aclanthology.org/2025.findings-naacl.176/>.

## Runtime agentivo limitado

A 0.39.0 acrescenta um primeiro runtime agentivo sobre essa fundação. Depois
da recuperação inicial, o modelo observa metadados, proveniência e previews
limitados dos chunks reais. Em até quatro ciclos, escolhe até três ferramentas de
leitura entre `search_code`, `find_symbol`, `open_neighborhood`,
`open_related`, `find_callers` e `find_callees`. `open_related` abre chunks
citáveis de arquivos companheiros,
dependências e dependentes que já estejam ligados no mapa estrutural, sempre
reaplicando projeto, branch e ACL no SQL. Resultados de um ciclo alimentam a
decisão seguinte; ações repetidas ou fora do esquema são descartadas. Um
caderno de cobertura registra aspectos cobertos, parciais e lacunas e orienta
a síntese sem ser tratado como evidência.

Uma hipótese sem resultados não encerra a investigação: a contagem zero volta
como observação para que o ciclo seguinte possa mudar de vocabulário. Depois da
síntese, respostas longas são auditadas em lotes de até três afirmações. Isso
mantém cada retorno estruturado dentro de um orçamento previsível sem reduzir a
exigência de que todas as afirmações sejam ligadas às fontes que citam.

A 0.39.1 corrige achados da primeira execução real: perguntas formuladas como
“como funciona” ou que pedem um fluxo passam pelo mesmo runtime; uma decisão
que não encerra a investigação nem escolhe ferramenta é devolvida ao modelo
para replanejamento; todos os chunks associados ao caderno de cobertura são
preservados na seleção; e a auditoria usa lotes de até três afirmações com um
contrato de saída mais explícito. Menções genéricas a objetos ou métodos não
podem, por si sós, contar como evidência da operação qualificada na pergunta.

A 0.40.0 acrescenta duas salvaguardas genéricas observadas na validação real.
Quando duas decisões consecutivas não escolhem ferramenta — ou quando o JSON da
decisão é inválido — o servidor seleciona uma leitura de contingência. A seleção
ranqueia somente termos da pergunta e do plano contra caminhos, títulos e
previews já autorizados; seus alvos continuam limitados a chunks e símbolos
realmente observados. Não existem nomes de projeto, branch, subsistema ou arquivo
nessa política. O modelo volta a observar os resultados no ciclo seguinte.

A revisão posterior à auditoria agora recebe o rascunho anterior e apenas os
achados rejeitados ou incertos, ambos marcados como dados não confiáveis. Ela é
orientada a preservar afirmações aprovadas com suas citações, eliminar as
rejeitadas e não criar introduções ou conclusões factuais sem fonte. A resposta
continua bloqueada se a segunda auditoria encontrar qualquer afirmação sem
sustentação.

A validação real da 0.40.0 confirmou a seleção genérica da definição correta e
elevou a cobertura média da suíte de 51% para 85%, mas também mostrou que três
ciclos não permitiam ao modelo observar os últimos resultados e continuar até
chamadores e operações relacionadas. A 0.40.1 permite um quarto ciclo, aciona a
contingência também na última oportunidade e passa para o próximo candidato
observado quando as leituras do primeiro já foram usadas. Identificadores reais
são convertidos em termos separados somente para a busca, melhorando a chance
de encontrar usos cuja grafia difere da definição qualificada.

Para perguntas de mecanismo ou fluxo, o contrato do investigador passa a tratar
integração de entrada, coordenação e efeitos a jusante como papéis de cobertura.
Eles não são nomes científicos nem requisitos absolutos: o agente deve buscá-los
quando a pergunta exigir uma explicação ampla e registrar uma lacuna quando não
houver evidência. Se a correção textual ainda acrescentar afirmações rejeitadas,
uma consolidação determinística preserva somente as unidades já aprovadas e
submete esse subconjunto a uma terceira auditoria. Nenhuma afirmação rejeitada é
liberada apenas porque o restante da resposta passou.

A validação real da 0.40.1 chegou a 97,9% de cobertura de citações, mas revelou
dois defeitos diferentes. Uma expansão em torno de um nome genérico ocupava a
janela inteira e escondia hipóteses iniciais independentes; em outro caso, duas
frases amplas fizeram a resposta descartar 22 afirmações já aprovadas. A 0.40.2
passa a intercalar grupos de resultados na janela observável, preserva uma
leitura estrutural independente ao lado da hipótese escolhida pelo modelo e
adiciona `open_related`. A consolidação determinística passa a ser uma
salvaguarda obrigatória quando há unidades aprovadas, mesmo se a reescrita pelo
modelo estiver desativada na configuração local. O subconjunto continua sendo
auditado novamente e não recebe texto novo.

A 0.41.0 acrescenta um grafo genérico de chamadas ao mapa persistido. O extrator
identifica chamadas dentro de funções, subrotinas e programas, associa o destino
somente dentro do mesmo repositório e das ocorrências compartilhadas de
branch/commit e separa resolução qualificada, nome único, nome único na branch e
indício lexical de receptor. Múltiplos destinos continuam não resolvidos. As
ferramentas `find_callers` e `find_callees` aceitam apenas IDs de chunks já
observados e devolvem chunks primários depois de reaplicar escopo e ACL no banco.
Essa camada é conservadora e determinística; parsing sintático por linguagem
deverá aumentar sua precisão sem mudar o contrato de segurança.

A primeira execução real da 0.41.0 produziu 45.996 chamadas no MFSim-NG e
confirmou a travessia bidirecional no PostgreSQL. O caso de fluxo passou a
recuperar o ponto de integração no domínio e terminou com nove afirmações
auditadas e sustentadas. A suíte estrita permaneceu em 0/2: resultados novos de
`find_callees` eram observados, mas o agente voltava ao coordenador original e
o conjunto final descartava a fronteira do grafo. No caso de localização, isso
permitiu uma resposta verdadeira sobre código adjacente, mas que não respondia à
operação solicitada.

A 0.41.1 trata esse achado sem conhecer nenhum subsistema. Evidências retornadas
por chamadores e chamados recebem prioridade temporária enquanto ainda são uma
fronteira não explorada. Um chamador novo é percorrido por suas chamadas para
expor a orquestração ao redor do alvo; um chamado novo é seguido a jusante antes
de reabrir o coordenador. Até duas evidências por travessia são preservadas em
uma fila limitada e intercaladas com as escolhas do modelo no contexto final.
O SQL passa a ordenar arestas pela linha da chamada, em vez do hash do chunk, e
a síntese é instruída a omitir operações adjacentes que não respondam à pergunta.

O teste real mostrou que preservar toda nova aresta com prioridade absoluta
pode amplificar uma hipótese inicial ruim. A 0.41.2 passa a escolher uma amostra
pequena da fronteira conforme o vocabulário da pergunta e a posição estrutural,
reserva de fato uma ação por ciclo para uma hipótese independente e audita não
apenas se a afirmação é verdadeira nas fontes, mas se ela corresponde à
operação solicitada. Essa política permanece independente de nomes de projetos,
branches, arquivos ou métodos científicos.

Na 0.41.3, conhecimento geral do modelo pode participar da descoberta, mas não
da prova. O planejador pode propor siglas, sinônimos e estruturas convencionais
como hipóteses pesquisáveis; somente trechos recuperados podem estabelecer
cobertura. As conexões encontradas ao longo de todos os ciclos são reranqueadas
em conjunto, impedindo que a primeira direção explorada esgote a cota antes que
uma alternativa mais pertinente seja encontrada.

## Próximas camadas

O mapa ainda deverá receber parsing sintático por linguagem, chamadas e usos
de símbolos mais precisos e mais ferramentas estruturais. Essas extensões devem
continuar genéricas, versionadas e avaliadas separadamente por corpus.

O subgrafo realmente percorrido também deverá ser devolvido como dado público
da resposta e exibido, depois das citações, em um painel recolhível. A
visualização deve ser pequena e orientada à consulta, com nós para arquivos e
símbolos e arestas somente para relações persistidas no índice. Cada elemento
deve preservar projeto, branch, commit, linhas, ACL e vínculo com a fonte. Ela
não representa pensamento privado do modelo: representa apenas a trilha
estrutural verificável que ajudou a selecionar as evidências. Relações incertas
ou não resolvidas devem aparecer separadas das conexões confirmadas, nunca como
uma aresta factual comum.
