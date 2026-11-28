# Registro de Decisões

Registro curto das decisões técnicas e de escopo do Farol de Dados. Cada linha diz o que foi decidido, em que situação está e por quê.

Documentos relacionados: [style.md](style.md), [agente.md](agente.md), [testes.md](testes.md), [commits.md](commits.md).

Itens marcados **(decisão)** foram propostos e **precisam da confirmação da equipe**. Se a equipe discordar, reverta o trecho correspondente nos arquivos de `.regras/` e registre aqui a nova decisão.

Situações possíveis: **Adotada**, **Prevista**, **Em aberto**, **Etapa posterior**, **Fora da primeira versão**.

| Decisão | Situação | Justificativa |
|---|---|---|
| Orquestração com LangGraph | Adotada, como implementação de referência | O fluxo tem ramificação real e pausas para revisão humana; as funções dos nós não dependem da biblioteca. |
| FastAPI | Prevista, após motor e schemas | Só se justifica ao desacoplar serviços; até lá o Streamlit pode chamar o núcleo. |
| Persistência | SQLite no protótipo; PostgreSQL (JSONB) como evolução **(decisão)** | O protótipo pode ser simples e local. SQLite já vem com o Python. |
| Leitura de PDF | `pdfplumber` **(decisão adotada)** | Licença MIT (sem risco AGPL do PyMuPDF); extração de texto página a página. |
| DOCX | Ancorar por cláusula e parágrafo **(decisão adotada)** | Dispensa conversão para PDF e LibreOffice; evidência marcada com `without_page=True`. |
| OCR | Etapa posterior **(decisão)** | Até lá, documentos sem camada de texto são sinalizados e não processados. |
| Provedor de modelo | Google Gemini (AI Studio gratuito) no MVP; Anthropic prevista para evolução **(decisão adotada)** | No MVP e na apresentação, usa-se a API gratuita do Gemini com rotação de até 3 chaves (`LLM_API_KEY`, `LLM_API_KEY_2`, `LLM_API_KEY_3`) como contingência contra falhas/limites de cota, enviando apenas dados sintéticos/públicos com CPF/RG mascarados (o plano gratuito pode reter dados). Para dados reais/produção, migrar para API paga sem treinamento ou API da Anthropic atrás de `LLMClient`. |
| Comunicação Streamlit e backend | **Chamada direta a `backend/api/services.py` no protótipo** | Interface Streamlit importa casos de uso diretamente no MVP para manter fluxo simples e rastreável; API FastAPI em `main.py` permanece opcional para exposição externa futura. |
| Índices de reajuste | Fora da primeira versão **(decisão)** | O sistema apenas sinaliza reajuste possivelmente devido. |
| Mascaramento de CPF/RG | No protótipo **(decisão)** | Minimização de dados antes de envio a serviço externo. |
| Isolamento entre empresas e perfis de acesso | Fora da primeira versão **(decisão)** | O protótipo opera com usuário e empresa simulados. |
| Relação da regra com o aditivo | `CREATED`, `ALTERED`, `REVOKED` **(decisão)** | "Substituída" foi incorporada a `ALTERED`. |
| Estado `REJECTED` da regra | Adotada **(decisão)** | A revisão humana precisa poder rejeitar uma regra extraída. |
| Competência fora da vigência | `NEEDS_REVIEW`, não `DIVERGENCE` **(decisão)** | Sem os documentos, não dá para distinguir cobrança indevida de prorrogação não enviada. Muda a cobrança 107 do cenário de desenvolvimento. |
| Mudança de regra no meio da competência | `NEEDS_REVIEW` sem pró-rata **(decisão)** | Pró-rata fica fora da primeira versão. |
| Limiar de 85% | Auto-aprovação para regras > 85% com revisão reversível **(decisão)** | Regras extraídas do contrato com confiança acima de 85% (`> 0.85`) são confirmadas automaticamente na ingestão (`sistema.auto` na trilha `reviews`), com aviso e possibilidade de alteração posterior (`Corrigir`/`Rejeitar`); para o Tradutor, o limiar continua escalonando vínculos para revisão humana. |
| Preços por item / posto / Ata (`ITEM_PRICE`) | Adotada **(decisão)** | `ContractRule` suporta `RuleType.ITEM_PRICE` com `item_name` e `quantity_scope` (`MONTHLY` para postos mensais recorrentes, `TOTAL` para teto acumulado global de Ata de Registro de Preços). |
| DANFEs de mercadoria (`competence_inferred` e `issue_date`) | Adotada **(decisão)** | DANFEs identificadas por fornecimento/AF derivam a competência da emissão sem travar `competence_inferred`; faturas emitidas após `effective_end` dentro do mês final são marcadas como `OUTSIDE_CONTRACT_VALIDITY`. |
| Trava de segurança e alinhamento Resumo × Apêndice | Adotada **(decisão)** | `MID_PERIOD_CHANGE` reporta gravidade `Média` com exposição `n/a`; se 100% das notas (`>= 3`) de um contrato divergirem no mesmo campo cadastral, o relatório colapsa em alerta único de leitura sem somar o bruto à exposição. |
| Leitura multimodal de PDF escaneado e transição de quantitativo | Adotada na demonstração **(decisão)** | Quando `pdfplumber` devolve `NO_TEXT` e `LLMClient` implementa `transcribe_pdf`, utiliza-se leitura multimodal página a página; em aditivos de acréscimo/supressão de postos (`de X para Y`), `anchoring.py` e `timeline.py` garantem que o novo total seja preservado e herdado em repactuações. |
| Gráfico em linha da linha do tempo (`Painel` e `Vigência`) | Adotada **(decisão)** | `Painel e Relatório` compara `Valor vigente × Valor cobrado` em gráfico de linha com marcadores de divergência; `Regras e Vigência` exibe evolução em degrau (`step-after`) da vigência mensal e marcos de aditivos. |

## Dependências previstas (adicionar quando o passo chegar)

| Dependência | Passo do roadmap | Observação |
|---|---|---|
| `pdfplumber` e `python-docx` | 6 (leitura — **adicionadas**) | Leitura de PDF por página e DOCX por cláusula/parágrafo. |
| `defusedxml` e `openpyxl` | 6 (leitura — **adicionadas**) | Leitura segura de XML de NF-e/NFS-e e planilhas `.xlsx`. |
| `google-genai` (SDK do provedor) | 7 (agentes — **adicionada**) | MVP com Google Gemini (AI Studio) atrás de `LLMClient`; permite migração futura para Anthropic. |
| LibreOffice (sistema, não Python) | Descartado **(decisão)** | Não é necessário com ancoragem direta por cláusula e parágrafo em DOCX. |

