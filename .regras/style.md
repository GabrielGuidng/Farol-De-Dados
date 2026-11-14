# Style Guide Oficial - Farol de Dados

Este documento define as regras inegociáveis de escrita de código. O objetivo é que qualquer módulo (Front-end, Motor ou Agentes) pareça escrito por uma única pessoa, com reprodutibilidade matemática e rastreabilidade.

Documentos relacionados: [agente.md](agente.md), [commits.md](commits.md), [testes.md](testes.md), [decisoes.md](decisoes.md).

## 1. Estrutura do Repositório

Os nomes de pastas de negócio ficam em português; tudo dentro delas fica em inglês (seção 3).

```text
backend/
  motor/        # núcleo determinístico: vigência, cálculo, classificação (sem IA)
  agentes/      # extração com modelo de linguagem + prompts.py
  schemas/      # modelos Pydantic (contratos de dados)
  persistencia/ # repositório (SQLite) e registro de decisões em modo de acréscimo
  api/          # rotas FastAPI
frontend/       # Streamlit
tests/
  unit/
  integration/
evaluation/     # conjuntos de avaliação e scripts (fora da suíte pytest padrão)
data/           # SOMENTE documentos públicos ou sintéticos (ver agente.md, seção 4)
```

Regra de dependência: `motor` não importa `agentes`. O motor recebe dados já validados e nunca sabe de onde vieram. `agentes` não importa `motor`. `persistencia` depende apenas de `schemas`.

## 2. Tipagem Obrigatória e Contratos de Dados

Toda função, método e rota declara os tipos de entrada e saída. Não subimos código com `Any` em funções de negócio.

- **`ruff`:** regras `ANN` ativas (inclui `ANN401`, que proíbe `Any`). Em `tests/` as anotações são dispensadas; em `backend/` e `frontend/` não.
- **Contratos de dados:** toda transferência entre agentes, API, motor e Streamlit passa por esquemas Pydantic.
- **Esquemas estritos:** `extra="forbid"` (campo desconhecido é erro) e `frozen=True` nos modelos de domínio.
- **Evidência faz parte do esquema:** uma regra contratual sem documento de origem e trecho literal não vazio é **inválida**. A localização exige **página ≥ 1** ou, para DOCX sem conversão para PDF, **cláusula e parágrafo** preenchidos; neste caso a evidência é marcada com um indicador de "sem página". Sem página e sem cláusula/parágrafo, a evidência é inválida. O esquema impede, por construção, uma regra sem ancoragem.

```python
# Errado
def calcular_diferenca(valor1, valor2):
    pass


# Certo
def calculate_difference(charged_amount: Decimal, rule_value: Decimal) -> Decimal:
    pass
```

Dinheiro em esquemas: use um tipo único que rejeita `float` explicitamente e valores negativos.

```python
from decimal import Decimal
from typing import Annotated

from pydantic import BeforeValidator, Field


def reject_float(value: object) -> object:
    """Recebe o valor bruto; devolve o mesmo valor ou levanta ValueError se for float."""
    if isinstance(value, float):
        raise ValueError("float nao e permitido para dinheiro: use Decimal ou str")
    return value


Money = Annotated[Decimal, BeforeValidator(reject_float), Field(ge=0)]
```

Validações obrigatórias nos esquemas: data final ≥ data inicial; CNPJ com **dígitos verificadores** válidos (não apenas formato); valores monetários não negativos; competência no formato `AAAA-MM`.

## 3. Idioma, Nomenclatura e Padrão de Casos

- **Inglês (lógica e estrutura):** variáveis, funções, classes, métodos, arquivos e enums (`calculate_difference`, `timeline_manager.py`, `AuditClassification`).
- **Português (interface e IA):** mensagens do Streamlit, textos de aviso ao usuário, justificativas exibidas na auditoria e prompts enviados ao modelo.
- **Exceção de pastas:** `motor/` e `agentes/` mantêm os nomes em português.

Convenções:

- `snake_case` para variáveis e funções (`contract_id`, `extract_text()`).
- `PascalCase` para classes e modelos Pydantic (`ContractRule`, `AuditResult`).
- `UPPER_CASE` para constantes e variáveis de ambiente (`MAX_RETRY_ATTEMPTS`).

## 4. Dinheiro e Temporalidade (O Core do Motor)

- **Dinheiro é `Decimal`, construído a partir de string.** Correto: `Decimal("10500.00")`. Incorreto: `Decimal(10500.0)`, `float()`.
- **Nenhum `float` em `backend/motor`.** Isso é verificado por um teste automático (ver testes.md, M-16), não só por revisão.
- **Arredondamento:** 2 casas, `ROUND_HALF_UP`, aplicado **uma vez no resultado** de cada operação monetária (ex.: horas × preço), por uma única função utilitária. Nunca arredonde valores intermediários.
- **Preço unitário pode ter mais de 2 casas** (a NF-e admite até 10). Guarde-o com a precisão original; arredonde apenas o total.
- **Tolerância de comparação** é parâmetro explícito de cada contrato, nunca uma constante escondida no código.
- **Percentuais** (ex.: diferença relativa) também em `Decimal`, apenas para exibição.

## 5. Datas e Vigência

- Use `datetime.date` (nunca `datetime.datetime`) para `data_inicio_vigencia` e `data_fim_vigencia`.
- **O fim é inclusivo.** Vigência aberta é `None` (não use datas-sentinela como `9999-12-31`).
- **Regra do aditivo:** ao processar um aditivo que altera uma regra, a versão anterior termina no dia imediatamente anterior ao início do efeito do aditivo. O encerramento vale apenas para a regra **da mesma cláusula/tipo** que o aditivo altera; as demais permanecem vigentes.
- **Competência é um período, não um dia.** Uma cobrança de `2026-10` cobre de `2026-10-01` a `2026-10-31`. Uma regra se aplica à cobrança se sua vigência cobre o período inteiro.
- **Mudança no meio do período:** se a vigência de uma regra começa ou termina dentro da competência, e não houver pró-rata definido, o motor **não decide**: devolve `NEEDS_REVIEW` com o motivo `MID_PERIOD_CHANGE`.
- **Sobreposição:** duas regras de mesmo tipo e cláusula, com valores diferentes e vigências sobrepostas, sem vínculo explícito de substituição, são **conflito** (`NEEDS_REVIEW`). O motor nunca escolhe "a mais recente" em silêncio.
- **Competência inferida** (ex.: pela data de emissão, quando o documento não informa) é marcada como inferida e gera `NEEDS_REVIEW`.

## 6. Resultados Controlados e Abstenção Seletiva

O código nunca "chuta" uma regra. A regra de ouro: **incerteza de domínio é resultado, não exceção.**

- **Incerteza de domínio** (conflito de datas, regra sem ancoragem, regra não revisada, documento ilegível): a função **retorna** um resultado tipado com `status` e `reasons`. Não levante exceção. O `ruff` bloqueia (`S110`, `BLE001`).
- **Erro de programação ou uso indevido** (`float` passado ao motor, argumento inválido): levante a exceção apropriada (`TypeError`, `ValueError`). Isso é um bug, não um caso de negócio.
- Nunca use `try: ... except Exception: pass` nem `except` genérico que engula o erro.
- **Ausência não é "nada mudou".** Se um documento não rendeu regras, o sistema devolve aviso explícito; nunca presume que o documento é irrelevante.
- **Só regra revisada por uma pessoa é confiável.** Uma divergência só pode ser classificada como `DIVERGENCE` se todas as regras usadas estão `CONFIRMED` ou `CORRECTED`. Com regra pendente, o máximo é `NEEDS_REVIEW` com a observação "possível divergência".

## 7. Docstrings Mínimas e Funcionais

Toda classe ou função não trivial em `backend/motor` e `backend/agentes` tem docstring de 1 a 2 linhas: o que entra e o que sai. Se houver regra de negócio específica, mencione.

```python
def select_applicable_rule(
    rules: list[ContractRule], reference_period: ReferencePeriod
) -> RuleSelection:
    """
    Recebe as regras de um tipo e a competência da fatura; devolve a regra vigente.
    Em conflito ou mudança no meio do período, devolve status NEEDS_REVIEW (não levanta).
    """
```

## 8. Organização de Prompts de IA

- Prompts ficam em `prompts.py` (somente constantes de texto) ou em arquivos `.txt`; nunca como f-strings longas no meio da lógica.
- **Prompts em português; código em inglês.**
- Cada prompt tem uma constante de versão (`LEGAL_AGENT_PROMPT_VERSION`). A versão usada é gravada junto de cada extração (trilha de auditoria).
- Não coloque dados reais de contratos nos prompts versionados. Exemplos de few-shot devem ser sintéticos.

## 9. Formatação, Lint e Imports

O `ruff` faz lint **e** formatação (substitui Black e isort).

- Linha máxima de 100 caracteres. Aspas duplas.
- Ordem de imports: (1) biblioteca padrão, (2) terceiros (`pydantic`, `streamlit`, `langgraph`), (3) módulos do projeto (`from backend.schemas import ContractRule`).

Configuração mínima sugerida (`pyproject.toml`):

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "ANN", "B", "UP", "BLE", "S110"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["ANN"]

[tool.ruff.format]
quote-style = "double"
```

Comandos de verificação: `uv run ruff check .` e `uv run ruff format --check .`.

## 10. Vocabulário Canônico de Estados

Todos os módulos usam exatamente estes nomes (enums em inglês; a interface os traduz para o português).

**Classificação da cobrança** (`AuditClassification`), do mais grave ao menos grave:

| Valor | Exibido ao usuário |
|---|---|
| `MISSING_DOCUMENTATION` | pendência documental |
| `DIVERGENCE` | divergência identificada |
| `NEEDS_REVIEW` | revisão humana necessária |
| `NO_DIVERGENCE` | sem divergência identificada |

**Estado da regra** (`ReviewStatus`): `PENDING_REVIEW`, `CONFIRMED`, `CORRECTED`, `REJECTED`, `INCONCLUSIVE`. Só `CONFIRMED` e `CORRECTED` são confiáveis.

**Estado do documento:** `reviewed: bool`. Um aditivo não revisado torna a consolidação das regras **incompleta** (`NEEDS_REVIEW`).

**Relação da regra com o aditivo** (`RuleRelation`): `CREATED`, `ALTERED`, `REVOKED`. "Substituída" não existe como valor: a substituição de uma condição por outra é registrada como `ALTERED`. **(decisão; ver [decisoes.md](decisoes.md))**

**Competência:** guardada como mês (`AAAA-MM`), não como data. **Tolerância de comparação:** parâmetro explícito de cada contrato, nunca constante no código.
