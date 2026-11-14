# Regras de Commit

Utilizamos o padrão **Conventional Commits**. As mensagens são escritas em português, em minúsculas e **sem acentos no assunto** (evita problemas de codificação no terminal). O corpo do commit pode ter acentos.

Documentos relacionados: [style.md](style.md), [agente.md](agente.md), [testes.md](testes.md), [decisoes.md](decisoes.md).

Formato padrão:

```text
tipo(escopo): descricao curta
```

- Verbo no **presente do indicativo, 3ª pessoa** (`adiciona`, `corrige`, `remove`), como nos exemplos abaixo.
- Assunto com no máximo 72 caracteres, sem ponto final.
- Mudança que quebra compatibilidade: acrescente `!` após o escopo (`feat(api)!: ...`) e explique no corpo.

## Tipos Permitidos

| Tipo | Uso | Exemplo Prático |
|---|---|---|
| `feat` | Novas funcionalidades | `feat(frontend): adiciona botao de upload de pdf no streamlit` |
| `fix` | Correções de bugs e falhas | `fix(motor): corrige erro de calculo de vigencia retroativa` |
| `docs` | Documentação, artigo e slides | `docs(artigo): adiciona capitulo de metodologia abnt` |
| `test` | Criação ou alteração de testes | `test(schemas): adiciona validacao pydantic para cnpj` |
| `refactor` | Melhorias sem alterar comportamento | `refactor(agentes): limpa imports nao utilizados no agente jurista` |
| `chore` | Manutenção, dependências e configs | `chore(config): atualiza versao do langgraph no pyproject.toml` |
| `style` | Formatação de código (espaços, aspas) | `style(motor): aplica formatacao do ruff no motor deterministico` |
| `ci` | Automação de verificações | `ci(config): executa ruff e pytest em cada push` |

## Escopos Sugeridos

O escopo indica a camada alterada:

- `frontend` (Streamlit)
- `motor` (regras temporais e cálculos em Python)
- `agentes` (LangGraph, extração e clientes de modelo)
- `prompts` (arquivos de prompt; ver regra abaixo)
- `schemas` (modelos Pydantic)
- `persistencia` (repositório SQLite, hash de arquivos e registro de decisões)
- `leitura` (leitura e conversão de documentos: PDF, DOCX, XML, planilhas, mascaramento)
- `api` (rotas FastAPI)
- `tests` (pytest)
- `avaliacao` (conjuntos e scripts em `evaluation/`)
- `artigo` (documentos acadêmicos ABNT e slides)
- `config` (arquivos base do repositório)

O tipo `docs` cobre README, `ESTADO_DO_PROJETO.md`, `evaluation/README.md` e os arquivos de `.regras/` (inclusive `decisoes.md`).

## Boas Práticas da Equipe

- **Um commit = uma mudança lógica.** Não misture a criação do Agente Jurista com a estilização de um botão no Streamlit. Faça commits separados.
- **Correção de bug com teste:** o commit de `fix` inclui o teste que reproduz o erro, ou um commit `test` imediatamente anterior.
- **Mudança de prompt ou de modelo** vai em commit próprio (`feat(prompts)` ou `fix(prompts)`) e traz no corpo o resultado da avaliação (ver agente.md, seção 5), porque altera o comportamento do sistema.
- **Mudança de gabarito** (resposta esperada de um caso de teste ou de avaliação) vai em commit próprio, com a justificativa no corpo. Nunca altere o gabarito no mesmo commit que altera o código para fazê-lo passar.
- **Validação pré-commit:** na raiz do projeto, rode obrigatoriamente:
  - `uv run ruff check .` (conformidade com o Style Guide);
  - `uv run ruff format --check .` (formatação);
  - `uv run pytest` (a matemática exata e o motor temporal não quebraram).
- **Automação recomendada:** configurar esses três comandos como hook de pré-commit ou no CI (`ci`), para não depender de memória.

## Nunca Versionar

- Contratos, faturas ou notas fiscais **reais** (use documentos públicos ou sintéticos, em `data/`).
- Chaves de API, arquivos `.env` e credenciais.
- Bancos de dados locais (`*.db`) e arquivos de log com conteúdo de documentos.

Se algo sensível for commitado por engano, avise a equipe imediatamente: apagar o arquivo em um novo commit **não** remove o conteúdo do histórico, e chaves expostas devem ser revogadas.
