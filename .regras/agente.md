# Regras para Agentes de IA e Modelos de Linguagem

Define as diretrizes para os componentes do fluxo (Jurista, Consolidador Temporal, Fiscal, Tradutor e Motor Investigador) e as regras para o uso de assistentes de código durante o desenvolvimento.

Documentos relacionados: [style.md](style.md), [commits.md](commits.md), [testes.md](testes.md), [decisoes.md](decisoes.md).

## 1. Princípios de IA Responsável

Os modelos de linguagem atuam **somente** na camada semântica e de extração. Tudo que envolve dinheiro, datas e decisão é código determinístico em `backend/motor`.

### 1.1 Separação de papéis (proibição matemática e temporal)

O modelo nunca calcula dinheiro, diferenças ou percentuais, nunca decide qual regra está vigente e nunca conclui que "nada mudou". Aritmética e vigência são responsabilidade exclusiva do motor (`Decimal`, ver style.md).

### 1.2 Ancoragem documental com verificação por código

Toda regra extraída carrega: documento de origem, página (ou cláusula e parágrafo, em DOCX sem conversão para PDF), cláusula (quando houver) e **trecho literal**. Ancoragem declarada pelo modelo não basta; o código verifica:

1. o trecho literal existe no texto do documento (comparação após normalizar espaços e aspas);
2. o valor (ou quantidade, índice) extraído aparece dentro do trecho;
3. em aditivos, o valor extraído não é o valor anterior (ver 1.3).

Se qualquer verificação falhar, a regra recebe `INCONCLUSIVE` e vai para revisão humana. Nunca é corrigida ou preenchida automaticamente.

### 1.3 Valor novo e valor anterior

Aditivos frequentemente citam o valor antigo ("passa de R$ X para R$ Y", "antes de R$ X"). O esquema de saída tem campos separados `new_value` e `previous_value`. O código sinaliza como inconclusiva a extração em que os dois são iguais ou em que o texto indica que o valor extraído é o anterior.

### 1.4 Abstenção seletiva (human-in-the-loop)

O agente nunca inventa nem "chuta". A extração fica `PENDING_REVIEW` ou `INCONCLUSIVE` quando houver:

- texto ilegível ou PDF sem camada de texto (OCR ausente ou de baixa qualidade);
- redação ambígua ou data de início não explícita (use `null`, não presuma);
- datas ou valores conflitantes entre aditivos;
- trecho não encontrado, valor fora do trecho ou valor anterior capturado (1.2 e 1.3);
- saída que não passa na validação de esquema.

**Ausência de extração não significa "nada mudou".** Se um documento não rende regras, o sistema emite aviso explícito e o documento só conta como consolidado depois de marcado como revisado por uma pessoa.

### 1.5 Confiança não é autoavaliação do modelo

Não use a "confiança" informada pelo próprio modelo como critério de decisão: ela não é calibrada. A pontuação do sistema é a de **verificações por código** (1.2 e 1.3) e serve apenas para ordenar a fila de revisão. **Nenhuma regra extraída é confiável sem revisão humana**; não existe auto-aceite por limite de pontuação.

**Limiar de 85%:** serve para **sinalizar baixa confiança e priorizar a fila** de revisão, e para **escalonar vínculos do Tradutor** (vínculo abaixo do limiar vai para revisão). Ele **nunca** aceita uma regra do Jurista automaticamente. O valor é configurável, não uma constante escondida.

### 1.6 Validação rígida de saída

Nenhum dado do agente chega ao banco sem passar por um esquema Pydantic em `backend/schemas`. A saída do modelo é obtida de forma estruturada (esquema de ferramenta/JSON Schema), não por análise de texto livre. Em saída inválida: no máximo `MAX_RETRY_ATTEMPTS` novas tentativas; persistindo, a extração é descartada **com aviso visível** (nunca em silêncio) e o documento fica sem revisão concluída.

### 1.7 O documento é dado, não instrução (injeção de prompt)

Contratos e faturas vêm de terceiros e podem conter frases do tipo "ignore as instruções anteriores". Portanto:

- o texto do documento entra no prompt dentro de delimitadores explícitos, com a instrução de sistema de que o conteúdo é dado a analisar;
- o agente de extração não tem ferramentas com efeito colateral (não grava, não envia, não consulta a rede);
- a defesa final é a verificação por código (1.2): uma extração induzida por texto malicioso não passa se o trecho e o valor não conferirem, e nada vira regra confiável sem revisão humana.

### 1.8 Idioma e organização de prompts

- Código e variáveis em inglês; prompts em português (terminologia jurídica brasileira).
- Prompts separados do código (`prompts.py` ou `.txt`), com constante de versão gravada junto de cada extração.
- Exemplos nos prompts devem ser sintéticos.

## 2. Componentes do Fluxo e Contratos de Interface

O fluxo é um grafo de estados com pausas para revisão humana. Cada agente é uma **função tipada** `entrada Pydantic -> saída Pydantic`, testável sem LangGraph e com um cliente de modelo falso. O LangGraph é a implementação de referência do grafo e apenas conecta as funções; a lógica não depende dele.

| Componente | Tipo | Entrada | Saída | Pode decidir? |
|---|---|---|---|---|
| **Agente Jurista** | LLM com validação estrita | Texto paginado de contrato e aditivos | Regras candidatas com página e trecho (`PENDING_REVIEW` ou `INCONCLUSIVE`) | Não. Só extrai. |
| **Consolidador Temporal** | Código determinístico | Regras confirmadas e datas de efeito | Regras vigentes por período | Sim, por regras fixas (nunca por IA). |
| **Agente Fiscal** | Parser determinístico (XML, planilhas) e LLM para PDF | Nota, fatura ou medição | Itens cobrados, competência e valores | Não. Só extrai. |
| **Agente Tradutor** | LLM de equivalência semântica | Itens da cobrança e regras vigentes | Vínculo item-regra com o trecho que o justifica e grau de confiança | Não. Propõe vínculos; o motor calcula. |
| **Motor Investigador** | Python com `Decimal` | Pares item-regra | Memória de cálculo, diferença exata e classificação | Sim, por regras fixas. |

O cliente do modelo fica atrás de uma interface (`LLMClient`, um `Protocol`) para permitir testes sem rede, troca de modelo e uso de modelo local.

### 2.1 Agente Fiscal

- **XML (NF-e) e planilhas são lidos por parser determinístico**, nunca por modelo de linguagem. XML é lido de forma segura (`defusedxml`).
- O modelo de linguagem é usado **somente para PDF** sem estrutura, e a saída passa pelas mesmas regras de esquema e ancoragem (1.2 e 1.6).

### 2.2 Agente Tradutor (equivalência semântica)

O Tradutor **não explica resultados**: ele liga cada item da cobrança à regra vigente correspondente.

- Todo vínculo item-regra **exige o trecho** (da regra e do item) que o justifica; vínculo sem trecho é inválido no esquema.
- Vínculo **abaixo do limiar** de similaridade, ambíguo ou com **dois itens candidatos para a mesma regra** vai para `NEEDS_REVIEW`, nunca para cálculo.
- O Tradutor **não calcula** nada: não soma, não compara valores e não classifica.

### 2.3 Relatório e texto explicativo

O relatório é montado pelo código a partir do resultado do motor. Quando um modelo redige texto explicativo, **todo número citado precisa constar no resultado do motor**, o que é verificado por código; texto com número que não consta é rejeitado.

## 3. Registro e Rastreabilidade

- Gravar, para cada extração: versão do prompt, identificador do modelo, documento e hash do arquivo, data e hora.
- Toda decisão humana (confirmar, corrigir, rejeitar) grava estado anterior, estado posterior, usuário e justificativa.
- O registro de decisões é de acréscimo por convenção. Não o chame de "imutável" nem "à prova de adulteração" se estiver em um banco editável.

## 4. Privacidade e Dados (LGPD)

- Usar um modelo hospedado externamente significa **enviar o texto do contrato a um terceiro**, possivelmente fora do Brasil (transferência internacional de dados). Isso exige base legal e cuidado; rodar localmente no navegador ou na máquina não equivale a "conformidade com a LGPD".
- Em desenvolvimento e demonstração: **somente documentos públicos, sintéticos ou anonimizados**.
- Nunca versionar contratos reais, chaves de API, `.env` nem bancos de dados (ver commits.md).
- Chaves de API em variáveis de ambiente (`UPPER_CASE`), nunca no código.
- Logs de aplicação não devem conter o texto integral dos documentos nem dados pessoais.
- **Mascaramento antes do envio:** CPFs e RGs são mascarados por padrões de texto antes de qualquer envio a serviço externo. Assinaturas físicas dependem de tratamento de imagem e ficam fora da primeira versão. **(decisão; ver [decisoes.md](decisoes.md))**
- **Integridade no recebimento:** cada arquivo recebido gera um resumo criptográfico (hash), gravado junto do documento; arquivo alterado gera hash diferente.

## 5. Avaliação dos Agentes

A qualidade do agente é medida em conjuntos de avaliação em `evaluation/`, fora da suíte `pytest` padrão (a suíte nunca chama a API real).

- **Métrica principal: erro silencioso**, isto é, valor errado que seguiria para a revisão sem nenhum alerta. Cada ocorrência é analisada caso a caso.
- Métricas secundárias: acertos, omissões, itens sinalizados como inconclusivos, taxa de encaminhamento para revisão.
- Mantenha um conjunto de desenvolvimento (para ajustar prompts e regras) e um conjunto **separado de validação**, que não é usado para ajustes. Os casos de validação são escritos e anotados à mão antes de rodar o sistema.
- **Toda mudança de prompt ou de modelo** exige nova rodada de avaliação; o resultado vai no corpo do commit (ver commits.md).
- Compare sempre com uma linha de base simples (ex.: extração por regex) para saber se o modelo realmente melhora.

## 6. Uso de Assistentes de Código (para a equipe)

Quem usar um assistente (Copilot, ChatGPT, Gemini, Claude etc.) deve instruí-lo a obedecer a estas regras **antes** de gerar código:

- **Conformidade:** respeitar [style.md](style.md), [commits.md](commits.md) e [testes.md](testes.md).
- **Tipagem inegociável:** nenhuma função, método ou rota FastAPI sem tipos de entrada e saída.
- **Dinheiro e datas:** `Decimal` a partir de string, `date`, nunca `float` nem `datetime` no motor.
- **Não alterar testes para "fazer passar".** Se um teste falha, o assistente explica se o erro está no código ou na expectativa, e a decisão é da equipe.
- **Mudanças pequenas:** uma mudança lógica por vez, para respeitar "um commit = uma mudança".
- **Antes do commit**, rodar: `uv run ruff check .`, `uv run ruff format --check .` e `uv run pytest -v`.
- **Revisar o que foi gerado.** Código de assistente é rascunho: quem faz o commit responde por ele.

Texto-base para colar no início da conversa com o assistente:

```text
Siga estritamente as regras do projeto Farol de Dados: tipagem completa (sem Any);
nomes de código em inglês, mensagens ao usuario em portugues; dinheiro sempre Decimal
criado a partir de string (nunca float); datas com datetime.date; nunca falhe em silencio
(sem except generico). Incerteza de dominio vira resultado com status NEEDS_REVIEW,
nao excecao. Nao altere testes para faze-los passar. Uma mudanca logica por vez.
```
