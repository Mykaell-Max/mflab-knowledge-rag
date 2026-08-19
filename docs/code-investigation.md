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
limitados dos chunks reais. Em até três ciclos, escolhe até três ferramentas de
leitura entre `search_code`, `find_symbol` e `open_neighborhood`. Resultados de
um ciclo alimentam a decisão seguinte; ações repetidas ou fora do esquema são
descartadas. Um caderno de cobertura registra aspectos cobertos, parciais e
lacunas e orienta a síntese sem ser tratado como evidência.

Uma hipótese sem resultados não encerra a investigação: a contagem zero volta
como observação para que o ciclo seguinte possa mudar de vocabulário. Depois da
síntese, respostas longas são auditadas em lotes de até cinco afirmações. Isso
mantém cada retorno estruturado dentro de um orçamento previsível sem reduzir a
exigência de que todas as afirmações sejam ligadas às fontes que citam.

## Próximas camadas

O mapa ainda deverá receber parsing sintático por linguagem, chamadas e usos
de símbolos mais precisos, busca bidirecional entre chamadores e definições e
mais ferramentas estruturais. Essas extensões devem continuar genéricas,
versionadas e avaliadas separadamente por corpus.
