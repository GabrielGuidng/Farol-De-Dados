# Documentos Sintéticos Multi-Situação

Arquivos gerados por [`tests/fixtures/synthetic_files.py`](../../tests/fixtures/synthetic_files.py) exclusivamente com dados fictícios para testes integrados ponta a ponta e demonstração na interface.

> Nenhum documento real existe neste diretório. Para regenerar todos os arquivos: `uv run python -m tests.fixtures.synthetic_files`.

## Família 1 — Contrato `CT-2026-001` (Fornecedor Exemplo Ltda., CNPJ `11.222.333/0001-81`)

| Arquivo | Formato | Situação coberta |
|---|---|---|
| `ct_2026_001_contrato_base.pdf` | PDF (3 págs.) | Contrato base (`R$ 10.000,00`, franquia `40 h`, excedente `R$ 150,00/h`, reajuste IPCA) + CPF/RG para mascaramento (`A-10`). |
| `ct_2026_001_aditivo_01.docx` | DOCX | Aditivo sem página ancorado por cláusula/parágrafo (`S-09`), elevando o valor de `R$ 10.000,00` para `R$ 10.800,00` (`01/05/2026`). |
| `ct_2026_001_aditivo_02.pdf` | PDF (2 págs.) | Renegociação reduzindo o valor de `R$ 10.800,00` para `R$ 10.500,00` a partir de `01/09/2026` (Caso A). |
| `nfe_101_caso_b_regular.xml` | NF-e XML | Caso B regular (`2026-04`, `R$ 10.000,00`, `NO_DIVERGENCE`). |
| `nfe_104_caso_a_divergencia.xml` | NF-e XML | Caso A divergência (`2026-10`, `R$ 10.800,00` contra `R$ 10.500,00`, descrição alternativa para o Tradutor). |
| `nfse_109_competencia_inferida.xml` | NFS-e ABRASF XML | Sem `<Competencia>`: competência inferida da emissão (`M-14` → `NEEDS_REVIEW`). |
| `cobrancas_lote_ct_2026_001.csv` | CSV (`;`) | Lote em formato brasileiro: `103` (excedente divergente `M-04`), `105`/`106` (duplicidade `M-12`), `107` (fora da vigência `M-08`), `108` (excedente sem medição `M-04`). |
| `fatura_110_pdf_item_ambiguo.pdf` | PDF | Fatura em PDF para o Agente Fiscal com item ambíguo (`Serviço adicional de plantão`, `M-11` / `T-02`). |

## Família 2 — Armadilhas e Borda (`CT-2026-002` e avulsos)

| Arquivo | Formato | Situação coberta |
|---|---|---|
| `ct_2026_002_contrato_armadilhas.docx` | DOCX | Contrato `CT-2026-002` (`R$ 5.000,00`) com cláusula contendo tentativa de prompt injection (`A-08`). |
| `ct_2026_002_aditivo_meio_periodo.pdf` | PDF (1 pág.) | Mudança para `R$ 5.500,00` em `15/04/2026` (meio de período `M-09` e aditivo pendente `M-17`). |
| `nfe_201_meio_periodo.xml` | NF-e XML | Cobrança de `2026-04` afetada por mudança no meio da competência (`MID_PERIOD_CHANGE`, `M-09`). |
| `nfe_202_sem_contrato.xml` | NF-e XML | Cobrança sem vínculo contratual (`MISSING_DOCUMENTATION`, `M-15`). |
| `contrato_escaneado_sem_texto.pdf` | PDF (2 págs.) | PDF sem camada de texto (`ReadQuality.NO_TEXT`, `A-07`). |
