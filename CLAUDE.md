# CLAUDE.md

Contexto para assistentes de código (Claude Code / agents) que operem neste repositório.

## O que é este projeto

Pipeline para **gerar atividades de Checkpoint** (prova teórica e prova prática) dos níveis de carreira da Alura, de ponta a ponta. Começa no scraping das transcrições dos cursos, gera as provas com LLM e publica no admin da plataforma via automação.

Este projeto foi extraído/isolado a partir de dois projetos maiores do usuário (`Tarefas` e `scraping_formações`), trazendo **somente** o que diz respeito à geração das provas de checkpoint. Os dois projetos originais permanecem intactos como referência.

## Pipeline (5 etapas)

```text
1) obter_transcricoes_cursos.py      →  trilha/<carreira>_nivel_<n>.json
2) checkpoint_criar_resumos_cursos.py →  output/checkpoints/resumos_<carreira>_nivel_<n>.json
3) gerar_prova_teorica_do_zero.py    →  output/cursos_checkpoint/prova_teorica_<slug>_nivel_<n>.txt
4) gerar_prova_pratica_do_zero.py    →  output/cursos_checkpoint/prova_pratica_<slug>_nivel_<n>.txt
5) upload_checkpoint_alura.py        →  publica seções/atividades no admin Alura (Playwright)
```

Cada script consome a saída do anterior. Os resumos são a **fonte única de verdade** para a geração das provas — nenhuma etapa posterior lê as transcrições diretamente.

## Stack

- **Python 3.10+**
- `openai==1.102.0` + `anthropic==0.97.0` (provas + resumos — provider escolhido pelo prefixo do MODEL: `gpt-*`/`o*-*` → OpenAI, `claude-*` → Anthropic)
- `python-dotenv`
- `playwright==1.51.0` (scraping + upload no admin) · `beautifulsoup4` · `tqdm` · `requests`
- Credenciais no `.env`: `OPENAI_CREDENTIALS`, `ANTHROPIC_API_KEY` (opcional, só se usar Claude), `EMAIL`, `PASSWORD`

## Convenções importantes (ler antes de editar)

- **Nomenclatura dos arquivos:** `<carreira>_nivel_<1|2|3>.json` (ex.: `governanca_de_dados_nivel_1.json`). Todos os scripts assumem esse padrão.
- **Schema dos resumos (etapa 2):** `{tema_central, conteudos_testaveis[], ferramentas_usadas[]}`. Cada `conteudo_testavel` é `{topico, nivel: central|complementar, tipo: conceitual|procedimental, habilidade, evidencia_de_ensino, armadilhas_comuns[]}`. Esse schema é **filtro qualitativo** — o LLM só inclui o que cabe virar questão de prova (não cataloga toda menção da aula).
- **Fidelidade à aula:** os scripts de geração (prova teórica/prática) **não podem inventar** conceitos, ferramentas ou técnicas que não apareçam nos resumos. Essa restrição está codificada nos prompts — não relaxar.
- **Linguagem neutra nas questões:** "pessoa desenvolvedora", "a empresa te contratou". **Nunca** "você foi contratado" nem masculino genérico.
- **Sem nuvem paga nas provas práticas:** AWS, Azure, GCP e derivados estão proibidos (gera custo para o aluno). Regra presente no system prompt de `gerar_prova_pratica_do_zero.py`.
- **Perfil da carreira (etapa 4):** heurística `_perfil_carreira` classifica a carreira como `programatica` ou `conceitual` baseado em % de `conteudos_testaveis` com `tipo: procedimental`. Se conceitual, system prompt orienta entregáveis documentais/diagramáticos.
- **Datasets inline:** só aparecem na prova prática quando a carreira envolve dados (heurística automática, override via `--modo_dados com|sem|auto`). CSVs/JSONs devem ter 30–120 linhas — há pós-processamento local que corta/amplia.

## Estrutura

```text
scripts/
├── _scraping_utils.py                     # limpar_texto (helper interno)
├── carreiras_niveis.py                    # mapa carreira/nível → IDs dos cursos
├── obter_transcricoes_cursos.py           # 1) Playwright + Alura
├── checkpoint_criar_resumos_cursos.py     # 2) resumos via LLM (OpenAI ou Anthropic)
├── gerar_prova_teorica_do_zero.py         # 3) múltipla escolha (4 fases)
├── gerar_prova_pratica_do_zero.py         # 4) Aula 3 (TXT estruturado)
└── upload_checkpoint_alura.py             # 5) publica no admin Alura (Playwright)
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
python scripts/checkpoint_criar_resumos_cursos.py --carreira governanca_de_dados --nivel 1
python scripts/gerar_prova_teorica_do_zero.py --nivel 1 --carreira "Governança de Dados" \
  --resumos_arquivo output/checkpoints/resumos_governanca_de_dados_nivel_1.json \
  --max_questoes 20 --min_por_curso 1 --max_por_curso 3 --domains_window 3
python scripts/gerar_prova_pratica_do_zero.py --nivel 1 --carreira "Governança de Dados" \
  --resumos_arquivo output/checkpoints/resumos_governanca_de_dados_nivel_1.json

# Publicação no admin Alura (precisa do <curso_id> do checkpoint na URL /admin/courses/v2/<id>)
python scripts/upload_checkpoint_alura.py --curso_id 5256 --etapa criar_secoes
python scripts/upload_checkpoint_alura.py --curso_id 5256 --etapa criar_atividade_apresentacao --nivel 1
python scripts/upload_checkpoint_alura.py --curso_id 5256 --etapa criar_atividades_prova_teorica \
  --carreira "Governança de Dados" --nivel 1
python scripts/upload_checkpoint_alura.py --curso_id 5256 --etapa criar_atividades_prova_pratica \
  --carreira "Governança de Dados" --nivel 1
```

## Diretrizes para quem for editar o código

- Adicionar uma nova carreira/nível: editar o dicionário em `scripts/carreiras_niveis.py`. Não duplicar IDs em múltiplos lugares.
- Trocar modelo: as constantes ficam no topo de cada script (`MODEL`, `MODEL_IDEAS`, `MODEL_FORMAT`, `MODEL_RANK`, `MODEL_GEN`). O **provider é detectado pelo prefixo**: `gpt-*` ou `o1/o3/o4-*` → OpenAI, `claude-*` → Anthropic. Modelos sem suporte a `temperature` customizada (gpt-5, o-series, claude-opus-4-7+) são tratados automaticamente em `_model_supports_temperature`.
- **Cache + Batch Anthropic:** quando o MODEL é Anthropic e a etapa tem ≥2 chamadas (etapa 2, etapa 3 fases 1/2), o script usa Message Batches API (50% off) automaticamente. Cache (90% off em hits) é sempre ativo nas chamadas Anthropic — funciona apenas se os blocks estáticos atingirem o mínimo de 1024 tokens (Sonnet/Opus) ou 2048 (Haiku). Flag `--no-batch` força sync (apenas para debug).
- Mexer nos **prompts** de geração de prova é uma mudança de alto impacto — valide com o usuário antes. Os prompts estão em funções `system_prompt_*` / `user_prompt_*` e foram iterados com base em incidentes reais (alternativas muito longas, questões fora de escopo, etc.).
- O parser de questões teóricas (`_parse_exercise_ideas_verbatim`) tolera markdown bold/itálico (Sonnet 4.6 retorna `**Exercício 1 - X**`). Se mudar o template de saída, validar.
- O `upload_checkpoint_alura.py` usa Playwright + JS evaluate para EasyMDE/CodeMirror (textareas escondidos no admin). Seletores chave: `select#chooseTask` (tipo de atividade, hierárquico), `input.add-alternative[data-type='emptySingleAlternative']`, `textarea[name="alternatives[N].text"]`, `textarea[name="alternatives[N].opinion"]`, `input[type="radio"][name="alternatives[N].correct"]`.
- Os scripts não são uma biblioteca — são CLIs. Não introduzir camadas de abstração "for future use".

## Onde buscar contexto adicional

- `README.md` → onboarding humano (instalação, fluxo, exemplos).
- Comentários no topo de cada script → detalhes específicos daquele passo.
- Os projetos originais `../Tarefas/` e `../scraping_formações/` não devem ser modificados — usar só como consulta se precisar entender a origem de algum trecho.
