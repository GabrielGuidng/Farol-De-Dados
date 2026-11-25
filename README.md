# Farol de Dados

> Apoio à auditoria de cobranças contratuais, corporativas e governamentais, com motor determinístico e agentes de IA especializados.

O **Farol de Dados** compara faturas e notas fiscais com as regras contratuais vigentes em cada competência (contrato-base e termos aditivos sucessivos). Ele aponta indícios de divergência, mostra a evidência documental de cada regra usada e deixa a decisão final para uma pessoa. Seus pilares são **cálculo exato**, **rastreabilidade documental** e **abstenção seletiva**: quando não consegue provar algo, o sistema não afirma.

Este README resume o projeto; as regras detalhadas de estilo, agentes, testes, commits e o registro de decisões ficam em [`.regras/`](.regras/).

---

## 🧭 Exemplo em 30 segundos

Contrato com preço mensal de R$ 10.000,00. O Aditivo 01 muda para R$ 10.800,00 a partir de maio/2026. O Aditivo 02 muda para R$ 10.500,00 a partir de setembro/2026. (Casos A e B, os cenários de demonstração do projeto.)

| Cobrança | Valor cobrado | Regra vigente na competência | Resultado |
|---|---|---|---|
| Abril/2026 (Caso B) | R$ 10.000,00 | R$ 10.000,00 (contrato original) | `NO_DIVERGENCE` |
| Outubro/2026 (Caso A) | R$ 10.800,00 | R$ 10.500,00 (Aditivo 02) | `DIVERGENCE`: R$ 300,00 a mais (2,86%), com documento, página e trecho do Aditivo 02 |

O erro que o sistema evita: aplicar o aditivo errado a uma competência (usar o mais recente em abril, ou esquecer o mais novo em outubro).

---

## 💡 Princípios Fundamentais

1. **Separação rígida entre IA e motor determinístico**
   - Modelos de linguagem atuam **apenas** na leitura, na extração estruturada e na equivalência semântica entre itens cobrados e cláusulas.
   - **O modelo nunca calcula dinheiro nem decide vigência.** Aritmética, datas e classificação são feitas pelo motor em Python com `Decimal`, sem `float`.
2. **Ancoragem documental com verificação por código**
   - Toda regra extraída traz documento de origem, página (ou cláusula e parágrafo, em DOCX sem conversão) e **trecho literal**. O código confere se o trecho existe no documento e se o valor extraído consta nele.
   - Em aditivos, o código sinaliza quando o valor extraído parece ser o valor **anterior** ("passa de X para Y").
3. **Abstenção seletiva e human-in-the-loop**
   - Ambiguidade, conflito entre aditivos, mudança de regra no meio do período ou leitura duvidosa viram revisão humana, nunca suposição.
   - **Nenhuma regra extraída por IA é tratada como confiável sem confirmação de uma pessoa.** O limiar de confiança (inicialmente 85%) serve para sinalizar e priorizar, não para aceitar regras automaticamente.
   - Documento sem regras extraídas gera aviso: o sistema não presume que "nada mudou". Aditivos precisam ser marcados como revisados.
4. **Resultados controlados**, em dois vocabulários distintos:

**Classificação da cobrança** (da mais grave para a menos grave):

| Valor | Exibido ao usuário | Significado |
|---|---|---|
| `MISSING_DOCUMENTATION` | pendência documental | Falta documento essencial ou vínculo seguro com o contrato; não dá para auditar. |
| `DIVERGENCE` | divergência identificada | Diferença entre o cobrado e o previsto em **regras já confirmadas por uma pessoa**. |
| `NEEDS_REVIEW` | revisão humana necessária | Há incerteza que exige análise humana (motivos abaixo). |
| `NO_DIVERGENCE` | sem divergência identificada | Nenhuma divergência encontrada **nas verificações realizadas**. Não é atestado de conformidade. |

Motivos comuns de `NEEDS_REVIEW`: regra ainda não revisada, aditivo não revisado, conflito de vigência, mudança de regra no meio da competência, competência fora da vigência registrada (a prorrogação pode existir e não ter sido enviada), competência inferida pela data de emissão, indício de duplicidade, itens da cobrança ambíguos, vínculo item-regra ambíguo e reajuste possivelmente devido.

**Estado de uma regra:** `PENDING_REVIEW`, `CONFIRMED`, `CORRECTED`, `REJECTED` e `INCONCLUSIVE` (a verificação por código não conseguiu comprová-la). Só `CONFIRMED` e `CORRECTED` sustentam uma `DIVERGENCE`.

**Relação de uma regra com o aditivo:** criada, alterada (inclui a substituição de uma condição por outra) ou revogada.

A classificação principal é a mais grave entre as verificações, e **todos** os alertas continuam visíveis.

---

## 🤖 Componentes do Fluxo

O fluxo é um grafo de estados com pausas para revisão humana. Os agentes são funções tipadas (`Pydantic -> Pydantic`), testáveis sem o LangGraph, que apenas as conecta.

| Componente | Tipo | Entrada | Saída | Pode decidir? |
|---|---|---|---|---|
| **Agente Jurista** | LLM com validação estrita | Texto paginado de contrato e aditivos | Regras candidatas com página e trecho (`PENDING_REVIEW` ou `INCONCLUSIVE`) | Não. Só extrai. |
| **Consolidador Temporal** | Código determinístico | Regras confirmadas e datas de efeito | Regras vigentes por período | Sim, por regras fixas (nunca por IA). |
| **Agente Fiscal** | Parser determinístico (XML, planilhas) e LLM para PDF | Nota, fatura ou medição | Itens cobrados, competência e valores | Não. Só extrai. |
| **Agente Tradutor** | LLM de equivalência semântica | Itens da cobrança e regras vigentes | Vínculo item-regra com o trecho que o justifica e grau de confiança; vínculo ambíguo vai para revisão | Não. Propõe vínculos; o motor calcula. |
| **Motor Investigador** | Python com `Decimal` | Pares item-regra | Memória de cálculo, diferença exata e classificação | Sim, por regras fixas. |

O relatório é montado pelo código a partir do resultado do motor. Quando um modelo redige texto explicativo, todo número citado precisa constar no resultado do motor, o que é conferido por código.

---

## 🛠️ Stack Tecnológica

- **Linguagem:** Python 3.12+
- **Ambiente e pacotes:** [uv](https://docs.astral.sh/uv/)
- **Core determinístico:** Python com `Decimal`
- **Validação e contratos:** [Pydantic v2](https://docs.pydantic.dev/) (`extra="forbid"`, `frozen=True`)
- **Orquestração:** [LangGraph](https://github.com/langchain-ai/langgraph) como implementação de referência do grafo de estados e [LangChain Core](https://github.com/langchain-ai/langchain)
- **Persistência:** SQLite no protótipo local; PostgreSQL (JSONB) como evolução
- **API:** [FastAPI](https://fastapi.tiangolo.com/) e [Uvicorn](https://www.uvicorn.org/), previstos após o motor e os schemas; até lá, o Streamlit pode chamar o núcleo diretamente
- **Interface:** [Streamlit](https://streamlit.io/)
- **Qualidade:** [pytest](https://docs.pytest.org/) e [Ruff](https://docs.astral.sh/ruff/) (lint e formatação, com tipagem obrigatória)

### Decisões em aberto

Itens ainda não fixados. Registre cada decisão em [`.regras/decisoes.md`](.regras/decisoes.md) quando for tomada.

| Tema | Situação |
|---|---|
| Leitura de PDF | Adotada: `pdfplumber` (licença MIT). |
| DOCX | Adotada: ancoragem por cláusula e parágrafo, sem conversão para PDF. |
| OCR para PDFs escaneados | Etapa posterior. Até lá, documentos sem camada de texto são sinalizados e não processados. |
| Provedor e modelo de linguagem | Adotado no MVP: Google Gemini (AI Studio) atrás de `LLMClient`, com fallback de até 3 chaves de API; evolução prevista para API da Anthropic. |
| Comunicação Streamlit e backend | A definir: via API FastAPI ou chamando o núcleo diretamente. |

---

## 📂 Estrutura do Projeto

Pastas de negócio em português; código interno em inglês.

```text
farol-de-dados/
├── backend/
│   ├── motor/         # Núcleo determinístico: vigência, cálculo, classificação (sem IA)
│   ├── agentes/       # Agentes de IA, verificação de ancoragem e prompts versionados
│   ├── schemas/       # Modelos Pydantic estritos
│   ├── persistencia/  # Repositório (SQLite) e registro de decisões em modo de acréscimo
│   ├── leitura/       # Leitura de PDF, DOCX, XML (NF-e/NFS-e), planilhas e mascaramento
│   └── api/           # Endpoints FastAPI
├── frontend/          # Interface em Streamlit
├── tests/
│   ├── unit/
│   └── integration/
├── evaluation/        # Conjuntos de avaliação da IA (fora do pytest)
├── data/              # Somente documentos sintéticos ou públicos
├── .regras/           # style.md, agente.md, testes.md, commits.md, decisoes.md
├── pyproject.toml
├── .env.example       # Modelo de variáveis de ambiente (sem valores reais)
└── README.md
```

Regra de dependência: `motor` e `agentes` não se importam. O motor recebe dados já validados.

---

## ⚠️ Status e Limitações Conhecidas

**Status (versão 0.1.0, fase de fundação):** ambiente, ferramentas e diretrizes configurados. Schemas, motor, persistência, agentes, interface e API **ainda não foram implementados**. O plano detalhado está em [ESTADO_DO_PROJETO.md](ESTADO_DO_PROJETO.md).

Limitações assumidas no escopo do MVP:

- **Reajuste:** o sistema sinaliza quando um reajuste pode ser devido; não calcula índices oficiais.
- **Datas:** competência mensal; mudança de regra no meio do mês vai para revisão (sem pró-rata).
- **OCR:** PDFs sem camada de texto não são lidos enquanto não houver OCR.
- **NFS-e:** os layouts variam por município; cada formato precisa ser tratado e testado.
- **Controle de acesso:** o protótipo opera com usuário e empresa simulados; isolamento entre empresas e perfis de acesso ficam fora da primeira versão.
- **Registro de decisões:** é de acréscimo (a aplicação não edita nem apaga), mas não é à prova de adulteração em banco editável.
- **Duplicidade:** é um indício, não uma conclusão.
- **Validação da IA:** a extração precisa ser avaliada com contratos reais anotados à mão antes de qualquer afirmação sobre sua precisão (ver `evaluation/`).
- **Posicionamento de mercado:** a originalidade frente a outras plataformas é uma hipótese a validar, não um fato.

---

## 🚀 Como Executar

> Os comandos abaixo supõem o scaffold concluído; os módulos de aplicação ainda estão sendo implementados.

### 1. Pré-requisitos

- **Python 3.12+**
- **uv**
  ```bash
  # Linux/macOS
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # Windows (PowerShell); confira o comando atual na documentação do uv
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

### 2. Ambiente

```bash
uv sync
cp .env.example .env     # preencha localmente; o .env nunca é versionado
```

Exemplo de `.env.example`:

```text
LLM_MODEL=gemini-2.5-flash
LLM_API_KEY=
LLM_API_KEY_2=
LLM_API_KEY_3=
```

### 3. Qualidade de código

```bash
uv run ruff check .             # lint e tipagem obrigatória
uv run ruff format --check .    # verificação de formatação
uv run ruff format .            # formata automaticamente
```

### 4. Testes

A suíte padrão é rápida e **offline**: os agentes são testados com um cliente de modelo falso, sem chamadas de rede.

```bash
uv run pytest -v
```

Avaliações contra a API real ficam em `evaluation/` e em testes marcados com `@pytest.mark.llm`, fora da suíte padrão.

### 5. Aplicações (quando implementadas)

```bash
uv run streamlit run frontend/app.py
uv run uvicorn backend.api.main:app --reload
```

---

## 📋 Diretrizes de Desenvolvimento

- 📏 [Style Guide](.regras/style.md): tipagem, `Decimal`, datas, abstenção e vocabulário de estados.
- 🤖 [Regras para Agentes de IA](.regras/agente.md): ancoragem, verificação por código, injeção de prompt e avaliação.
- 🧪 [Regras de Testes](.regras/testes.md): cenários obrigatórios. Os Casos A e B são os cenários de demonstração; os demais são **cenários de desenvolvimento** definidos pela equipe e não substituem a avaliação independente.
- 📝 [Regras de Commit](.regras/commits.md): Conventional Commits em português e checklist pré-commit.
- 🗂️ [Registro de Decisões](.regras/decisoes.md): decisões técnicas e de escopo, e o que ainda está em aberto.

---

## 🔒 Privacidade e LGPD

- **Documentos enviados a um provedor de modelo de linguagem saem do seu ambiente**, possivelmente para fora do Brasil (transferência internacional de dados), e contratos contêm dados pessoais (nomes, CPFs, assinaturas). Por isso, em desenvolvimento e demonstração, **use somente documentos públicos, sintéticos ou anonimizados**.
- **Mascaramento:** CPFs e RGs são mascarados por padrões de texto antes do envio a serviço externo. Assinaturas físicas dependem de tratamento de imagem e ficam fora da primeira versão.
- **Integridade:** cada arquivo recebido gera um resumo criptográfico (hash) no recebimento.
- **Proibido versionar documentos reais.** A pasta `data/` aceita apenas amostras sintéticas ou públicas.
- **Segredos:** chaves de API ficam em variáveis de ambiente (`.env` ignorado pelo git), nunca no código.
- **Logs:** não devem conter o texto integral dos documentos nem dados pessoais.
- **Não cole contratos reais** em assistentes de código nem em chats de IA.
- Rodar localmente reduz riscos, mas **não equivale a conformidade com a LGPD**. O uso com dados reais exige análise de base legal, papéis de controlador e operador e verificação de retenção no provedor.
