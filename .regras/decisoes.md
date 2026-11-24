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
| Provedor de modelo | **Em aberto** | Verificar retenção e uso para treinamento antes de qualquer dado real. Configurável por `LLM_MODEL`, atrás de `LLMClient`. |
| Comunicação Streamlit e backend | **Em aberto** | Via API FastAPI ou chamando o núcleo diretamente. |
| Índices de reajuste | Fora da primeira versão **(decisão)** | O sistema apenas sinaliza reajuste possivelmente devido. |
| Mascaramento de CPF/RG | No protótipo **(decisão)** | Minimização de dados antes de envio a serviço externo. |
| Isolamento entre empresas e perfis de acesso | Fora da primeira versão **(decisão)** | O protótipo opera com usuário e empresa simulados. |
| Relação da regra com o aditivo | `CREATED`, `ALTERED`, `REVOKED` **(decisão)** | "Substituída" foi incorporada a `ALTERED`. |
| Estado `REJECTED` da regra | Adotada **(decisão)** | A revisão humana precisa poder rejeitar uma regra extraída. |
| Competência fora da vigência | `NEEDS_REVIEW`, não `DIVERGENCE` **(decisão)** | Sem os documentos, não dá para distinguir cobrança indevida de prorrogação não enviada. Muda a cobrança 107 do cenário de desenvolvimento. |
| Mudança de regra no meio da competência | `NEEDS_REVIEW` sem pró-rata **(decisão)** | Pró-rata fica fora da primeira versão. |
| Limiar de 85% | Apenas para sinalizar e priorizar **(decisão)** | Nenhuma regra do Jurista é aceita automaticamente; o limiar também escalona vínculos do Tradutor. |

## Dependências previstas (adicionar quando o passo chegar)

| Dependência | Passo do roadmap | Observação |
|---|---|---|
| `pdfplumber` e `python-docx` | 6 (leitura — **adicionadas**) | Leitura de PDF por página e DOCX por cláusula/parágrafo. |
| `defusedxml` e `openpyxl` | 6 (leitura — **adicionadas**) | Leitura segura de XML de NF-e/NFS-e e planilhas `.xlsx`. |
| SDK do provedor de modelo | 7 (agentes) | Depois da decisão do provedor. |
| LibreOffice (sistema, não Python) | Descartado **(decisão)** | Não é necessário com ancoragem direta por cláusula e parágrafo em DOCX. |

