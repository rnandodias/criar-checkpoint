# CLAUDE.md

Contexto para assistentes de código (Claude Code / agents) que operem neste repositório.

## O que é este projeto

Pipeline para **gerar atividades de Checkpoint** (prova teórica e prova prática) dos níveis de carreira da Alura, de ponta a ponta. Começa no scraping das transcrições dos cursos e termina nos TXTs das provas.

Este projeto foi extraído/isolado a partir de dois projetos maiores do usuário (`Tarefas` e `scraping_formações`), trazendo **somente** o que diz respeito à geração das provas de checkpoint. Os dois projetos originais permanecem intactos como referência.

## Pipeline (4 etapas)

```text
obter_transcricoes_cursos.py      →  trilha/<carreira>_nivel_<n>.json
checkpoint_criar_resumos_cursos.py →  output/checkpoints/resumos_<carreira>_nivel_<n>.json
gerar_prova_teorica_do_zero.py    →  output/cursos_checkpoint/prova_teorica_<slug>_nivel_<n>.txt
gerar_prova_pratica_do_zero.py    →  output/cursos_checkpoint/prova_pratica_<slug>_nivel_<n>.txt
```

Cada script consome a saída do anterior. Os resumos são a **fonte única de verdade** para a geração das provas — nenhuma etapa posterior lê as transcrições diretamente.

## Stack

- **Python 3.10+**
- `openai==1.102.0` (provas + resumos) · `python-dotenv`
- `playwright==1.51.0` (scraping) · `beautifulsoup4` · `tqdm` · `requests`
- Credenciais no `.env`: `OPENAI_CREDENTIALS`, `EMAIL`, `PASSWORD`

## Convenções importantes (ler antes de editar)

- **Nomenclatura dos arquivos:** `<carreira>_nivel_<1|2|3>.json` (ex.: `governanca_de_dados_nivel_1.json`). Todos os scripts assumem esse padrão.
- **Fidelidade à aula:** os scripts de geração (prova teórica/prática) **não podem inventar** conceitos, ferramentas ou técnicas que não apareçam nos resumos. Essa restrição está codificada nos prompts — não relaxar.
- **Linguagem neutra nas questões:** "pessoa desenvolvedora", "a empresa te contratou". **Nunca** "você foi contratado" nem masculino genérico.
- **Sem nuvem paga nas provas práticas:** AWS, Azure, GCP e derivados estão proibidos (gera custo para o aluno). Regra presente no system prompt de `gerar_prova_pratica_do_zero.py`.
- **Datasets inline:** só aparecem na prova prática quando a carreira envolve dados (heurística automática, override via `--modo_dados com|sem|auto`). CSVs/JSONs devem ter 30–120 linhas — há pós-processamento local que corta/amplia.

## Estrutura

```text
scripts/
├── _scraping_utils.py           # limpar_texto (helper interno)
├── carreiras_niveis.py          # mapa carreira/nível → IDs dos cursos
├── obter_transcricoes_cursos.py # 1) Playwright + Alura
├── checkpoint_criar_resumos_cursos.py  # 2) resumos via OpenAI
├── gerar_prova_teorica_do_zero.py      # 3) múltipla escolha (4 fases)
└── gerar_prova_pratica_do_zero.py      # 4) Aula 3 (TXT estruturado)
trilha/            # entrada: transcrições
output/
├── checkpoints/        # resumos
└── cursos_checkpoint/  # provas finais
```

## Comandos úteis

```bash
# Listar carreiras/níveis já mapeados
python scripts/obter_transcricoes_cursos.py --listar

# Pipeline completo para governança de dados nível 1
python scripts/obter_transcricoes_cursos.py --carreira governanca_de_dados --nivel 1
# (editar INPUT_FILES no próximo script antes de rodar)
python scripts/checkpoint_criar_resumos_cursos.py
python scripts/gerar_prova_teorica_do_zero.py --nivel 1 --carreira "Governança de Dados" \
  --resumos_arquivo output/checkpoints/resumos_governanca_de_dados_nivel_1.json \
  --max_questoes 10 --min_por_curso 1 --max_por_curso 2 --domains_window 3
python scripts/gerar_prova_pratica_do_zero.py --nivel 1 --carreira "Governança de Dados" \
  --resumos_arquivo output/checkpoints/resumos_governanca_de_dados_nivel_1.json
```

## Diretrizes para quem for editar o código

- Adicionar uma nova carreira/nível: editar o dicionário em `scripts/carreiras_niveis.py`. Não duplicar IDs em múltiplos lugares.
- Trocar modelo da OpenAI: as constantes ficam no topo de cada script (`MODEL`, `MODEL_IDEAS`, `MODEL_FORMAT`, `MODEL_RANK`, `MODEL_GEN`). Ao baixar para `gpt-4o-mini`, também reduzir `SINGLE_PASS_CHAR_LIMIT` / `CHUNK_SIZE` (já há um bloco comentado em `checkpoint_criar_resumos_cursos.py` com valores sugeridos).
- Mexer nos **prompts** de geração de prova é uma mudança de alto impacto — valide com o usuário antes. Os prompts estão em funções `system_prompt_*` / `user_prompt_*` e foram iterados com base em incidentes reais (alternativas muito longas, questões fora de escopo, etc.).
- O parser de questões teóricas (`_parse_exercise_ideas_verbatim`) depende do formato exato retornado pelo LLM. Se mudar o template de saída no user prompt, ajustar o parser também.
- Os scripts não são uma biblioteca — são CLIs. Não introduzir camadas de abstração "for future use".

## Onde buscar contexto adicional

- `README.md` → onboarding humano (instalação, fluxo, exemplos).
- Comentários no topo de cada script → detalhes específicos daquele passo.
- Os projetos originais `../Tarefas/` e `../scraping_formações/` não devem ser modificados — usar só como consulta se precisar entender a origem de algum trecho.
