# Guia de Front-End — Sistema de Apoio à Auditoria Contratual

> **Versão:** 2.0 · **Temas:** Escuro (padrão) e Claro
> **Status:** documento normativo do projeto. Toda tela, componente ou CSS novo deve seguir este guia.
> **Stack atual:** Streamlit (protótipo). As regras são escritas em *tokens* para sobreviverem a uma migração futura de framework.
> **Convenção:** **DEVE** = obrigatório · **NÃO DEVE** = proibido · **PODE** = opcional.

---

## 0. Como usar este guia

1. Nunca escreva um hexadecimal direto em código de tela. Use sempre um **token** (seção 3).
2. Todo componente **DEVE** funcionar nos dois temas. Se só funciona em um, está errado.
3. O **tema escuro é o padrão**; o claro é a alternativa (para impressão, projetor, ambientes muito iluminados).
4. Em dúvida entre dois estilos, escolha o mais sóbrio.
5. Antes de concluir qualquer tela, passe pelo **checklist da seção 13**.
6. Mudanças neste guia entram por commit próprio, com justificativa.

---

## 1. Origem Visual e Princípios

### 1.1. De onde vem a paleta

A paleta nasce da ilustração de referência do projeto: um **céu noturno quase preto**, cabelos em **azul profundo**, tecido em **branco-lavanda** e um **disco de ouro** abraçado junto ao peito, que remete ao **Disco de Ouro da Voyager**: um registro físico de informação feito para durar e ser lido no futuro. Essa ideia combina com o sistema: o disco vira o símbolo da **evidência rastreável**.

| Elemento da ilustração | Papel na interface |
| :--- | :--- |
| Céu noturno quase preto | Fundo do tema escuro e sidebar (nos dois temas) |
| Cinza-azulado das sombras | Superfícies elevadas, bordas, texto secundário |
| Azul profundo do cabelo | Ação primária, links, foco |
| Branco-lavanda do vestido | Texto no tema escuro; fundos do tema claro |
| **Disco de ouro (Voyager)** | **Ouro: evidência, vigência, rastreabilidade** |
| Brilho em estrela de 4 pontas (✦) | Único ícone-motivo: marca **conteúdo sugerido por IA** |

### 1.2. Princípios

- **Instrumento técnico, não vitrine.** Precisão, confiabilidade financeira, rigor jurídico. Nada lúdico.
- **Escuro como identidade, claro como alternativa.** O escuro espelha a ilustração; o claro garante conforto em leitura longa diurna e em impressão.
- **Cada elemento tem função.** Se não ajuda a analisar ou navegar, remova ou suavize.
- **Fluxo espelha o raciocínio do auditor:** *Documento Original → Regra Extraída → Vigência na Competência → Confronto com Fatura → Revisão Humana*.
- **Fronteira IA × cálculo é sempre visível** (seção 9).

---

## 2. Regra 60-30-10 (vale para os dois temas)

| Parcela | O que ocupa |
| :--- | :--- |
| **60%** | Neutros: canvas, cartões, respiro |
| **30%** | Estrutura: sidebar, texto, cabeçalhos de tabela, divisórias |
| **10%** | Ação e precisão: botão primário azul, filetes e selos em ouro |

---

## 3. Tokens de Cor

Os **nomes** dos tokens são idênticos nos dois temas; só os valores mudam.

### 3.1. Superfícies

| Token | 🌙 Escuro (padrão) | ☀️ Claro | Uso |
| :--- | :--- | :--- | :--- |
| `--bg-canvas` | `#0B0D12` | `#F7F8FC` | Fundo geral |
| `--bg-surface` | `#12151C` | `#FFFFFF` | Cartões, tabelas, formulários, visualizador de documento |
| `--bg-subtle` | `#1B1F2A` | `#EEF0F7` | Cabeçalho de tabela, zebra, filtros, hover leve |
| `--bg-sidebar` | `#07080B` | `#0D0F15` | Sidebar (sempre escura) |
| `--bg-sidebar-hover` | `#1B1F2A` | `#1B1F2A` | Item sob cursor ou ativo na sidebar |

> Hierarquia no escuro: `sidebar` (mais fundo) < `canvas` < `surface` < `subtle` (mais elevado). Elevação se faz **clareando**, nunca com sombra.

### 3.2. Texto

| Token | 🌙 Escuro | ☀️ Claro | Uso |
| :--- | :--- | :--- | :--- |
| `--text-primary` | `#EEF0F7` | `#141A40` | Títulos, valores, cláusulas |
| `--text-secondary` | `#A0A6BB` | `#404259` | Subtítulos, rótulos, metadados |
| `--text-muted` | `#7B819B` | `#6B6F94` | Placeholders, legendas (≈ 4,6:1) |
| `--text-on-dark` | `#EEF0F7` | `#EEF0F7` | Texto na sidebar |
| `--text-on-dark-muted` | `#A0A6BB` | `#A0A6BB` | Rótulos secundários na sidebar |

> **NÃO DEVE** usar `--text-muted` sobre `--bg-subtle` em nenhum dos temas: o contraste cai abaixo de 4,5:1.

### 3.3. Ação e foco (azul)

| Token | 🌙 Escuro | ☀️ Claro | Uso |
| :--- | :--- | :--- | :--- |
| `--primary` | `#23518C` | `#23518C` | Fundo do botão primário, seleção ativa |
| `--primary-hover` | `#2C65AE` | `#1B3F6E` | Hover (no escuro **clareia**, no claro **escurece**) |
| `--primary-pressed` | `#1B3F6E` | `#141A40` | Pressionado |
| `--on-primary` | `#FFFFFF` | `#FFFFFF` | Texto sobre o botão primário |
| `--link` | `#ABD2FA` | `#23518C` | Links e texto de destaque azul |
| `--focus-ring` | `#ABD2FA` a 40% | `#23518C` a 35% | Contorno de foco de teclado, linha selecionada |
| `--border-focus` | `#7FA8E0` | `#A2BDD2` | Borda de campo em foco |
| `--accent-blue` | `#1F64BF` | `#1F64BF` | Somente séries de gráfico e destaques pequenos |

> `#23518C` como **texto** sobre fundo escuro tem apenas ≈ 2,3:1. Por isso o texto azul no escuro usa `--link` (`#ABD2FA`), nunca `--primary`.

### 3.4. Ouro (precisão e rastreabilidade)

| Token | 🌙 Escuro | ☀️ Claro | Uso |
| :--- | :--- | :--- | :--- |
| `--gold` | `#C2A267` | `#A58F69` | Filetes, borda de evidência, aba ativa, nós da linha do tempo |
| `--gold-text` | `#D8BF8A` | `#735114` | Micro-rótulos, selos, títulos de evidência |
| `--gold-bg` | `#1C1812` | `#F9F6F0` | Fundo de evidência e de itens pendentes |
| `--gold-border` | `#4A3E26` | `#E2D8C5` | Contorno de caixas de evidência e badges |

No escuro o ouro fica mais luminoso, próximo do disco da ilustração; no claro permanece mais fosco para manter contraste de texto. A **função** é a mesma nos dois.

**Onde o ouro PODE aparecer:** filetes, marcadores de vigência, selos de versão (`CONTRATO-BASE`, `ADITIVO 01`), grifos de evidência, aba ativa, nós da timeline.

**Onde o ouro NÃO DEVE aparecer:** botões primários, fundos grandes, texto corrido, brilhos ou gradientes metálicos.

### 3.5. Bordas

| Token | 🌙 Escuro | ☀️ Claro | Uso |
| :--- | :--- | :--- | :--- |
| `--border` | `#2A2F40` | `#D8D9EA` | Borda padrão de cartões, tabelas, inputs |
| `--border-sidebar` | `#C2A267` a 30% | `#A58F69` a 35% | Divisória direita da sidebar |

### 3.6. Status de auditoria

Fórmula obrigatória: **fundo + borda + texto + ícone**. Cor nunca é o único sinal.

| Status | Ícone | 🌙 Fundo / Borda / Texto | ☀️ Fundo / Borda / Texto |
| :--- | :--- | :--- | :--- |
| **Conforme** | ✓ | `#0F2418` / `#1F5A38` / `#86EFAC` | `#ECFDF3` / `#BBF7D0` / `#14532D` |
| **Divergência** | ▲ | `#2A1214` / `#7F1D1D` / `#FCA5A5` | `#FEF2F2` / `#FECACA` / `#991B1B` |
| **Pendente / IA** | ✦ | `--gold-bg` / `--gold-border` / `--gold-text` | `--gold-bg` / `--gold-border` / `--gold-text` |
| **Informativo** | ● | `#0F1D33` / `#1E3A66` / `#ABD2FA` | `#EFF6FF` / `#BFDBFE` / `#23518C` |

> Verde e vermelho não existem na ilustração. Foram escolhidos à parte e dessaturados para não competir com a paleta principal.

### 3.7. Cores proibidas

Tons vibrantes, neon, roxos e rosados de outras referências: `#0033FF`, `#0600AB`, `#1B2CC1`, `#2038B6`, `#53159B`, `#977DFF`, `#FFCCF2`.

**Também proibido no escuro:** preto puro `#000000` e branco puro `#FFFFFF` como texto (cansa a vista); use `--bg-canvas` e `--text-primary`.

---

## 4. Gradientes, Sombras e Texturas

- **Dados são sempre cor sólida:** tabelas, botões, KPIs, badges, gráficos e caixas de evidência.
- **Gradientes permitidos (restritos à paleta noturna/azul-profundo):**
  - Sidebar vertical quase imperceptível: Escuro `#07080B` → `#0F1430`; Claro `#0D0F15` → `#141A40`.
  - Fundo principal (`.stApp`, token `--canvas-gradient`) vertical de cima para baixo (`180deg`), com o topo mais escuro e a base sendo a região mais clara, sem ouro em fundos grandes: Escuro `#0B0D12` → `#0F1430` → `#141A40` com brilho sutil `#23518C` (`rgba(35,81,140,.28)`); Claro `#EEF0F7` → `#F7F8FC` → `#FFFFFF` com nuance suave `#A2BDD2` (`rgba(162,189,210,.18)`).
- **NÃO DEVE** usar `box-shadow` difuso, `backdrop-filter`, vidro fosco, brilho ou "sparkle". O brilho da ilustração é referência de cor, não de efeito.
- Profundidade vem de **contraste entre superfícies** e **bordas finas**.

---

## 5. Geometria (igual nos dois temas)

### 5.1. Bordas

| Elemento | Regra |
| :--- | :--- |
| Padrão | `1px solid var(--border)` |
| Topo de KPI e base da aba ativa | `2px solid var(--gold)` |
| Borda esquerda de bloco de evidência | `3px solid var(--gold)` |
| Conteúdo de IA não validado | `1px dashed var(--gold)` |
| Após aprovação humana | A borda tracejada vira sólida |

O tracejado é **exclusivo** para conteúdo não validado.

### 5.2. Cantos

| Elemento | Raio |
| :--- | :--- |
| Botões, badges, inputs, caixas de evidência | `4px` |
| Cartões, KPIs, painéis, tabelas | `6px` |
| Máximo permitido | `8px` |

### 5.3. Espaçamento (múltiplos de 4px)

| Escala | Valor | Uso |
| :--- | :--- | :--- |
| Micro | `4px` / `8px` | Ícone-texto, rótulo-campo, linhas compactas |
| Padrão | `16px` | Padding de cartões, itens do mesmo grupo |
| Seção | `24px` / `32px` | Entre blocos (KPIs → tabela) |

---

## 6. Tipografia

### 6.1. Famílias

| Função | Fonte | Fallback |
| :--- | :--- | :--- |
| Interface e leitura | **IBM Plex Sans** | `-apple-system, "Segoe UI", sans-serif` |
| Dados e técnica | **IBM Plex Mono** | `ui-monospace, Consolas, monospace` |

**IBM Plex Mono é obrigatória** para: valores em R$, percentuais, competências (`AAAA-MM`), códigos de regra, ids de aditivo, fórmulas, logs.

### 6.2. Escala

| Papel | Tamanho | Peso | Cor | Notas |
| :--- | :--- | :--- | :--- | :--- |
| H1 | 24px | 600 | `text-primary` | margem inferior 16px |
| H2 | 16px | 600 | `text-primary` | |
| H3 | 14px | 600 | `text-secondary` | |
| Corpo / cláusula | 14px | 400 | `text-primary` ou `secondary` | line-height 1.6 |
| Micro-rótulo | 11px | 600 | `text-secondary` (ou `gold-text` em evidência) | CAIXA ALTA, `letter-spacing: 0.06em` |
| Valor de KPI | 24px | 600 | `text-primary` | IBM Plex Mono |

> No tema escuro, texto claro sobre fundo escuro parece mais "pesado": **NÃO DEVE** usar peso acima de 600 e **PODE** aumentar `line-height` do corpo para 1.65.

### 6.3. Dados financeiros

- Formato brasileiro com duas casas: `R$ 1.250,00`, `-R$ 300,00`.
- Valores e percentuais à **direita**; texto à **esquerda**.
- Números em IBM Plex Sans (texto corrido, rótulo de gráfico): `font-variant-numeric: tabular-nums`.

---

## 7. Componentes

### 7.1. Botões

| Tipo | Estilo |
| :--- | :--- |
| **Primário** | Fundo `--primary`, texto `--on-primary`. **Um por seção ou diálogo.** |
| **Secundário** | Fundo `--bg-surface`, borda `--border`, texto `--text-primary` |
| **Destrutivo** | Estilo secundário, texto na cor de texto de *Divergência* (3.6); nunca vermelho sólido |

Foco: anel `--focus-ring` com 2px de offset.

### 7.2. Cartões de KPI

- 3 a 4 colunas em linha única, no topo.
- Fundo `surface`, borda `--border`, filete superior `2px --gold`, raio `6px`.
- Rótulo em micro-rótulo caixa alta; valor em IBM Plex Mono.

### 7.3. Tabelas de fatura e confronto

- Ocultar o índice do Pandas.
- Colunas: *Competência · Regra Aplicada · Valor Esperado · Valor Cobrado · Diferença · Status*.
- Cabeçalho em `--bg-subtle`; zebra opcional em `--bg-subtle`.
- Só a célula de **Diferença** e o badge de **Status** recebem cor de divergência.

### 7.4. Badges

Altura fixa, raio `4px`, micro-rótulo caixa alta, ícone + texto, cores da seção 3.6. Selos de versão (`ADITIVO 01`) usam o conjunto ouro.

### 7.5. Abas

Aba ativa: sublinhado `2px --gold`, texto `--text-primary`. Inativa: `--text-secondary`.
Etapas: *Visão Geral e Vigência*, *Regras Extraídas*, *Auditoria de Faturas*, *Trilha de Evidências*.

### 7.6. Sidebar

- **Sempre escura**, nos dois temas. Fundo `--bg-sidebar`, divisória direita `--border-sidebar`.
- Conteúdo: seleção de contrato/cliente, filtros globais de competência, **seletor de tema**, status da base local.
- Item ativo: fundo `--bg-sidebar-hover` + barra esquerda `2px --gold`.

### 7.7. Linha do tempo de vigência

Horizontal: *Contrato-Base → Aditivo 01 → Aditivo 02*. Ao selecionar uma competência, o nó correspondente ganha marcador `--gold`.

### 7.8. Split-view de revisão humana

Duas colunas, ~55% / 45%, gap `32px`.

| Esquerda — Evidência | Direita — Estruturação |
| :--- | :--- |
| Trecho exato do documento em caixa `--gold-bg`, borda esquerda `3px --gold`, cabeçalho mono: `ADITIVO 01 • CLÁUSULA 3.2 • PÁG. 4` | Campos estruturados + **Aprovar Regra** (primário) e **Corrigir Manualmente** (secundário) |

### 7.9. Seletor de tema

Controle simples na sidebar (`🌙 Escuro` / `☀️ Claro`). A escolha persiste em `st.session_state` durante a sessão. Trocar de tema **NÃO DEVE** reiniciar nem alterar dados em tela.

---

## 8. Estados da Interface

| Estado | Tratamento |
| :--- | :--- |
| **Carregando** | `st.spinner` com texto objetivo ("Extraindo regras do contrato…"). Sem animação decorativa. |
| **Vazio** | Caixa `--bg-subtle`, borda `--border`, texto `--text-secondary` e a próxima ação sugerida. |
| **Erro** | Estilo *Divergência* (3.6), com o que falhou e o que fazer. |
| **Sucesso** | Estilo *Conforme* (3.6), discreto. |
| **Desabilitado** | Texto `--text-muted`, fundo `--bg-subtle`, sem hover. |

---

## 9. Fronteira Visual: IA × Motor Determinístico

| Origem | Tratamento obrigatório |
| :--- | :--- |
| **Sugestão de IA (não validada)** | Fundo `--gold-bg`, borda `1px dashed --gold`, badge `✦ SUGESTÃO IA — REVISAR` em `--gold-text` |
| **Regra validada / cálculo determinístico** | Fundo `--bg-surface`, borda sólida `--border`, valores em IBM Plex Mono |
| **Divergência calculada (`Decimal`)** | Destaque só na célula de diferença e no badge de status, com link para a evidência da regra vigente |

**Regra de ouro:** nenhum dado com tratamento de IA entra em cálculo oficial antes de aprovação humana explícita.

---

## 10. Gráficos

| Série | 🌙 Escuro | ☀️ Claro |
| :--- | :--- | :--- |
| 1 (principal) | `#7FA8E0` | `#23518C` |
| 2 | `#1F64BF` | `#1F64BF` |
| 3 | `#A0A6BB` | `#4F5173` |
| 4 | `#ABD2FA` | `#A2BDD2` |
| Destaque de divergência | cor de texto *Divergência* | cor de texto *Divergência* |

- Fundo do gráfico = `--bg-surface`; grade em `--border`; eixos e rótulos em `--text-secondary`.
- **Ouro não é cor de série.** Ele marca evidência e vigência, não dados.

---

## 11. Acessibilidade

- Contraste mínimo **4,5:1** para texto até 14px; **3:1** para texto grande e componentes de interface (vale para os dois temas).
- Status sempre com **cor + ícone + texto**.
- Foco de teclado sempre visível (`--focus-ring`).
- Alvos clicáveis com no mínimo 32px de altura.
- Nunca depender de hover para revelar informação crítica.
- Respeitar `prefers-color-scheme` **somente** na primeira visita; depois vale a escolha do usuário.

---

## 12. Implementação no Streamlit

### 12.1. Estrutura de arquivos

```
app/
├── .streamlit/config.toml     # tema base (escuro)
├── ui/
│   ├── tokens.py              # fonte única dos valores dos dois temas
│   ├── theme.py               # inject_theme(), seletor de tema
│   ├── styles.css             # CSS global usando apenas var(--token)
│   └── components.py          # kpi_card(), status_badge(), evidence_box()...
└── pages/
```

### 12.2. `.streamlit/config.toml` (padrão escuro)

```toml
[theme]
base = "dark"
primaryColor = "#23518C"
backgroundColor = "#0B0D12"
secondaryBackgroundColor = "#12151C"
textColor = "#EEF0F7"
font = "sans serif"

[client]
toolbarMode = "minimal"
```

Em cada página: `st.set_page_config(layout="wide")`.

> Versões recentes do Streamlit aceitam seções `[theme.light]` e `[theme.dark]` no `config.toml`. Confira na documentação da versão que o projeto fixar; a abordagem por CSS abaixo funciona em qualquer versão e é a **fonte da verdade** do guia.

### 12.3. `ui/tokens.py`

```python
THEMES = {
    "dark": {
        "bg-canvas": "#0B0D12",
        "bg-surface": "#12151C",
        "bg-subtle": "#1B1F2A",
        "bg-sidebar": "#07080B",
        "bg-sidebar-hover": "#1B1F2A",
        "text-primary": "#EEF0F7",
        "text-secondary": "#A0A6BB",
        "text-muted": "#7B819B",
        "primary": "#23518C",
        "primary-hover": "#2C65AE",
        "primary-pressed": "#1B3F6E",
        "on-primary": "#FFFFFF",
        "link": "#ABD2FA",
        "focus-ring": "rgba(171,210,250,.40)",
        "border-focus": "#7FA8E0",
        "gold": "#C2A267",
        "gold-text": "#D8BF8A",
        "gold-bg": "#1C1812",
        "gold-border": "#4A3E26",
        "border": "#2A2F40",
        "border-sidebar": "rgba(194,162,103,.30)",
        "sidebar-gradient": "linear-gradient(180deg,#07080B 0%,#0F1430 100%)",
        "ok-bg": "#0F2418",
        "ok-bd": "#1F5A38",
        "ok-tx": "#86EFAC",
        "bad-bg": "#2A1214",
        "bad-bd": "#7F1D1D",
        "bad-tx": "#FCA5A5",
        "info-bg": "#0F1D33",
        "info-bd": "#1E3A66",
        "info-tx": "#ABD2FA",
    },
    "light": {
        "bg-canvas": "#F7F8FC",
        "bg-surface": "#FFFFFF",
        "bg-subtle": "#EEF0F7",
        "bg-sidebar": "#0D0F15",
        "bg-sidebar-hover": "#1B1F2A",
        "text-primary": "#141A40",
        "text-secondary": "#404259",
        "text-muted": "#6B6F94",
        "primary": "#23518C",
        "primary-hover": "#1B3F6E",
        "primary-pressed": "#141A40",
        "on-primary": "#FFFFFF",
        "link": "#23518C",
        "focus-ring": "rgba(35,81,140,.35)",
        "border-focus": "#A2BDD2",
        "gold": "#A58F69",
        "gold-text": "#735114",
        "gold-bg": "#F9F6F0",
        "gold-border": "#E2D8C5",
        "border": "#D8D9EA",
        "border-sidebar": "rgba(165,143,105,.35)",
        "sidebar-gradient": "linear-gradient(180deg,#0D0F15 0%,#141A40 100%)",
        "ok-bg": "#ECFDF3",
        "ok-bd": "#BBF7D0",
        "ok-tx": "#14532D",
        "bad-bg": "#FEF2F2",
        "bad-bd": "#FECACA",
        "bad-tx": "#991B1B",
        "info-bg": "#EFF6FF",
        "info-bd": "#BFDBFE",
        "info-tx": "#23518C",
    },
}
```

### 12.4. `ui/theme.py`

```python
import streamlit as st
from ui.tokens import THEMES


def inject_theme():
    st.session_state.setdefault("theme", "dark")  # escuro é o padrão
    with st.sidebar:
        choice = st.radio(
            "Tema",
            ["🌙 Escuro", "☀️ Claro"],
            index=0 if st.session_state.theme == "dark" else 1,
            horizontal=True,
            label_visibility="collapsed",
        )
    st.session_state.theme = "dark" if "Escuro" in choice else "light"

    vars_css = "".join(f"--{k}:{v};" for k, v in THEMES[st.session_state.theme].items())
    base_css = open("ui/styles.css", encoding="utf-8").read()
    st.markdown(f"<style>:root{{{vars_css}}}\n{base_css}</style>", unsafe_allow_html=True)
```

Chamar `inject_theme()` uma vez, no topo de cada página, logo após `set_page_config`.

### 12.5. `ui/styles.css` (usa apenas variáveis)

```css
@import url("https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;600&display=swap");

:root {
  --radius-sm:4px; --radius-md:6px;
  --font-ui:"IBM Plex Sans",-apple-system,"Segoe UI",sans-serif;
  --font-mono:"IBM Plex Mono",ui-monospace,Consolas,monospace;
}

html, body, [class*="css"] { font-family: var(--font-ui); }
.stApp { background: var(--bg-canvas); color: var(--text-primary); }
a { color: var(--link); }

section[data-testid="stSidebar"] {
  background: var(--sidebar-gradient);
  border-right: 1px solid var(--border-sidebar);
  color: var(--text-on-dark, #EEF0F7);
}

.stButton > button[kind="primary"] {
  background: var(--primary); color: var(--on-primary); border: 0;
  border-radius: var(--radius-sm);
}
.stButton > button[kind="primary"]:hover { background: var(--primary-hover); }
.stButton > button[kind="primary"]:active { background: var(--primary-pressed); }
:focus-visible { outline: 2px solid var(--focus-ring); outline-offset: 2px; }

.kpi { background:var(--bg-surface); border:1px solid var(--border);
       border-top:2px solid var(--gold); border-radius:var(--radius-md); padding:16px; }
.kpi .label { font-size:11px; font-weight:600; letter-spacing:.06em;
              text-transform:uppercase; color:var(--text-secondary); }
.kpi .value { font:600 24px var(--font-mono); color:var(--text-primary); }

.evidence { background:var(--gold-bg); border-left:3px solid var(--gold);
            border-radius:var(--radius-sm); padding:16px; }
.evidence .meta { font:600 11px var(--font-mono); color:var(--gold-text);
                  letter-spacing:.06em; text-transform:uppercase; }

.ai-suggestion { background:var(--gold-bg); border:1px dashed var(--gold);
                 border-radius:var(--radius-sm); padding:16px; }

.badge { display:inline-flex; gap:4px; align-items:center; padding:2px 8px;
         border-radius:var(--radius-sm); font:600 11px var(--font-ui);
         letter-spacing:.06em; text-transform:uppercase; border:1px solid; }
.badge.ok   { background:var(--ok-bg);   border-color:var(--ok-bd);   color:var(--ok-tx); }
.badge.bad  { background:var(--bad-bg);  border-color:var(--bad-bd);  color:var(--bad-tx); }
.badge.info { background:var(--info-bg); border-color:var(--info-bd); color:var(--info-tx); }
.badge.ai   { background:var(--gold-bg); border-color:var(--gold-border); color:var(--gold-text); }

.num { font-family:var(--font-mono); text-align:right; }
```

### 12.6. Limitações conhecidas

- `st.dataframe` **não** suporta borda tracejada nem badges por célula, e seu tema interno segue o `config.toml` (base escura), não o seletor próprio. Para tabelas com badges e troca de tema fiel, renderize a tabela em HTML com as classes acima; use `st.dataframe` só para exploração rápida.
- Gráficos (Plotly/Altair) precisam receber as cores da seção 10 conforme o tema ativo; eles não herdam variáveis CSS.
- Seletores internos do Streamlit (`data-testid`) podem mudar entre versões. Mantenha-os centralizados em `styles.css` e fixe a versão no `requirements.txt`.

---

## 13. Checklist de Validação (antes de concluir qualquer tela)

1. Telas conferidas **nos dois temas**; troca de tema não perde dados nem estado.
2. Nenhum hexadecimal fora de `tokens.py`; nenhuma cor proibida (3.7).
3. Escuro: sem `#000000` e sem `#FFFFFF` como texto. Elevação por clareamento, não por sombra.
4. Gradientes restritos à sidebar e ao fundo vertical do canvas (`--canvas-gradient`, mais claro embaixo); sem sombra difusa; sem vidro fosco.
5. Ouro apenas em filetes, evidências, vigência, selos e aba ativa; nunca em botão primário nem como série de gráfico.
6. Texto azul no escuro usa `--link`, nunca `--primary`.
7. Valores monetários e competências em IBM Plex Mono, formato `R$ 1.250,00`.
8. No máximo **um** botão primário por seção.
9. Dados de IA separados (tracejado + selo `✦`) dos cálculos determinísticos.
10. Toda divergência permite chegar à regra vigente e ao trecho documental de origem.
11. Status com cor + ícone + texto; contraste ≥ 4,5:1 nos dois temas.
12. Estados de carregamento, vazio e erro tratados.
13. Espaçamentos em múltiplos de 4px; raios ≤ 8px.

---

## 14. Histórico de Decisões

| Decisão | Motivo |
| :--- | :--- |
| Tema escuro como padrão, claro como alternativa | O escuro espelha a ilustração; o claro cobre impressão e ambientes claros |
| Tokens com mesmos nomes nos dois temas | Componentes não precisam saber qual tema está ativo |
| Ouro mais luminoso no escuro, mais fosco no claro | Mantém contraste ≥ 4,5:1 e a mesma função nos dois |
| Ouro tratado como "Disco de Ouro da Voyager" | Símbolo de registro durável de informação = evidência rastreável |
| Texto azul no escuro via `--link` (`#ABD2FA`) | `#23518C` sobre fundo escuro tem só ≈ 2,3:1 |
| Hover do primário clareia no escuro e escurece no claro | Em fundo escuro, "mais escuro" não lê como destaque |
| Sidebar escura nos dois temas | Ancora a identidade visual e reduz o trabalho de manter dois layouts |
| `--text-muted` ajustado para ≈ 4,6:1 | Versão original ficava em ≈ 3:1 |
| ✦ como único ícone-motivo | Traz o brilho da ilustração sem efeitos visuais |
| Seletor de tema próprio via CSS | Funciona em qualquer versão do Streamlit, sem depender de recurso novo |
