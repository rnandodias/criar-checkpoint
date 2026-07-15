# criar-checkpoint

Pipeline para **gerar atividades de Checkpoint** (prova teórica + prova prática) dos níveis de carreira da Alura, de ponta a ponta:

1. **Coleta** as transcrições dos cursos via **API oficial de cursos da Alura**.
2. **Resume** cada curso em conteúdos testáveis (fonte única de verdade das provas).
3. **Gera** a prova teórica (20 questões de múltipla escolha) e a prova prática (Aula 3 do Checkpoint).
4. **Publica** as atividades direto no admin da Alura via automação Playwright.

Cada etapa é um script CLI independente, orquestrado sequencialmente. O pipeline foi desenhado para rodar **em conjunto com um assistente de IA** (o [Claude Code](https://docs.anthropic.com/en/docs/claude-code)), que atua como **copiloto de execução**: dispara comandos, monitora o progresso, alerta problemas estruturais e pausa para consultar quando aparece uma decisão relevante (custo, escopo, correção de rumo).

Também funciona 100% na mão, via CLI, para quem preferir.

---

## Arquitetura em 30 segundos

```text
1) obter_transcricoes  ──▶ trilha/<carreira>_nivel_<n>.json
2) checkpoint_criar_resumos_cursos  ──▶  output/<slug>_nivel_<n>/resumos.json
3) gerar_prova_teorica_do_zero      ──▶  output/<slug>_nivel_<n>/prova_teorica.txt
3.5) revisar_prova_teorica          ──▶  auto-corrige + gera prova_teorica_relatorio.md
4) gerar_prova_pratica_do_zero      ──▶  output/<slug>_nivel_<n>/prova_pratica.txt
4.5) revisar_prova_pratica          ──▶  auto-corrige + gera prova_pratica_relatorio.md
5) upload_checkpoint_alura          ──▶  publica seções/atividades no admin da Alura
```

- Etapa 1 é chamada HTTP (rápida, ~5s para 8 cursos).
- Etapas 2, 3, 3.5, 4 e 4.5 usam LLM Anthropic (**Claude Opus 4-8** em tudo, por padrão).
- Etapa 5 usa Playwright (janela visível por padrão) para automatizar o admin da Alura.
- Etapas 2 e 3 (fases 1 e 2) usam **Message Batches API** automaticamente quando ≥2 chamadas (50% off).
- Etapa 4 tem batch **opt-in** via `--batch`.
- **Etapas 3.5 e 4.5** rodam depois das etapas 3 e 4 respectivamente: analisam o TXT, auto-corrigem itens com problemas mecânicos, e disparam **rerun automático** (variante 3) quando o problema é sistêmico (≥50% dos exercícios ou ≥3 seções). Zero intervenção manual necessária no caso comum.

---

## Pré-requisitos

- **Python 3.10+**
- **Credenciais no `.env`** (copie de `.env.example`):
  - `ALURA_API_TOKEN` → etapa 1 (API oficial de cursos)
  - `ANTHROPIC_API_KEY` → etapas 2, 3, 4 (LLM)
  - `EMAIL` e `PASSWORD` → etapa 5 (login no admin via Playwright)
  - `OPENAI_CREDENTIALS` → opcional; só se quiser trocar de provider LLM
- **Curso de Checkpoint criado no admin**: você precisa do `curso_id` que aparece na URL `/admin/courses/v2/<id>`. Esse curso vazio é criado manualmente antes da etapa 5.

## Instalação

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Linux/macOS

pip install -r requirements.txt
playwright install                # só necessário para a etapa 5

cp .env.example .env              # depois edite com suas credenciais
```

---

## Como usar

### 🟢 Modo com Claude Code (recomendado)

Este é o modo que temos usado. Você conversa com o Claude Code no terminal (ou VS Code), ele dispara os scripts em background, monitora saídas e te consulta em decisões.

**Um exemplo de sessão típica:**

> **Você:** "Vamos criar um checkpoint pra Engenharia de Dados nível 1."
>
> **Claude Code:** _"Essa carreira não está cadastrada em [`scripts/carreiras_niveis.py`](./scripts/carreiras_niveis.py). Me passa: slug (ex.: `engenharia_de_dados`), lista de IDs dos cursos do nível 1 na ordem da trilha, e nome oficial pros prompts."_
>
> **Você:** "Slug `engenharia_de_dados`, IDs `1234,5678,9012,...`, nome `Engenharia de Dados`."
>
> **Claude Code:** cadastra no arquivo, dispara etapa 1 (API), reporta `8/8 cursos em 5s`. Dispara etapa 2 (resumos com Opus, batch). Dispara etapa 3 (teórica). Dispara etapa 4 (prática com `--batch`). Aponta pra você os dois TXTs prontos e diz: _"Aguardando okay do coordenador + `curso_id` para publicar."_
>
> **Você:** "OK do coordenador, `curso_id` 6554."
>
> **Claude Code:** dispara as 4 sub-etapas da publicação (`criar_secoes`, `criar_atividade_apresentacao`, `criar_atividades_prova_teorica`, `criar_atividades_prova_pratica`) em sequência, reportando cada uma. Se algum passo falhar (timeout de login, por exemplo), tenta de novo automaticamente.

**O que o assistente faz por você:**

- Cadastra novas carreiras/níveis no [`scripts/carreiras_niveis.py`](./scripts/carreiras_niveis.py).
- Dispara cada script com os argumentos certos, em background.
- Monitora saída e detecta problemas (parser divergente, teto matemático de exercícios, timeout de rede, credencial inválida).
- Alerta sobre situações fora do padrão (ex.: "só 9 exercícios em vez de 20 — parece bug no parser").
- Retenta falhas transitórias automaticamente.
- Pausa e pergunta em decisões (trocar de modelo, gastar mais tokens, corrigir prompt, aceitar 18 em vez de 20).

**O que você faz:**

- Fornece os dados de entrada (carreira, IDs, `curso_id`).
- Revisa os TXTs antes da publicação (mandar pro coordenador).
- Aprova/redireciona quando o assistente pausa pra decidir.
- Confirma o okay final para publicar.

### 🔵 Modo manual (CLI puro)

Se você não vai usar o Claude Code, o pipeline roda 100% na mão. Sequência para gerar e publicar o checkpoint de uma carreira/nível:

```bash
# 1) Cadastre a carreira/nível em scripts/carreiras_niveis.py (se não existir)

# 2) Coleta transcrições via API Alura
python scripts/obter_transcricoes_cursos.py --carreira <slug> --nivel <n>

# 3) Gera resumos (batch Anthropic automático se ≥2 chamadas)
python scripts/checkpoint_criar_resumos_cursos.py --carreira <slug> --nivel <n>

# 4) Gera prova teórica (20 questões, batch automático nas fases 1 e 2)
#    resumos_arquivo é opcional; se omitido, deriva de output/<slug>_nivel_<n>/resumos.json
python scripts/gerar_prova_teorica_do_zero.py --nivel <n> --carreira "Nome Oficial" \
  --max_questoes 20 --min_por_curso 1 --max_por_curso 3 --domains_window 3

# 4.5) Revisa a prova teórica e auto-corrige o que der (Opus 4-8; escape hatch se problema sistêmico)
python scripts/revisar_prova_teorica.py --carreira "Nome Oficial" --nivel <n>

# 5) Gera prova prática (batch opt-in com --batch)
python scripts/gerar_prova_pratica_do_zero.py --nivel <n> --carreira "Nome Oficial" --batch

# 5.5) Revisa a prova prática (análise estática + teste de resolvedor + auto-correção)
python scripts/revisar_prova_pratica.py --carreira "Nome Oficial" --nivel <n>

# 6) REVISE os TXTs em output/<slug>_nivel_<n>/ + o relatório em ...relatorio.md e envie ao coordenador
#    Só siga para publicação depois do okay dele.

# 7) Publica no admin (precisa do curso_id, criar o curso de checkpoint vazio antes)
python scripts/upload_checkpoint_alura.py --curso_id <ID> --etapa criar_secoes
python scripts/upload_checkpoint_alura.py --curso_id <ID> --etapa criar_atividade_apresentacao --nivel <n>
python scripts/upload_checkpoint_alura.py --curso_id <ID> --etapa criar_atividades_prova_teorica \
  --carreira "Nome Oficial" --nivel <n>
python scripts/upload_checkpoint_alura.py --curso_id <ID> --etapa criar_atividades_prova_pratica \
  --carreira "Nome Oficial" --nivel <n>
```

**Comandos auxiliares:**

```bash
# Listar carreiras/níveis já mapeados
python scripts/obter_transcricoes_cursos.py --listar

# Re-subir apenas exercícios específicos da teórica (após um run parcial)
python scripts/upload_checkpoint_alura.py --curso_id <ID> --etapa criar_atividades_prova_teorica \
  --carreira "Nome Oficial" --nivel <n> --indices 1,5,7

# Forçar sync na etapa 2 (debug, sem batch)
python scripts/checkpoint_criar_resumos_cursos.py --carreira <slug> --nivel <n> --no-batch
```

---

## Etapa por etapa — detalhes técnicos

### 1) `obter_transcricoes_cursos.py` — API Alura

Chama `https://cursos.alura.com.br/api/course/{id}` para cada curso da carreira/nível, extrai o texto das atividades e grava um JSON no formato:

```json
[
  {"id": 5869, "nome": "...", "link": "https://cursos.alura.com.br/course/<slug>",
   "transcricao": ["Atividade 1 - Título\n<texto>", "Atividade 2 - ...", ...]}
]
```

- Inclui atividades do tipo `VIDEO`, `HQ_EXPLANATION` e `TEXT_CONTENT` (materiais curados como "Para saber mais", tabelas comparativas, "O que aprendemos?"). Ignora `SINGLE_CHOICE` para não poluir o resumo.
- Throttle de 150ms entre chamadas (rate limit da API é 10 req/s).
- Sem login por EMAIL/PASSWORD. Só precisa de `ALURA_API_TOKEN`.

### 2) `checkpoint_criar_resumos_cursos.py` — Resumos LLM

Lê o JSON de transcrições e gera, para cada curso, um resumo estruturado (fonte única de verdade das provas):

```json
{
  "tema_central": "frase curta sobre o que o curso aborda",
  "conteudos_testaveis": [
    {"topico": "...", "nivel": "central|complementar", "tipo": "conceitual|procedimental",
     "habilidade": "...", "evidencia_de_ensino": "...", "armadilhas_comuns": ["..."]}
  ],
  "ferramentas_usadas": ["..."]
}
```

- Uma chamada por vídeo/atividade (fallback de chunking para textos muito longos).
- Batch Anthropic automático quando ≥2 chamadas por curso (50% off).
- Cache de prompts também ativo (90% off em hits) — funciona se os blocks estáticos atingirem 1024 tokens (Sonnet/Opus) ou 2048 (Haiku).
- Provider detectado pelo prefixo do `MODEL`: `gpt-*`/`o1-*` → OpenAI, `claude-*` → Anthropic. Padrão atual: `claude-opus-4-6`.

### 3) `gerar_prova_teorica_do_zero.py` — Prova teórica

Gera 20 questões de múltipla escolha (4 alternativas + justificativas). Fluxo interno em 4 fases:

| Fase | O que faz | Modo | Modelo padrão |
|---|---|---|---|
| 1 | Gera ideias de questões por curso | **Batch** (≥2 cursos) | `MODEL_IDEAS` = `claude-opus-4-6` |
| 2 | Transforma ideias em múltipla escolha (mínimo por curso) | **Batch** (≥2 questões) | `MODEL_FORMAT` = `claude-opus-4-6` |
| 3 | Completa até atingir `--max_questoes` | **Sync** (loop iterativo com estado) | `MODEL_FORMAT` |
| 4 | Ranqueia dificuldade 1–5 e ordena ascendente | **Sync** (1 chamada única) | `MODEL_RANK` = `claude-opus-4-6` |

**Teto matemático:** o total possível é `num_cursos × max_por_curso`. Se você pedir 20 questões numa carreira com 6 cursos e `max_por_curso=3`, o teto é 18 e o script para lá. Solução: aumentar `--max_por_curso 4`.

**Parser tolerante:** o parser (`_parse_exercise_ideas_verbatim`) aceita markdown bold, cabeçalhos `##`, e sinônimos comuns nos marcadores (`Enunciado`/`Pergunta`/`Questão` = `Texto da questão`; `Resolução`/`Solução`/`Resposta correta` = `Resposta`; `Conceito`/`Tópico` = `Conceito abordado`). O prompt em `_ask_exercise_ideas` impõe formato estrito — o parser é rede de segurança.

**Domínios:** usa uma lista padrão de empresas fictícias (Bytebank, Serenatto, Freelando, etc.). Substituível via `--domains_arquivo caminho.json`.

### 4) `gerar_prova_pratica_do_zero.py` — Prova prática (Aula 3)

Gera 1 arquivo TXT com: descrição do projeto, ambiente, 4 etapas com dificuldade crescente, pergunta-chave por etapa, missão passo a passo, dicas de troubleshooting, matriz de cobertura (auditoria mapeando cursos → etapas).

- **1 única chamada LLM** (`MODEL_GEN` = `claude-opus-4-6`).
- Batch **opt-in** via `--batch` (50% off, latência 5–30 min).
- **Sem nuvem paga:** AWS/Azure/GCP proibidos (custo pro aluno).
- **Datasets inline** (CSV/JSON, 30–120 linhas) só aparecem quando a carreira envolve dados (heurística automática, override com `--modo_dados com|sem|auto`).
- **Perfil da carreira** (programática vs conceitual): heurística baseada em % de `conteudos_testaveis` com `tipo: procedimental`. Em carreiras conceituais (ex.: governança), o system prompt orienta entregáveis documentais/diagramáticos.

### 5) `upload_checkpoint_alura.py` — Publicação (Playwright)

Automação Playwright que cria seções e atividades direto no admin da Alura.

| Etapa CLI | O que faz |
|---|---|
| `criar_secoes` | Cria as 3 seções (Apresentação, Prova teórica, Prova prática) e marca a teórica como `É prova?` |
| `criar_atividade_apresentacao` | Cria 1 atividade Explicação "Etapas do projeto" na seção Apresentação |
| `criar_atividades_prova_teorica` | Cria 1 atividade "Única escolha" por exercício do TXT da teórica |
| `criar_atividades_prova_pratica` | Cria 1 atividade Explicação por subtítulo da prática + 1 Conclusão hardcoded |
| `desativar_atividades_prova_teorica` | Desativa atividades da prova teórica que não estão numa lista de títulos a manter |

**Flags úteis:**

- `--indices 1,5,7`: sobe apenas os exercícios das posições listadas (1-based). Útil para re-subir quem falhou após um run parcial.
- `--limite N`: processa apenas os N primeiros exercícios (validação inicial).
- `--offset N`: pula os N primeiros exercícios (retomada após criação parcial).
- `--headless`: roda sem janela visível.

**Detalhes técnicos:**

- Admin usa **EasyMDE/CodeMirror** em campos de markdown — o script preenche via JS (`CodeMirror.setValue()` + `.save()`).
- Dropdown de tipo hierárquico (`select#chooseTask`) — seleção via `data-task-enum` (HQ_EXPLANATION, SINGLE_CHOICE).
- Alternativas usam names HTML específicos (`alternatives[N].text`, `alternatives[N].opinion`, `alternatives[N].correct`).
- **Idempotência parcial:** criação de seções e atividades **não é reversível** — um rerun cria duplicatas. Use `--indices` / `--offset` / `--limite` para retomar.

---

## Convenções importantes

- **Nomenclatura dos arquivos:** `<slug_carreira>_nivel_<1|2|3>.json` (ex.: `governanca_de_dados_nivel_1.json`). Todos os scripts assumem esse padrão.
- **Slug da carreira:** snake_case, sem acento (ex.: `desenvolvimento_back_end_nodejs_v2`).
- **Nome oficial da carreira:** o que vai no `--carreira` da CLI e nos prompts. Pode ter espaço, hífen, acento (ex.: `"Desenvolvimento Back-End Node.js v2"`).
- **Fidelidade à aula:** os scripts de geração **não podem inventar** conceitos, ferramentas ou técnicas que não estejam nos resumos. Regra codificada nos prompts — não relaxar.
- **Linguagem neutra:** "pessoa desenvolvedora", "a empresa te contratou". **Nunca** "você foi contratado" nem masculino genérico.
- **Sem nuvem paga na prática:** AWS, Azure, GCP proibidos.

---

## Estrutura do projeto

```text
criar-checkpoint/
├── .env.example                     # modelo de credenciais
├── CLAUDE.md                        # contexto para assistentes de código
├── README.md                        # este arquivo
├── requirements.txt
├── scripts/
│   ├── _scraping_utils.py                    # legado (não é mais usado)
│   ├── carreiras_niveis.py                   # mapa carreira/nível → IDs dos cursos
│   ├── obter_transcricoes_cursos.py          # 1) API Alura de cursos
│   ├── checkpoint_criar_resumos_cursos.py    # 2) resumos LLM
│   ├── gerar_prova_teorica_do_zero.py        # 3) prova teórica (múltipla escolha)
│   ├── revisar_prova_teorica.py              # 3.5) QA + auto-correção (variantes 2 e 3 automáticas)
│   ├── gerar_prova_pratica_do_zero.py        # 4) prova prática (Aula 3)
│   ├── revisar_prova_pratica.py              # 4.5) QA + teste de resolvedor + auto-correção
│   └── upload_checkpoint_alura.py            # 5) publica seções/atividades no admin
├── trilha/                          # saída da etapa 1 (entrada da etapa 2)
└── output/
    └── <slug>_nivel_<n>/            # uma pasta por projeto (carreira + nível)
        ├── resumos.json                     # saída da etapa 2
        ├── resumos.jsonl
        ├── prova_teorica.txt                # etapa 3 (sobrescrito pela 3.5 se houver correção)
        ├── prova_teorica.pre_revisao.txt    # 3.5 backup (só se algo foi alterado)
        ├── prova_teorica_relatorio.md       # 3.5 relatório
        ├── prova_pratica.txt                # etapa 4
        ├── prova_pratica.pre_revisao.txt    # 4.5 backup
        └── prova_pratica_relatorio.md       # 4.5 relatório
```

---

## Troubleshooting comum

**Etapa 1 retorna `HTTP 401`/`403`**
Token da API está inválido ou expirado. Cheque `ALURA_API_TOKEN` no `.env`.

**Etapa 3 gerou menos exercícios do que `--max_questoes`**
Provavelmente atingiu o teto matemático `num_cursos × max_por_curso`. Aumente `--max_por_curso`.

**Etapa 5 falha no login com `TimeoutError: Timeout 15000ms exceeded`**
Falha transitória do `wait_for_load_state("networkidle")`. Basta re-executar o comando — geralmente passa na segunda tentativa.

**Uploader pulou um exercício da teórica**
Provavelmente o exercício tem formato levemente fora do padrão que o parser espera. O parser da geração é tolerante, mas o parser do uploader espera marcadores exatos. Cheque o TXT — pode faltar `Título:`, `Pergunta:` ou linha de separação.

**Custo do run parecendo alto**
Confirme que o Opus só está sendo usado onde faz sentido. Se estiver rodando várias iterações, considere: (a) rodar prática com `--batch` (50% off), (b) validar estrutura com `--limite 1` antes de um upload completo.
