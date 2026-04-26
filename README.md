# criar-checkpoint

Projeto isolado para **gerar atividades de Checkpoint** (prova teórica e prova prática) dos níveis de carreira da Alura, de ponta a ponta:

1. **Coleta** as transcrições dos vídeos dos cursos diretamente da plataforma (scraping).
2. **Resume** cada curso em conteúdos testáveis.
3. **Gera** a prova teórica (múltipla escolha) e a prova prática (Aula 3 do Checkpoint).
4. **Publica** as atividades direto no admin da Alura via automação Playwright.

Todo o fluxo acontece neste único projeto.

---

## Fluxo completo (ponta a ponta)

```text
┌──────────────────────────┐  JSON transcrições  ┌────────────────────┐  JSON resumo  ┌────────────────────────┐  TXT prova  ┌─────────────────────────┐
│ 1) obter_transcricoes    │ ───────────────────▶│ 2) checkpoint_criar│ ────────────▶ │ 3) gerar_prova_teorica │ ──────────▶ │ 5) upload_checkpoint    │
│    Playwright + Alura    │  trilha/<nome>.json │    resumos (LLM)   │  output/...   │ 4) gerar_prova_pratica │             │    Playwright + admin   │
└──────────────────────────┘                     └────────────────────┘               └────────────────────────┘             └─────────────────────────┘
```

A etapa 2 aceita modelos OpenAI (`gpt-*`, `o1/o3/o4-*`) **ou** Anthropic (`claude-*`) — o provider é escolhido pelo prefixo do MODEL no topo de cada script. Para Anthropic, a etapa 2 e 3 usam Message Batches API automaticamente (50% off).

### 1) Coletar as transcrições dos cursos

```bash
# Usando o registro de carreiras/níveis já incluído:
python scripts/obter_transcricoes_cursos.py --carreira governanca_de_dados --nivel 1

# Ou passando IDs manualmente:
python scripts/obter_transcricoes_cursos.py \
  --nome_saida governanca_de_dados_nivel_1 \
  --ids 3713,4631,4632,3714,4633,3716,4635,3717,5166,4634

# Para ver as carreiras/níveis já mapeados:
python scripts/obter_transcricoes_cursos.py --listar
```

- Saída: [`trilha/<nome_saida>.json`](./trilha/) já no formato esperado pelo próximo script.

### 2) Gerar os resumos dos cursos

```bash
# Atalho via carreira/nível (monta o nome do arquivo automaticamente):
python scripts/checkpoint_criar_resumos_cursos.py --carreira governanca_de_dados --nivel 1

# Ou explicitando o(s) arquivo(s):
python scripts/checkpoint_criar_resumos_cursos.py --input_files governanca_de_dados_nivel_1.json

# Anthropic + batch ativo por padrão (50% off). Para forçar sync (debug):
python scripts/checkpoint_criar_resumos_cursos.py --carreira governanca_de_dados --nivel 1 --no-batch
```

- Saída: `output/checkpoints/resumos_<nome>.json` (e `.jsonl`).

### 3) Gerar a prova teórica

```bash
python scripts/gerar_prova_teorica_do_zero.py \
  --nivel <1, 2, 3> \
  --carreira "Nome Carreira" \
  --resumos_arquivo output/checkpoints/resumos_<nome>.json \
  --max_questoes 10 --min_por_curso 1 --max_por_curso 2 --domains_window 3
```

- Saída: `output/cursos_checkpoint/prova_teorica_<slug_carreira>_nivel_<n>.txt`

### 4) Gerar a prova prática

```bash
python scripts/gerar_prova_pratica_do_zero.py \
  --nivel <1, 2, 3> \
  --carreira "Nome Carreira" \
  --resumos_arquivo output/checkpoints/resumos_<nome>.json
```

- Saída: `output/cursos_checkpoint/prova_pratica_<slug_carreira>_nivel_<n>.txt`

### 5) Publicar no admin da Alura (Playwright)

Cria seções e atividades no curso de checkpoint (`/admin/courses/v2/<curso_id>`).

```bash
# Cria as 3 seções (Apresentação, Prova teórica, Prova prática) e marca a teórica como prova
python scripts/upload_checkpoint_alura.py --curso_id <ID> --etapa criar_secoes

# Cria a atividade "Etapas do projeto" (Explicação) na seção Apresentação
python scripts/upload_checkpoint_alura.py --curso_id <ID> --etapa criar_atividade_apresentacao --nivel 1

# Cria 1 atividade "Única escolha" por exercício do TXT da prova teórica
python scripts/upload_checkpoint_alura.py --curso_id <ID> --etapa criar_atividades_prova_teorica \
  --carreira "Governança de Dados" --nivel 1
# Para validar antes de processar todos: --limite 1; para retomar após criação parcial: --offset N

# Cria 1 atividade "Explicação" por subtítulo da prova prática + 1 Conclusão hardcoded
python scripts/upload_checkpoint_alura.py --curso_id <ID> --etapa criar_atividades_prova_pratica \
  --carreira "Governança de Dados" --nivel 1
```

- Modo padrão: janela visível (headful). Use `--headless` para rodar em background.
- Idempotência parcial: a criação de seções e atividades **não é reversível** — um rerun cria duplicatas. Use `--limite N` / `--offset N` para retomar.

---

## Estrutura do projeto

```text
criar-checkpoint/
├── .env.example                     # modelo de credenciais
├── .gitignore
├── README.md
├── requirements.txt
├── scripts/
│   ├── _scraping_utils.py                    # utilitário: limpeza de texto
│   ├── carreiras_niveis.py                   # mapa carreira/nível → IDs dos cursos
│   ├── obter_transcricoes_cursos.py          # 1) scraping das transcrições
│   ├── checkpoint_criar_resumos_cursos.py    # 2) resumos dos cursos
│   ├── gerar_prova_teorica_do_zero.py        # 3) prova teórica (múltipla escolha)
│   ├── gerar_prova_pratica_do_zero.py        # 4) prova prática (Aula 3)
│   └── upload_checkpoint_alura.py            # 5) publica seções/atividades no admin Alura
├── trilha/                          # saída da etapa 1 (entrada da etapa 2)
└── output/
    ├── checkpoints/                 # saída da etapa 2 (entrada das etapas 3 e 4)
    └── cursos_checkpoint/           # saída final: provas teórica e prática
```

---

## Pré-requisitos

- Python 3.10+
- Conta ativa na plataforma da Alura (email + senha) — para o scraping e o upload no admin.
- **Pelo menos uma** chave válida de LLM:
  - **OpenAI API** (`OPENAI_CREDENTIALS`) — usada quando o MODEL começa com `gpt-*` ou `o1/o3/o4-*`.
  - **Anthropic API** (`ANTHROPIC_API_KEY`) — usada quando o MODEL começa com `claude-*`. Configuração premium atual usa Claude Opus 4.7 / Sonnet 4.6 / Haiku 4.5.

## Instalação

```bash
# 1) Criar e ativar um venv (recomendado)
python -m venv .venv
.venv\Scripts\activate              # Windows
# source .venv/bin/activate         # Linux/macOS

# 2) Instalar dependências Python
pip install -r requirements.txt

# 3) Instalar os browsers do Playwright (necessário para o scraping)
playwright install

# 4) Configurar credenciais
cp .env.example .env                # Linux/macOS
# copy .env.example .env            # Windows
# Edite .env e preencha: OPENAI_CREDENTIALS, EMAIL, PASSWORD
```

---

## Detalhes de cada script

### `scripts/obter_transcricoes_cursos.py`

Faz login na Alura via Playwright, navega pelas seções de cada curso informado e extrai a transcrição de cada vídeo.

- **Entrada:** IDs dos cursos (por `--carreira`/`--nivel` registrados em [`scripts/carreiras_niveis.py`](./scripts/carreiras_niveis.py) ou via `--ids`).
- **Saída:** JSON em [`trilha/`](./trilha/) no formato:
  ```json
  [
    {"id": 3713, "nome": "...", "link": "https://...", "transcricao": ["texto vídeo 1", "texto vídeo 2", ...]}
  ]
  ```
- Flags úteis: `--headful` (navegador visível para debug), `--listar` (mostra as carreiras/níveis já mapeados).

> Este script é uma **versão enxuta** do `get_course_transcription` do projeto `SCRAPING_FORMAÇÕES`, trazendo só o que importa para o fluxo de checkpoints. Para adicionar novas carreiras/níveis, edite o dicionário em [`scripts/carreiras_niveis.py`](./scripts/carreiras_niveis.py).

### `scripts/checkpoint_criar_resumos_cursos.py`

Lê os JSONs de transcrições da pasta [`trilha/`](./trilha/) e gera, para cada curso, um resumo de **conteúdos testáveis** (filtro qualitativo: só entra o que cabe virar questão de prova):

```json
{
  "tema_central": "frase curta sobre o que o curso aborda",
  "conteudos_testaveis": [
    {
      "topico": "Diferenciação entre data owner, steward e custodian",
      "nivel": "central",
      "tipo": "conceitual",
      "habilidade": "Atribuir responsabilidades a cada papel em cenário concreto",
      "evidencia_de_ensino": "Aula dedicada com organograma e exercício de classificação.",
      "armadilhas_comuns": ["Confundir owner com steward"]
    }
  ],
  "ferramentas_usadas": ["Excel", "Draw.io"]
}
```

- Uma chamada ao LLM por vídeo (com fallback de chunking para vídeos muito longos).
- Paralelismo por vídeo via `ThreadPoolExecutor` (ajuste `MAX_WORKERS` conforme rate limit).
- Provider escolhido pelo prefixo do MODEL (`gpt-*`/`o*-*` → OpenAI, `claude-*` → Anthropic).
- Configuração premium padrão: `claude-opus-4-7` (Anthropic). Alternativas comentadas no topo do script.
- Para Anthropic com ≥2 vídeos por curso: usa Message Batches API automaticamente (50% off). Cache também ativo (90% off em hits) — funciona apenas se o block estático atinge o mínimo (1024 tokens Sonnet/Opus, 2048 Haiku).

**Como usar:**

```bash
python scripts/checkpoint_criar_resumos_cursos.py --carreira governanca_de_dados --nivel 1
```

### `scripts/gerar_prova_teorica_do_zero.py`

Gera a **prova teórica** (múltipla escolha, 4 alternativas, com justificativas) a partir dos resumos do nível.

Fluxo interno:

1. Gera **ideias de questões** por curso (`MODEL_IDEAS`, default `claude-sonnet-4-6`).
2. Transforma cada ideia em **múltipla escolha** (`MODEL_FORMAT`, default `claude-sonnet-4-6`).
3. (Opcional) Ajusta o tamanho das alternativas com `--ajustar_alternativas`.
4. Ranqueia por **dificuldade** 1–5 (`MODEL_RANK`, default `claude-haiku-4-5-20251001`) e ordena ascendente.

Para Anthropic, fases 1 e 2 viram batch automaticamente quando há ≥2 cursos/questões. Flag `--no-batch` força sync.

Domínios: usa uma lista padrão de empresas fictícias (Bytebank, Serenatto, Freelando, etc.). Pode-se passar `--domains_arquivo caminho.json` para sobrescrever.

Exemplo (Governança de Dados, nível 1):

```bash
python scripts/gerar_prova_teorica_do_zero.py \
  --nivel 1 \
  --carreira "governanca_de_dados" \
  --resumos_arquivo output/checkpoints/resumos_governanca_de_dados_nivel_1.json \
  --max_questoes 20 --min_por_curso 2 --max_por_curso 3 \
  --domains_window 5 --ajustar_alternativas
```

### `scripts/gerar_prova_pratica_do_zero.py`

Gera a **prova prática** (Aula 3 do Checkpoint) em formato texto estruturado, com:

- Descrição do projeto, ambiente, 4 etapas com dificuldade crescente.
- Pergunta-chave, missão passo a passo e dicas de troubleshooting por etapa.
- Matriz de cobertura (auditoria) mapeando cada curso do nível às etapas.
- Datasets inline (CSV/JSON) **somente** quando a carreira envolver dados (heurística automática, override com `--modo_dados com|sem|auto`).
- Regra: **não** usa ferramentas de nuvem pagas (AWS, Azure, GCP etc.).
- **Perfil da carreira** (programatica vs conceitual): heurística baseada em % de `conteudos_testaveis` com `tipo: procedimental`. Em carreiras conceituais (ex.: governança), o system prompt orienta entregáveis documentais/diagramáticos.
- Modelo padrão: `claude-opus-4-7` (1 chamada apenas, sem batch).

```bash
python scripts/gerar_prova_pratica_do_zero.py \
  --nivel 1 \
  --carreira "governanca_de_dados" \
  --resumos_arquivo output/checkpoints/resumos_governanca_de_dados_nivel_1.json \
  --modo_dados auto --verbose
```

### `scripts/upload_checkpoint_alura.py`

Automação Playwright que cria seções e atividades direto no admin da Alura. Cobre 5 etapas distintas:

| Etapa CLI | O que faz |
|---|---|
| `criar_secoes` | Cria as 3 seções (Apresentação, Prova teórica, Prova prática) e marca a Prova teórica como `É prova?` |
| `marcar_prova_teorica` | Apenas marca o checkbox `É prova?` da seção Prova teórica (útil quando as seções já existem) |
| `criar_atividade_apresentacao` | Cria 1 atividade Explicação "Etapas do projeto" na seção Apresentação |
| `criar_atividades_prova_teorica` | Cria 1 atividade "Única escolha" por exercício do TXT da prova teórica |
| `criar_atividades_prova_pratica` | Cria 1 atividade Explicação por subtítulo da prova prática (Descrição, Antes de começar, Preparando o ambiente, 4 Etapas) + 1 Conclusão hardcoded |

Detalhes técnicos:

- O admin usa **EasyMDE/CodeMirror** em todos os campos de markdown — o script preenche via `CodeMirror.setValue()` + `.save()`.
- O dropdown de tipo é hierárquico (`select#chooseTask`); seleção via `data-task-enum` (HQ_EXPLANATION, SINGLE_CHOICE).
- Alternativas usam names HTML específicos (`alternatives[N].text`, `alternatives[N].opinion`, `alternatives[N].correct`).
- Login adapta-se a trackers lentos (usa `wait_until="domcontentloaded"` em vez de `load`).
- Flags úteis: `--limite N` (processa apenas os N primeiros — validação inicial), `--offset N` (pula os N primeiros — retomada após criação parcial), `--headless` (sem janela).

---

## Convenções

- **Nome dos arquivos de transcrição:** `<nome_da_carreira>_nivel_<1|2|3>.json` (ex.: `governanca_de_dados_nivel_1.json`). O script de scraping já grava seguindo esse padrão quando usado com `--carreira` + `--nivel`.
- **Nome dos arquivos de resumo:** `resumos_<nome_da_carreira>_nivel_<1|2|3>.json` — gerados automaticamente.
- **Linguagem neutra nas questões:** prefira "pessoa desenvolvedora", "a empresa te contratou"; nunca "você foi contratado" ou masculino genérico.
- **Fidelidade à aula:** nenhum dos scripts inventa conceitos — tudo é derivado dos resumos, que por sua vez vêm das transcrições.
- **Schema dos resumos:** filtro qualitativo (`conteudos_testaveis` apenas), não catalogação exaustiva. Cada item tem `topico, nivel, tipo, habilidade, evidencia_de_ensino, armadilhas_comuns`.
- **Switch de provider LLM:** definido pelo prefixo do MODEL no topo de cada script. `gpt-*`/`o1/o3/o4-*` → OpenAI; `claude-*` → Anthropic. Para Anthropic com várias chamadas, batch+cache automáticos.
