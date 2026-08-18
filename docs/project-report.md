# Plano e estado de desenvolvimento do MFLab Knowledge RAG

## Plano inicial

O projeto foi concebido para organizar e disponibilizar o conhecimento técnico
do MFLab de forma automática, local, versionada e verificável. O problema inicial
identificado foi a dispersão das informações entre repositórios, branches,
documentos, casos de simulação, registros do GitLab e conhecimento acumulado
pelos pesquisadores. Também foi considerada a dificuldade de localizar uma
informação sem confundir versões distintas do MFSim, código atual, implementações
experimentais e materiais históricos.

Como primeira decisão de arquitetura, foi adotada uma solução baseada em RAG,
ou *Retrieval-Augmented Generation*, em vez do treinamento imediato de um modelo
com os dados do laboratório. Essa escolha permite que o conteúdo seja atualizado
ou removido sem novo treinamento, preserva a origem de cada informação e permite
que as respostas sejam acompanhadas por repositório, branch, commit, arquivo e
intervalo de linhas. O treinamento ou ajuste fino de modelos permaneceu como uma
possibilidade futura para necessidades de comportamento e formato, e não como
mecanismo principal de armazenamento de fatos técnicos.

O plano inicial foi dividido em camadas independentes. A primeira camada seria
responsável pela descoberta e pelo inventário das fontes autorizadas. A segunda
realizaria a normalização, a divisão estrutural do conteúdo e a preservação dos
metadados. A terceira armazenaria o catálogo, o índice lexical e os vetores. A
quarta faria a recuperação híbrida e produziria contextos citáveis. A quinta
disponibilizaria a geração de respostas por um modelo local. Posteriormente,
seriam adicionadas uma interface web, autenticação multiusuário, integrações com
agentes e conectores para outras fontes do laboratório.

Desde o início, foi estabelecido que os repositórios científicos seriam tratados
como somente leitura. Também foi definido que nomes de projetos, branches,
arquivos, símbolos e casos científicos não seriam incorporados ao motor do
indexador. Essas informações seriam fornecidas por arquivos de configuração e
por suítes de avaliação, permitindo que o mesmo serviço atendesse repositórios
com estruturas e convenções diferentes.

## Desenvolvimento realizado

Foi criado um projeto independente dos solvers, denominado MFLab Knowledge RAG.
O indexador foi implementado em Python e passou a operar sobre mirrors isolados e
snapshots imutáveis dos commits. Dessa forma, nenhuma troca de branch, alteração
de arquivo ou operação de escrita é realizada nos clones científicos utilizados
como fonte.

Foi implementada uma configuração multi-repositório por arquivo TOML. Cada
repositório pode definir sua origem, projeto, classe de acesso, branch canônica,
escopo de branches, perfil de inventário e filtros opcionais. A branch padrão
também pode ser descoberta a partir do servidor remoto. Todas as branches
remotas são identificadas automaticamente, e commits compartilhados podem
reutilizar o mesmo inventário. A estrutura resultante preserva a relação de cada
branch com a canônica sem inventar relações de filiação que não são registradas
formalmente pelo Git.

O primeiro inventário do MFSim-NG revelou que uma varredura irrestrita incluiria
builds, documentação gerada, binários e muitos formatos inadequados para o RAG.
Por esse motivo, a enumeração foi ajustada para considerar o conteúdo versionado,
e foram criados perfis declarativos de inclusão e exclusão. Esses perfis foram
retirados do motor e colocados em uma política versionada, permitindo que novos
repositórios recebam regras próprias sem alterações no código.

Foi implementada a normalização incremental dos arquivos autorizados. Documentos
e chunks preservam formato, caminho, linhas, título ou símbolo identificado,
classe de acesso e todas as ocorrências por branch e commit. Conteúdos idênticos
compartilhados entre branches são deduplicados, sem perder sua proveniência. O
resultado é armazenado no PostgreSQL, que também fornece a busca textual. O
pgvector foi integrado para armazenar embeddings locais de 1.024 dimensões.

Foi implementada uma busca híbrida que combina correspondência lexical e
similaridade semântica. A busca lexical atende identificadores, nomes de arquivos
e mensagens exatas, enquanto a busca vetorial atende perguntas conceituais em
linguagem natural. Também foi adicionada uma expansão de contexto baseada em
relações estruturais genéricas, como pares entre fontes e headers, documentos
vizinhos e arquivos pertencentes a um mesmo conjunto configurado. Os filtros de
projeto, branch, caminho e acesso são aplicados antes que o conteúdo seja
retornado.

Foi criada uma API HTTP local com endpoints de saúde, estado, repositórios,
busca, montagem de contexto e perguntas. O contexto entregue ao modelo recebe
fontes identificadas como `S1`, `S2` e assim por diante, além de instruções para
tratar o conteúdo recuperado apenas como evidência não confiável. As respostas
são verificadas quanto às citações utilizadas, à cobertura das afirmações e à
presença de referências inválidas. Quando não existe evidência suficiente, é
produzida uma abstenção em vez de uma resposta inventada. Quando são usados
projetos, branches ou commits diferentes, os escopos são apresentados
separadamente.

Foi instalado um modelo de embeddings local e foi configurado um servidor vLLM
com um modelo Qwen3-8B-FP8 para geração. O PostgreSQL, o pgvector, a API, o
servidor de modelo e o indexador foram mantidos na estação Morgoth. O conteúdo
dos repositórios não é enviado a serviços externos durante a recuperação ou a
geração.

Foi implementado um pipeline único para sincronização, normalização, carga no
banco e cálculo dos embeddings. Esse pipeline é idempotente e incremental.
Mirrors, snapshots, inventários, documentos, chunks e vetores são reutilizados
quando não foram alterados. Embeddings novos são confirmados em checkpoints, o
que permite retomar uma execução interrompida sem recalcular o trabalho já
concluído.

Foi adicionada uma execução não assistida por `systemd`. Um timer verifica os
repositórios aproximadamente a cada cinco minutos, utiliza uma trava para evitar
processamentos simultâneos e registra estado, progresso, duração e histórico. O
polling periódico foi considerado suficiente para o piloto, e a implementação
de webhooks deixou de ser uma prioridade imediata. A operação normal não exige
terminal aberto nem acompanhamento manual.

Durante a validação da GPU, foi detectada falta de memória quando o vLLM, a API
de embeddings e o indexador permaneciam carregados ao mesmo tempo. O uso de CPU
eliminou a falha, mas apresentou desempenho insuficiente. A configuração local
foi então ajustada para limitar o vLLM a 68% da memória da GPU e executar o
indexador em CUDA com batch quatro. Após o ajuste, 411 embeddings novos foram
processados em aproximadamente 63 segundos, com cerca de 2,9 GiB de VRAM livres
ao final da validação.

No estado registrado em 18 de agosto de 2026, foram indexadas 17 branches e 17
commits do MFSim-NG, além de 107 branches e 104 commits do MFSim CMake. Foram
armazenados 12.332 embeddings do MFSim-NG e 83.468 embeddings do MFSim CMake,
totalizando 95.800 chunks vetorizados. Esses números representam um snapshot e
serão atualizados automaticamente quando o conteúdo dos repositórios mudar.

Foram criadas avaliações versionadas para impedir regressões. A suíte conceitual
corrigida do MFSim-NG passou em cinco de cinco casos e dez de dez expectativas.
A regressão de símbolos também passou em cinco de cinco casos. A avaliação ponta
a ponta das respostas da API passou em quatro de quatro casos, com cobertura
média de citações de 100%. Também foram validadas a busca canônica do MFSim
CMake, a preservação de proveniência, a abstenção sem evidência e a rejeição de
um endpoint externo de geração.

## Situação atual

O piloto já dispõe de um indexador multi-repositório e multi-branch, banco
PostgreSQL com pgvector, busca híbrida, modelo de embeddings, modelo gerador,
API citável e atualização automática. O MFSim-NG e o MFSim CMake permanecem
separados no banco e podem ser consultados com filtros de projeto, branch e
caminho. As configurações locais e as credenciais permanecem fora do Git, e o
token utilizado para leitura do GitLab não possui permissão de escrita nos
repositórios.

A indexação do MFSim CMake foi validada quanto à cobertura e à proveniência, mas
ainda não possui uma suíte científica de recuperação e respostas com a mesma
profundidade da suíte criada para o MFSim-NG. Também continua sendo utilizado um
parsing parcialmente heurístico para identificar símbolos e seções do código.

## Etapas futuras

Como próxima etapa, será criada uma suíte de avaliação específica para o MFSim
CMake. Serão selecionadas perguntas reais, em português e inglês, e as fontes
esperadas serão verificadas diretamente no código. Serão avaliadas a recuperação
lexical, a recuperação conceitual, as respostas com citações, a preservação da
branch e do commit, a ausência de mistura com o MFSim-NG e a abstenção quando a
evidência não estiver disponível. Os dados científicos do gabarito permanecerão
em arquivos de avaliação, sem serem incorporados às regras do motor.

Em seguida, serão ampliadas as avaliações que envolvem múltiplos repositórios e
múltiplas branches. Serão incluídos casos capazes de detectar respostas que
combinem silenciosamente versões incompatíveis. As métricas de recuperação e de
resposta serão mantidas como critérios de regressão antes da implantação de cada
alteração.

O parsing heurístico será substituído gradualmente por parsing estrutural
genérico para C, C++, Fortran, CMake e arquivos de configuração. Serão
identificadas funções, classes, procedimentos, módulos, assinaturas, includes e
relações entre símbolos sem depender de nomes específicos do MFSim. Casos de
simulação também poderão ser representados como conjuntos estruturados de
configurações, geometrias, UDFs e requisitos de execução.

Antes que a API seja disponibilizada para outros usuários da rede, será
implementada autenticação e será definida uma política de acesso por usuário ou
grupo. O serviço de busca continuará separado do indexador e do servidor de
modelo. Limites de requisições, filas, auditoria e concorrência serão avaliados
com base no uso real da estação.

Posteriormente, será criado um painel web para consulta e operação. O painel
deverá mostrar saúde dos serviços, repositórios configurados, branches,
quantidade de documentos e embeddings, execuções em andamento, progresso,
falhas, próximas atualizações, histórico, requisições e fontes utilizadas nas
respostas. Também deverá permitir busca e chat com citações sem exigir o uso do
terminal.

Conectores adicionais poderão ser desenvolvidos para issues, merge requests,
comentários, documentos técnicos, artigos, teses, relatórios e apresentações,
desde que cada coleção seja autorizada e classificada. PDFs serão tratados com
preservação de páginas, estrutura e tabelas, e resultados científicos volumosos
serão catalogados por metadados em vez de inseridos integralmente no RAG.

Por fim, poderá ser criada uma camada MCP para permitir que agentes consultem o
mesmo serviço. Inicialmente serão oferecidos apenas recursos de leitura. Qualquer
ferramenta capaz de executar compilação, diagnóstico ou simulação será tratada
separadamente, com parâmetros validados, limites de recursos, timeout, auditoria
e aprovação humana quando houver efeitos sobre o ambiente.

## Resultado esperado

Ao final das etapas previstas, será disponibilizado um serviço interno capaz de
acompanhar automaticamente a evolução dos repositórios e das demais fontes
autorizadas do MFLab. Pesquisadores poderão localizar implementações,
configurações, decisões e documentos por linguagem natural ou por termos exatos,
recebendo respostas acompanhadas por fontes verificáveis. O sistema permanecerá
atualizável, auditável e independente de um único modelo ou interface, sem
substituir a revisão técnica nem transformar respostas geradas em fonte de
verdade.

