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

A validação real mostrou que descobrir uma conexão não garantia sua presença na
janela final: métodos irmãos de um único arquivo podiam deslocar chamadores,
objetos de estado e trechos que o próprio agente havia associado à cobertura.
Na 0.41.4, a seleção preserva primeiro caminhos distintos entre resultados
relevantes, coloca evidências de cobertura antes de escolhas incidentais e
reserva parte das observações para o resultado de ferramenta mais recente. Um
quinto ciclo limitado fica disponível somente quando os anteriores ainda não
encerraram a investigação. Essas regras operam apenas sobre metadados e
evidências observados, sem vocabulário de projeto ou domínio embutido.

Na 0.41.5, arestas de chamada produzidas pela ferramenta mais recente precedem
vizinhanças e buscas na cota observável. Duas coberturas completas e idênticas
encerram o agente, evitando ciclos sem ganho. O empacotamento final reserva uma
parcela do orçamento para várias fontes ordenadas, impedindo que um único
arquivo longo elimine integrações já descobertas. Essa divisão não atribui
verdade nem relevância por tamanho: a ordem continua vindo da investigação e
cada afirmação continua sujeita à auditoria semântica.

O orçamento atual permanece limitado por uma chamada ao modelo. A resposta usa
um teto configurável e pode terminar antes dele; mecanismos complexos recebem
instrução de profundidade proporcional à pergunta. Para ultrapassar com
segurança a janela única do provedor, a próxima arquitetura deverá sintetizar
seções auditadas por aspecto e compô-las hierarquicamente sem perder as fontes
primárias. Aumentar apenas o contexto ou a saída não substitui essa etapa.

Na 0.41.6, uma cobertura declarada como completa para perguntas de mecanismo
não encerra a exploração enquanto nenhuma travessia de chamada tiver devolvido
evidência. Os chunks já associados à cobertura fornecem os alvos da sondagem,
sem criar símbolos ou caminhos. Se o último ciclo revelar uma fronteira, ela
recebe um salto final limitado antes da seleção. A síntese recebe no máximo seis
fontes para preservar diversidade sem consumir toda a janela com trechos
laterais.

Ausência de marcação de fonte e ausência de sustentação passam a ser problemas
separados. Uma etapa de descoberta pode associar fontes a unidades textuais
exatas, mas não pode alterá-las. Toda associação é validada estruturalmente e
depois submetida à mesma auditoria de implicação usada para citações produzidas
na síntese. Essa etapa não transforma similaridade lexical em prova.

O teste real da 0.41.6 distinguiu dois tipos de lacuna. Em uma consulta, o
agente explicou configuração e remesh com afirmações auditadas, mas não
investigou separadamente onde a implementação concreta era construída. Em
outra, três aspectos locais estavam presentes, porém apenas o primeiro recebeu
sondagem estrutural. Na 0.41.7, perguntas de inicialização mantêm construção ou
fábrica, implementação concreta, configuração e uso como hipóteses distintas.
Quando o servidor precisa conectar cobertura local ao fluxo, escolhe um alvo
observável por aspecto e procura até três chamadores. Isso evita que o primeiro
construtor ou helper esgote a cota, sem inventar símbolos ou aceitar hipóteses
como evidência.

Itens de apresentação contendo somente um símbolo ou caminho em código inline
e uma citação não entram no conjunto de afirmações factuais. Eles indicam apenas
a existência do item naquela fonte, não chamada, efeito ou papel no fluxo. Uma
lista que acrescenta qualquer verbo ou explicação continua sendo auditada como
prosa factual; portanto, essa distinção não transforma presença de citação em
prova automática.

A execução real da 0.41.7 confirmou que sondar aspectos distintos encontra
integração a montante, mas também mostrou duas perdas posteriores. Perguntas de
localização multietapa ainda podiam encerrar sem grafo, e vários chunks de um
coordenador podiam excluir uma implementação ou estado já observados. Na
0.41.8, localizações com múltiplos aspectos recebem a mesma exigência mínima de
conexão estrutural. Perguntas de inicialização reservam uma consulta genérica
para construção ou fábrica, independentemente das hipóteses produzidas pelo
modelo; o resultado continua precisando ser observado e auditado.

A composição final mantém uma pequena amostra relevante da recuperação base em
paralelo ao caderno e ao grafo. O empacotador reserva até quatro caminhos
distintos e mantém duas posições para métodos complementares de um mesmo
arquivo. Essa diversidade é de
evidência, não de verdade: não promove um arquivo a mecanismo correto, e toda
afirmação produzida continua vinculada às fontes e submetida à auditoria.

Desde a 0.42.3, aspectos obrigatórios precisam carregar uma citação literal da
pergunta (`question_span`). Conceitos apenas úteis para procurar código podem
continuar em consultas e identificadores, mas não bloqueiam a completude. Após
a auditoria de cada afirmação, uma checagem separada compara somente as
afirmações sustentadas com esses aspectos ancorados. Dessa forma, remover uma
sobreafirmação não transforma automaticamente uma explicação suficiente em
resposta parcial, enquanto pedidos explícitos de fluxo, comparação ou trechos
de código continuam exigindo que essa forma apareça na resposta final.

Na 0.42.4, cada aspecto ancorado recebe um identificador estável e opaco na
auditoria final (`A1`, `A2` e assim por diante). O modelo julga a cobertura por
esse identificador, de modo que traduzir ou parafrasear acidentalmente o rótulo
do aspecto não transforme todos os resultados válidos em lacunas. O servidor
continua publicando o rótulo original validado, e IDs de afirmações que não
tenham passado pela auditoria factual continuam rejeitados.

A remoção determinística de sobreafirmações também deixa de apagar um exemplo
de código válido apenas porque blocos cercados não são unidades de prosa. Um
bloco só é preservado quando seu conteúdo é uma substring exata de uma fonte
autorizada citada por uma afirmação já aprovada. Código reconstruído, alterado,
proveniente de outra fonte ou sem vínculo com uma afirmação sustentada é
descartado. Essa regra é genérica e não depende de projeto, linguagem, caminho,
branch ou símbolo conhecido antecipadamente.

O teste real da 0.42.4 confirmou que os blocos exatos sobreviveram à redução,
mas expôs o mesmo problema de identidade também dentro do ciclo exploratório:
o modelo encontrava evidências úteis e ainda devolvia um caderno vazio ou com
rótulos reformulados. Na 0.42.5, os IDs estáveis acompanham os aspectos desde a
primeira decisão de ferramenta até a reconciliação final. O rótulo aceito pelo
servidor sempre substitui qualquer reformulação feita pelo modelo antes de um
chunk ser associado à cobertura.

A checagem final deixa de pedir uma decisão conjunta sobre até seis aspectos.
Cada faceta é julgada em uma chamada curta e independente, usando somente as
afirmações já aprovadas; uma saída inválida ou excessivamente conservadora não
apaga os resultados das demais. O empacotamento também reserva cinco dos seis
lugares para caminhos distintos e ainda mantém um lugar para outro método do
mesmo arquivo. Isso reduz a perda de implementações já observadas sem assumir
qual arquivo ou subsistema deveria ser escolhido.

Na 0.42.6, uma auditoria isolada de aspecto é tratada como posicional: como a
chamada contém somente uma faceta pertencente ao servidor, omitir o ID ou
traduzir o rótulo não torna sua identidade ambígua. Status e IDs de afirmação
continuam limitados aos valores validados; essa tolerância não cria cobertura
nem aceita uma afirmação nova.

Veredictos factuais passam a ser reutilizados durante a mesma pergunta quando
o texto integral da afirmação e o conjunto de fontes citadas forem idênticos.
Qualquer alteração invalida a chave e exige nova auditoria. Isso impede que a
mesma unidade oscile entre sustentada e rejeitada apenas por ser reavaliada
depois de uma poda, mantendo a verificação conservadora para conteúdo novo. As
chamadas estruturadas usam ainda uma semente fixa aceita pelo provedor local.

Por fim, uma amostra pequena e ranqueada das observações iniciais é preservada
como candidata de base. Evidências descobertas cedo por consultas distintas não
somem apenas porque ciclos posteriores produziram muitos vizinhos do mesmo
coordenador. A seleção continua baseada na pergunta, nas hipóteses configuradas
e nos resultados observados, sem vocabulário científico incorporado ao motor.

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
