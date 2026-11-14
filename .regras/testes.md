# Regras de Testes

A auditoria financeira não pode falhar em cálculos aritméticos nem em decisões de vigência. Testamos o coração do sistema (regras temporais, matemática exata e abstenção) antes de construir a interface visual.

Documentos relacionados: [style.md](style.md), [agente.md](agente.md), [commits.md](commits.md), [decisoes.md](decisoes.md).

## 1. Ferramentas e Execução

- **Framework:** `pytest`, no grupo de dependências de desenvolvimento (`dev`) do `pyproject.toml`, via `uv`.
- **Comando padrão:**

```bash
uv run pytest -v
```

- **A suíte padrão nunca usa rede nem a API real de modelo de linguagem.** Os agentes são testados com um cliente falso (`FakeLLMClient`) que devolve saídas pré-definidas, inclusive saídas erradas de propósito. Execuções reais contra a API ficam em `evaluation/` e em testes marcados com `@pytest.mark.llm`, que o `pytest` ignora por padrão:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["llm: usa a API real de modelo de linguagem (fora da suíte padrão)"]
addopts = "-m 'not llm'"
```

## 2. Nomenclatura e Estrutura

- Todo arquivo de teste em `tests/` começa com `test_` (ex.: `test_motor_timeline.py`).
- `tests/unit/`: testes isolados (schemas Pydantic, cálculos do motor, verificação de extração).
- `tests/integration/`: fluxo contínuo (documento extraído → revisão → motor → classificação; persistência).
- Cenários reutilizáveis (contrato, aditivos, cobranças) ficam em `conftest.py` ou `tests/fixtures/`.
- Cada teste cita o ID do cenário no nome ou na docstring (`test_m06_...`), para rastrear a regra de negócio que protege.

## 3. Foco do MVP e Cenários Obrigatórios

Não buscamos 100% de cobertura. O esforço vai para os pontos que sustentam a confiabilidade. Cobertura pode ser medida em `backend/motor` apenas para achar ramos sem teste, sem meta numérica.

### A. Esquemas Pydantic (validação de entrada)

O sistema recusa dados corrompidos ou logicamente inválidos antes de processá-los.

| ID | Falha proposital | Esperado |
|---|---|---|
| S-01 | Data final de vigência menor que a inicial | `ValidationError` |
| S-02 | CNPJ mal formatado **e** CNPJ com dígitos verificadores inválidos | `ValidationError` |
| S-03 | Valor monetário negativo | `ValidationError` |
| S-04 | `float` onde se espera dinheiro | `ValidationError` |
| S-05 | Campo desconhecido no payload | `ValidationError` (`extra="forbid"`) |
| S-06 | Regra sem documento, página ou trecho literal | `ValidationError` (sem ancoragem não existe regra) |
| S-07 | Competência fora do formato `AAAA-MM` | `ValidationError` |
| S-08 | Valor inválido de enum (ex.: relação `SUBSTITUTED`, estado inexistente) | `ValidationError` |
| S-09 | Evidência de DOCX sem página, com cláusula e parágrafo | Válida e marcada como "sem página"; sem cláusula e parágrafo, `ValidationError` |

### B. Motor determinístico (comportamento de negócio)

| ID | Cenário | Validação esperada |
|---|---|---|
| M-01 | Seleção temporal | **Caso A:** out/2026 aplica o Aditivo 02 (R$ 10.500,00) e aponta diferença de R$ 300,00. **Caso B:** abr/2026 aplica a regra base (R$ 10.000,00), sem usar o aditivo mais recente. |
| M-02 | Reconstrução de vigência | Ao processar um aditivo, a versão anterior termina no dia imediatamente anterior ao efeito do aditivo; apenas a regra da mesma cláusula/tipo é encerrada (a franquia, por exemplo, segue vigente). |
| M-03 | Exatidão financeira | O motor recusa `float` e calcula somente com `Decimal` (ex.: `Decimal("0.1") + Decimal("0.2") == Decimal("0.3")`). |
| M-04 | Franquia e excedente | Horas excedentes cobradas × permitidas (medição − franquia): cobrança de 8 h com 6 permitidas sinaliza R$ 300,00 a mais. Excedente sem medição → `NEEDS_REVIEW`. |
| M-05 | Abstenção por conflito | Aditivos conflitantes na mesma data, ou redação ambígua: nenhuma escolha silenciosa; resultado `NEEDS_REVIEW` com motivo. |
| M-06 | Regra não revisada | Com a regra `PENDING_REVIEW`, a cobrança nunca é `DIVERGENCE` (no máximo `NEEDS_REVIEW` com "possível divergência"). |
| M-07 | Aditivo não revisado | Documento de aditivo com `reviewed=False` torna a consolidação incompleta: `NEEDS_REVIEW`. |
| M-08 | Fora da vigência | Competência posterior ao fim, sem aditivo de prorrogação: `NEEDS_REVIEW` (não `DIVERGENCE`), com aviso sobre prorrogação possivelmente não enviada. |
| M-09 | Mudança no meio do período | Vigência que muda dentro da competência, sem pró-rata definido: `NEEDS_REVIEW` com `MID_PERIOD_CHANGE`. |
| M-10 | Prioridade de classificação | `DIVERGENCE` prevalece sobre `NEEDS_REVIEW`, e os alertas de revisão continuam listados. |
| M-11 | Itens ambíguos | Mais de um item não excedente: diferença de preço vira `NEEDS_REVIEW`, nunca `DIVERGENCE`. A palavra "adicional" sozinha não classifica um item como excedente. |
| M-12 | Duplicidade | Mesmo número, ou mesma competência e mesmo valor sob o mesmo contrato: as duas cobranças recebem alerta (`NEEDS_REVIEW`). |
| M-13 | Reajuste periódico | Com data-base e periodicidade, o reajuste devido a cada 12 meses é detectado; preço posterior ao reajuste devido não gera alerta. |
| M-14 | Competência inferida | Competência obtida da data de emissão gera `NEEDS_REVIEW`. |
| M-15 | Sem contrato | Cobrança sem vínculo seguro com contrato: `MISSING_DOCUMENTATION`. |
| M-16 | Proibição de `float` | Um teste varre o código-fonte de `backend/motor` (análise da árvore sintática) e falha se encontrar `float(`, literais decimais usados em dinheiro ou anotação `float`. |
| M-17 | Aditivo revisado vs. não revisado | Em cobranças de competências diferentes, só o aditivo não revisado gera `NEEDS_REVIEW` na consolidação. |

### C. Agentes e extração (com cliente falso, sem rede)

| ID | Cenário | Validação esperada |
|---|---|---|
| A-01 | Trecho literal que não existe no documento | `INCONCLUSIVE` |
| A-02 | Valor extraído que não aparece no trecho | `INCONCLUSIVE` |
| A-03 | `new_value` igual a `previous_value` | `INCONCLUSIVE` |
| A-04 | Trecho do tipo "passa de R$ X para R$ Y" com X extraído como novo valor, sem `previous_value` | `INCONCLUSIVE` |
| A-05 | Saída do modelo fora do esquema | Aviso explícito ao usuário; nenhuma regra gravada; nunca descarte silencioso |
| A-06 | Documento que não rende nenhuma regra | Aviso "o sistema não presume que nada mudou"; documento permanece não revisado |
| A-07 | PDF sem camada de texto | Nenhuma extração; aviso de baixa qualidade de leitura |
| A-08 | Documento contendo texto de injeção ("ignore as instruções...") | O texto vai delimitado como dado; a saída continua sujeita a esquema e verificação; nada vira regra confiável sem revisão humana |
| A-09 | Extração válida | Regra entra `PENDING_REVIEW` com documento, página e trecho preenchidos; nunca `CONFIRMED` |
| A-10 | CPF e RG em texto enviado ao modelo | Mascarados antes do envio (verificado no texto que chega ao `FakeLLMClient`) |

### C.1 Agente Tradutor (equivalência semântica, com cliente falso)

| ID | Cenário | Validação esperada |
|---|---|---|
| T-01 | Tradutor devolve vínculo item-regra sem trecho que o justifique | Inválido (esquema) |
| T-02 | Vínculo abaixo do limiar de similaridade ou ambíguo | `NEEDS_REVIEW`, nunca cálculo |
| T-03 | Dois itens candidatos para a mesma regra | `NEEDS_REVIEW` |

### C.2 Relatório

| ID | Cenário | Validação esperada |
|---|---|---|
| R-01 | Texto explicativo com número que não consta no resultado do motor | Rejeitado pela verificação por código |

### D. Integração

| ID | Cenário | Validação esperada |
|---|---|---|
| I-01 | Fluxo completo | documento → regras `PENDING_REVIEW` → revisão humana → motor → classificação |
| I-02 | Cenário de desenvolvimento de 8 cobranças | Resultado igual à tabela da seção 4 |
| I-03 | Persistência | Gravar e recarregar preserva `Decimal` e datas sem perda |
| I-04 | Trilha de decisão | Cada decisão humana grava estado anterior, posterior, usuário e justificativa |
| I-05 | Recebimento de arquivo | Hash gravado; arquivo alterado gera hash diferente |
| I-06 | Corrigir uma regra depois de uma auditoria | A auditoria antiga preserva as regras e a memória de cálculo da época |

## 4. Cenário de Desenvolvimento: 8 Cobranças (inclui os Casos A e B)

Contrato `CT-2026-001`, vigência jan–dez/2026, franquia de 40 h, excedente de R$ 150,00/h. Regras: preço base R$ 10.000,00; Aditivo 01 (R$ 10.800,00 desde mai/2026); Aditivo 02 (R$ 10.500,00 desde set/2026). Todas as regras `CONFIRMED` e aditivos `reviewed=True` neste cenário.

| Cobrança | Competência | Situação | Classificação esperada |
|---|---|---|---|
| 101 (Caso B) | abr/2026 | R$ 10.000,00; 38 h | `NO_DIVERGENCE` |
| 102 | jun/2026 | R$ 10.800,00; 40 h | `NO_DIVERGENCE` |
| 103 | jul/2026 | R$ 10.800,00 + 8 h excedentes (R$ 1.200,00); 46 h medidas, 6 permitidas | `DIVERGENCE` (R$ 300,00 a mais) |
| 104 (Caso A) | out/2026 | R$ 10.800,00; vigente R$ 10.500,00 | `DIVERGENCE` (R$ 300,00; 2,86%) |
| 105 e 106 | ago/2026 | R$ 10.800,00 cada, mesmo fornecedor e competência | `NEEDS_REVIEW` (duplicidade) |
| 107 | jan/2027 | fora da vigência, sem prorrogação | `NEEDS_REVIEW` |
| 108 | nov/2026 | R$ 10.500,00 + 3 h excedentes; sem medição | `NEEDS_REVIEW` |

> **Origem dos casos:** somente os Casos A e B (cobranças 104 e 101) são os cenários de demonstração do projeto. As demais são cenários de desenvolvimento criados pela equipe. **Decisão registrada:** a cobrança 107 era `DIVERGENCE`; foi alterada para `NEEDS_REVIEW` porque, com os documentos disponíveis, o sistema não distingue "cobrança indevida" de "prorrogação ainda não enviada" (ver [decisoes.md](decisoes.md)). Confirme com a equipe. Mudança em commit próprio.

## 5. Qualidade dos Próprios Testes

- **O cenário de desenvolvimento não substitui** o conjunto de avaliação independente (ver seção 6 e `evaluation/`).
- **O gabarito é independente do código.** Casos novos são escritos e anotados à mão **antes** de rodar o sistema. Não ajuste uma resposta esperada para fazer o teste passar.
- **Casos de regressão para cada bug encontrado:** todo erro corrigido ganha um teste que o reproduz (use a frase ou o documento real que causou o erro).
- **Verificação manual de sensibilidade** antes de entregas importantes: quebre de propósito uma regra do motor (ex.: trate regra pendente como confiável) e confirme que algum teste falha. Teste que continua passando com o código quebrado não protege nada.
- **Teste de propriedade (opcional):** para o motor temporal, gerar sequências aleatórias de aditivos e verificar que, para cada mês, no máximo uma regra do mesmo tipo é considerada vigente, ou o resultado é `NEEDS_REVIEW`.

## 6. Avaliação da Extração (fora do `pytest`)

Roda em `evaluation/`, com documentos públicos ou sintéticos:

- Frases e contratos com resposta anotada à mão, em conjunto de desenvolvimento e conjunto de validação separados.
- Resultado por caso: `acerto`, `erro_silencioso`, `sinalizada` (enviada à revisão), `omissao`.
- **Métrica principal: erros silenciosos.** Cada um é analisado individualmente.
- Comparação obrigatória com uma linha de base por regex.
- Nova rodada a cada mudança de prompt ou de modelo; o resultado vai no commit.
- **Falsos positivos e falsos negativos** da classificação (divergência apontada sem existir / existente e não apontada).
- **Cálculo comparado com valores independentes:** resultados do motor conferidos com valores calculados à parte.
- **Usabilidade:** observar se o usuário localiza a justificativa (documento, página, trecho) de cada resultado.

Métricas e estrutura detalhadas em [evaluation/README.md](../evaluation/README.md).
