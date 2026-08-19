# Evolução do RAG para assistente técnico

## Objetivo

O serviço deve responder dúvidas gerais, localizar implementações, comparar
projetos e branches e apoiar a investigação de problemas. A resposta final
continua baseada em código e documentação autorizados, com proveniência por
projeto, branch, commit, caminho e linhas.

## Camadas

1. **Escopo**: resolve filtros explícitos e menções de projetos ou branches.
2. **Mapa qualitativo**: descreve arquivos, diretórios, símbolos, módulos e
   relações como artefatos derivados e versionados.
3. **Planejamento**: decompõe a pergunta em aspectos recuperáveis sem produzir
   fatos.
4. **Exploração limitada**: consulta mapas, símbolos, vizinhança e diferenças
   entre branches por ferramentas somente leitura.
5. **Evidência**: recupera os trechos originais que sustentam a resposta.
6. **Verificação**: mede cobertura, separa escopos e se abstém quando necessário.

## Índice qualitativo incremental

O mapa completo será calculado apenas para a branch preferencial de cada
repositório. Outras branches armazenarão deltas em relação à referência:

- arquivos adicionados, removidos e alterados;
- símbolos afetados;
- módulos e relações possivelmente modificados;
- resumos derivados ligados aos chunks que lhes deram origem.

Cada nó derivado terá hash de entrada, modelo, versão do prompt, commit e IDs de
fontes. Conteúdo inalterado será reutilizado. O mapa serve para navegação; fatos
da resposta devem apontar para as fontes primárias.

## Exploração limitada

O agente receberá operações específicas, nunca shell ou escrita no Git:

- listar módulos e símbolos;
- buscar conceitos e nomes;
- abrir trechos e arquivos vizinhos;
- seguir relações estruturais;
- comparar branches ou commits;
- verificar cobertura das evidências.

A execução terá limites de passos, tempo e volume de contexto. Toda operação
aplicará ACL antes de retornar texto e ficará registrada para futura exibição no
painel administrativo.

## Etapas de entrega

1. Catálogo completo de branches, aliases e branch preferencial.
2. Escopos automáticos visíveis e comparações balanceadas.
3. Extração determinística de símbolos e relações estruturais.
4. Resumos hierárquicos incrementais com proveniência.
5. Planejador local e explorador limitado.
6. Avaliações de localização, conceito, diagnóstico e comparação.
