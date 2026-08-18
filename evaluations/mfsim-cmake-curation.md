# Curadoria inicial do MFSim CMake

Estado observado em 18 de agosto de 2026 na branch canônica `master`, commit
`3cdbff4811a9`.

## Casos promovidos ao piloto

- Ciclo do DPM: `src_lag/DPM/dpm.c` apareceu como centro do rastreamento e
  `src_lag/DPM/dpm_fortran_functions.f90` apresentou a adição e advecção das
  partículas.
- Pressão/Poisson: `src_mat/mat_poisson_disc_simple.f90` apresentou a montagem
  PETSc/PISO e `src_amr/eul_pres_multigrid.f90` apresentou
  `pressure_solvers`.
- Refinamento adaptativo: `src_vof/vof_amr3d.f90` apresentou
  `vof_refinement`, enquanto `src_amr/eul_refinement.f90` apresentou os gatilhos
  e projeções usados pelo refinamento.

Essas referências foram promovidas somente para um gabarito piloto. O objetivo
é detectar regressões de recuperação e isolamento de escopo, não declarar que
esses arquivos esgotam cada mecanismo científico.

## Casos ainda não promovidos

- Comunicação MPI de partículas: a consulta recuperou principalmente
  comunicação de pontos Lagrangianos de IB e conversão VOF. Isso não comprova o
  mecanismo de comunicação das partículas do DPM.
- Saída Lagrangiana: a consulta recuperou leitura HDF5 genérica e conversão VOF,
  sem evidência suficientemente específica sobre a saída do DPM.

Esses dois casos exigem inspeção direta do snapshot antes de receber caminhos
esperados. Resultados do próprio recuperador não devem ser transformados em
verdade de referência sem essa confirmação.

## Resultado da execução piloto

As suítes promovidas foram executadas na Morgoth em 18 de agosto de 2026:

- recuperação híbrida: 3/3 casos, 6/6 expectativas, recall 100% e MRR 1,000;
- respostas `/ask`: 4/4 casos, incluindo abstenção, com cobertura média de
  citações de 100%;
- todas as fontes retornadas preservaram projeto `MFSim CMake`, branch `master`
  e commit `3cdbff4811a9`;
- pico de GPU: 12.969 MiB de 16.311 MiB.
