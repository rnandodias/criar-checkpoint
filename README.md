# criar-checkpoint

Projeto isolado para **gerar atividades de Checkpoint** (prova teórica e prova prática) dos níveis de carreira da Alura, de ponta a ponta:

1. **Coleta** as transcrições dos vídeos dos cursos diretamente da plataforma (scraping).
2. **Resume** cada curso em um schema estruturado.
3. **Gera** a prova teórica (múltipla escolha) e a prova prática (Aula 3 do Checkpoint).

Todo o fluxo acontece neste único projeto — não é mais necessário movimentar arquivos manualmente entre repositórios.

---

## Fluxo completo (ponta a ponta)

```text
┌──────────────────────────────┐  JSON de transcrições   ┌────────────────────────────┐  JSON resumo  ┌──────────────────────────┐
│ 1) obter_transcricoes_cursos │ ───────────────────────▶│ 2) checkpoint_criar_resumos│ ────────────▶ │ 3) gerar_prova_teorica   │
│    (Playwright + Alura)      │   trilha/<nome>.json    │    _cursos (OpenAI)        │  output/...   │    gerar_prova_pratica   │
└──────────────────────────────┘                         └────────────────────────────┘               └──────────────────────────┘
```

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

- Edite a variável `INPUT_FILES` no topo de [`scripts/checkpoint_criar_resumos_cursos.py`](./scripts/checkpoint_criar_resumos_cursos.py) para apontar para o arquivo gerado na etapa 1.
- Rode:

```bash
python scripts/checkpoint_criar_resumos_cursos.py
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
│   └── gerar_prova_pratica_do_zero.py        # 4) prova prática (Aula 3)
├── trilha/                          # saída da etapa 1 (entrada da etapa 2)
└── output/
    ├── checkpoints/                 # saída da etapa 2 (entrada das etapas 3 e 4)
    └── cursos_checkpoint/           # saída final: provas teórica e prática
```

---

## Pré-requisitos

- Python 3.10+
- Conta ativa na plataforma da Alura (email + senha) — para o scraping.
- Chave válida da **OpenAI API** — para os resumos e geração das provas.

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

Lê os JSONs de transcrições da pasta [`trilha/`](./trilha/) e gera, para cada curso, um resumo estruturado:

```json
{
  "objetivos": [...],
  "topicos": [...],
  "habilidades": [...],
  "ferramentas_ou_bibliotecas": [...],
  "conceitos_chave": [...],
  "exemplos_relevantes": [...],
  "erros_ou_armadilhas_comuns": [...]
}
```

- Uma chamada ao LLM por vídeo (com fallback de chunking para vídeos muito longos).
- Paralelismo por vídeo via `ThreadPoolExecutor` (ajuste `MAX_WORKERS` conforme rate limit).
- Modelo padrão: `gpt-4o` (pode trocar para `gpt-4o-mini` editando as constantes do topo).

**Como usar:** edite `INPUT_FILES` no topo do script para listar os arquivos a processar e execute:

```bash
python scripts/checkpoint_criar_resumos_cursos.py
```

### `scripts/gerar_prova_teorica_do_zero.py`

Gera a **prova teórica** (múltipla escolha, 4 alternativas, com justificativas) a partir dos resumos do nível.

Fluxo interno:

1. Gera **ideias de questões** por curso (`gpt-4o`).
2. Transforma cada ideia em **múltipla escolha** (`gpt-4o-2024-08-06`).
3. (Opcional) Ajusta o tamanho das alternativas com `--ajustar_alternativas`.
4. Ranqueia por **dificuldade** 1–5 (`gpt-4o-mini`) e ordena ascendente.

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

```bash
python scripts/gerar_prova_pratica_do_zero.py \
  --nivel 1 \
  --carreira "governanca_de_dados" \
  --resumos_arquivo output/checkpoints/resumos_governanca_de_dados_nivel_1.json \
  --modo_dados auto --verbose
```

---

## Convenções

- **Nome dos arquivos de transcrição:** `<nome_da_carreira>_nivel_<1|2|3>.json` (ex.: `governanca_de_dados_nivel_1.json`). O script de scraping já grava seguindo esse padrão quando usado com `--carreira` + `--nivel`.
- **Nome dos arquivos de resumo:** `resumos_<nome_da_carreira>_nivel_<1|2|3>.json` — gerados automaticamente.
- **Linguagem neutra nas questões:** prefira "pessoa desenvolvedora", "a empresa te contratou"; nunca "você foi contratado" ou masculino genérico.
- **Fidelidade à aula:** nenhum dos scripts inventa conceitos — tudo é derivado dos resumos, que por sua vez vêm das transcrições.
