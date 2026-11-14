# evaluation/

Avaliação dos agentes de IA contra a API real. **Fica fora da suíte `pytest` padrão**, que nunca usa rede (ver [.regras/testes.md](../.regras/testes.md) e [.regras/agente.md](../.regras/agente.md), seção 5).

## Estrutura

```text
evaluation/
├── README.md        # este arquivo: métricas e regras
├── phrases/         # frases de aditivo com resposta conhecida
├── contracts/       # contratos públicos ou sintéticos anotados à mão
└── results/         # saídas de cada rodada (versão do prompt, modelo, data)
```

## Regras

- **Somente documentos públicos, sintéticos ou anonimizados.**
- Dois conjuntos separados: **desenvolvimento** (para ajustar prompts e regras) e **validação** (nunca usado para ajustes).
- Os casos de validação são escritos e **anotados à mão antes** de rodar o sistema.
- **Toda mudança de prompt ou de modelo** exige nova rodada; o resultado vai no corpo do commit (`feat(prompts)` / `fix(prompts)`).
- Comparação obrigatória com uma **linha de base por regex**.
- O cenário de desenvolvimento das 8 cobranças (`testes.md`, seção 4) **não substitui** este conjunto independente.

## Métricas

| Métrica | Descrição |
|---|---|
| **Erro silencioso** (principal) | Valor errado que seguiria para a revisão sem nenhum alerta. Cada ocorrência é analisada individualmente. |
| Acerto | Extração correta e ancorada. |
| Sinalizada | Enviada à revisão (`INCONCLUSIVE`, `NEEDS_REVIEW`). |
| Omissão | Regra existente que não foi extraída. |
| Falsos positivos e falsos negativos | Divergências apontadas sem existir / existentes e não apontadas. |
| Cálculo | Resultado do motor comparado com valores calculados de forma independente. |
| Usabilidade | Observar se o usuário localiza a justificativa (documento, página, trecho) de cada resultado. |

## Registro de cada rodada (`results/`)

Cada rodada grava: versão do prompt, identificador do modelo, data, conjunto usado e contagem por métrica.

