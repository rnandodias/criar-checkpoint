# CLAUDE.md

Contexto para assistentes de código (Claude Code / agents) que operem neste repositório.

## O que é este projeto

Pipeline para **gerar atividades de Checkpoint** (prova teórica e prova prática) dos níveis de carreira da Alura, de ponta a ponta. Começa coletando as transcrições dos cursos via **API oficial de cursos da Alura**, gera as provas com LLM e publica no admin da plataforma via automação Playwright.

Este projeto foi extraído/isolado a partir de dois projetos maiores do usuário (`Tarefas` e `scraping_formações`), trazendo **somente** o que diz respeito à geração das provas de checkpoint. Os dois projetos originais permanecem intactos como referência.

## Pipeline (5 etapas principais + 2 revisões automáticas + 1 handoff pré-publicação)

```text
1)   obter_transcricoes_cursos.py     →  trilha/<carreira>_nivel_<n>.json         (API Alura de cursos)
2)   checkpoint_criar_resumos_cursos.py → output/<slug>_nivel_<n>/resumos.json
3)   gerar_prova_teorica_do_zero.py    → output/<slug>_nivel_<n>/prova_teorica.txt
3.5) revisar_prova_teorica.py          → sobrescreve prova_teorica.txt + backup .pre_revisao.txt + relatorio.md
4)   gerar_prova_pratica_do_zero.py    → output/<slug>_nivel_<n>/prova_pratica.txt
4.5) revisar_prova_pratica.py          → sobrescreve prova_pratica.txt + backup .pre_revisao.txt + relatorio.md
4.9) empacotar_para_coordenador.py     → instrucoes_coordenador.txt + revisao_coordenador_*.zip (handoff, sem LLM)
5)   upload_checkpoint_alura.py        → publica seções/atividades no admin Alura (Playwright)
```

A etapa **4.9** é o handoff pré-publicação: gera um documento de instruções para o coordenador (folha de seleção das 10 questões teóricas + pendências da prática extraídas dos relatórios de QA) e empacota provas + relatórios num ZIP. É determinística (não usa LLM); roda depois do QA e antes do upload. O coordenador revisa/aprova o ZIP e só então roda-se a Etapa 5.

Cada script consome a saída do anterior. Os resumos são a **fonte única de verdade** para a geração das provas — nenhuma etapa posterior lê as transcrições diretamente.

## Stack

- **Python 3.10+**
- `openai==1.102.0` + `anthropic==0.97.0` (provas + resumos — provider escolhido pelo prefixo do MODEL: `gpt-*`/`o*-*` → OpenAI, `claude-*` → Anthropic)
- `python-dotenv`
- `requests` + `tqdm` (etapa 1 — chamadas à API de cursos da Alura)
- `playwright==1.51.0` (etapa 5 — upload no admin, EasyMDE/CodeMirror)
- Credenciais no `.env`:
  - `ALURA_API_TOKEN` → **etapa 1** (API oficial de cursos)
  - `OPENAI_CREDENTIALS` ou `ANTHROPIC_API_KEY` → etapas 2, 3, 4 (LLMs)
  - `EMAIL`, `PASSWORD` → **etapa 5** apenas (login Playwright no admin)

## Convenções importantes (ler antes de editar)

- **Nomenclatura dos arquivos:** `<carreira>_nivel_<1|2|3>.json` (ex.: `governanca_de_dados_nivel_1.json`). Todos os scripts assumem esse padrão.
- **Schema dos resumos (etapa 2):** `{tema_central, conteudos_testaveis[], ferramentas_usadas[]}`. Cada `conteudo_testavel` é `{topico, nivel: central|complementar, tipo: conceitual|procedimental, habilidade, evidencia_de_ensino, armadilhas_comuns[]}`. Esse schema é **filtro qualitativo** — o LLM só inclui o que cabe virar questão de prova (não cataloga toda menção da aula).
- **Fidelidade à aula:** os scripts de geração (prova teórica/prática) **não podem inventar** conceitos, ferramentas ou técnicas que não apareçam nos resumos. Essa restrição está codificada nos prompts — não relaxar.
- **Linguagem neutra nas questões:** "pessoa desenvolvedora", "a empresa te contratou". **Nunca** "você foi contratado" nem masculino genérico.
- **Sem nuvem paga nas provas práticas:** AWS, Azure, GCP e derivados estão proibidos (gera custo para o aluno). Regra presente no system prompt de `gerar_prova_pratica_do_zero.py`.
- **Perfil da carreira (etapa 4):** heurística `_perfil_carreira` classifica a carreira como `programatica` ou `conceitual` baseado em % de `conteudos_testaveis` com `tipo: procedimental` (`> 50%` → programática). Se conceitual, system prompt orienta entregáveis documentais/diagramáticos. Override manual via flag `--perfil auto|programatica|conceitual` (default `auto` = heurística) — útil para carreiras híbridas que caem no lado errado do limiar. **Ao forçar o perfil na etapa 4, passe o mesmo `--perfil` na etapa 4.5** (`revisar_prova_pratica.py`): ele alimenta a ótica do revisor cego e o rerun do escape hatch; sem isso o revisor avaliaria a prova sob o perfil errado.
- **Datasets inline:** só aparecem na prova prática quando a carreira envolve dados (heurística automática, override via `--modo_dados com|sem|auto`). CSVs/JSONs devem ter 30–120 linhas — há pós-processamento local que corta/amplia.

## Estrutura

```text
scripts/
├── _scraping_utils.py                     # legado — não importado por nenhum script ativo
├── carreiras_niveis.py                    # mapa carreira/nível → IDs dos cursos
├── obter_transcricoes_cursos.py           # 1) API oficial de cursos da Alura
├── checkpoint_criar_resumos_cursos.py     # 2) resumos via LLM
├── gerar_prova_teorica_do_zero.py         # 3) múltipla escolha (4 fases)
├── revisar_prova_teorica.py               # 3.5) QA + auto-correção (variantes 2 e 3 automáticas)
├── gerar_prova_pratica_do_zero.py         # 4) Aula 3 (TXT estruturado)
├── revisar_prova_pratica.py               # 4.5) QA + teste de resolvedor + auto-correção
├── empacotar_para_coordenador.py          # 4.9) handoff: instruções + ZIP p/ revisão do coordenador (sem LLM)
└── upload_checkpoint_alura.py             # 5) publica no admin Alura (Playwright)
trilha/            # entrada: transcrições
output/
└── <slug>_nivel_<n>/                      # 1 pasta por projeto (carreira + nível)
    ├── resumos.json                       #   etapa 2
    ├── resumos.jsonl
    ├── prova_teorica.txt                  #   etapa 3 (sobrescrito pela 3.5 se houver correções)
    ├── prova_teorica.pre_revisao.txt      #   3.5 backup (só existe se algo foi corrigido)
    ├── prova_teorica_relatorio.md         #   3.5 saída
    ├── prova_pratica.txt                  #   etapa 4 (sobrescrito pela 4.5)
    ├── prova_pratica.pre_revisao.txt      #   4.5 backup
    ├── prova_pratica_relatorio.md         #   4.5 saída
    ├── _reforco_*.txt                     #   gerado pelo escape hatch (variante 3) — pode apagar após rerun
    ├── instrucoes_coordenador.txt         #   4.9 folha de decisão do coordenador
    └── revisao_coordenador_<slug>_nivel_<n>.zip  #   4.9 pacote (instruções + provas + relatórios)
```

## Comandos úteis

```bash
# Listar carreiras/níveis já mapeados
python scripts/obter_transcricoes_cursos.py --listar

# Pipeline completo para governança de dados nível 1
# (resumos_arquivo é opcional — se omitido, deriva de output/<slug>_nivel_<n>/resumos.json)
python scripts/obter_transcricoes_cursos.py --carreira governanca_de_dados --nivel 1
python scripts/checkpoint_criar_resumos_cursos.py --carreira governanca_de_dados --nivel 1
python scripts/gerar_prova_teorica_do_zero.py --nivel 1 --carreira "Governança de Dados" \
  --max_questoes 20 --min_por_curso 1 --max_por_curso 3 --domains_window 3
python scripts/revisar_prova_teorica.py --carreira "Governança de Dados" --nivel 1
python scripts/gerar_prova_pratica_do_zero.py --nivel 1 --carreira "Governança de Dados" --batch
python scripts/revisar_prova_pratica.py --carreira "Governança de Dados" --nivel 1
# Handoff pré-publicação: gera instrucoes_coordenador.txt + ZIP para revisão do coordenador
python scripts/empacotar_para_coordenador.py --carreira "Governança de Dados" --nivel 1

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
- **Estrutura de output/**: uma pasta por projeto (`output/<slug>_nivel_<n>/`). O slug vem de `_slugify(carreira)` aplicado ao nome oficial da carreira (o mesmo em todos os scripts). Todos os scripts (etapas 2, 3, 3.5, 4, 4.5, 5) leem e escrevem nessa pasta.
- **Modelo padrão atual:** Opus 4-6 em TODAS as etapas LLM (`MODEL`, `MODEL_IDEAS`, `MODEL_FORMAT`, `MODEL_GEN`, `MODEL_RANK`, e `MODEL_REVISOR` nas etapas 3.5/4.5). Preferência do usuário registrada em memória — usar outro modelo só se for pedido explicitamente. **Não voltar para Opus 4-8** (gera enunciados curtos demais na teórica — decisão do usuário). **Provider detectado pelo prefixo**: `gpt-*` ou `o1/o3/o4-*` → OpenAI, `claude-*` → Anthropic. Modelos sem suporte a `temperature` customizada (gpt-5, o-series, claude-opus-4-7+, claude-opus-4-8, claude-opus-5) são tratados automaticamente em `_model_supports_temperature`.
- **Cache + Batch Anthropic:**
  - **Etapa 2** (resumos) e **etapa 3** (teórica, fases 1 e 2): Message Batches API (50% off) **automática** quando MODEL é Anthropic e a etapa tem ≥2 chamadas. Cache (90% off em hits) sempre ativo — funciona se os blocks estáticos atingirem 1024 tokens (Sonnet/Opus) ou 2048 (Haiku). Flag `--no-batch` força sync (debug).
  - **Etapa 4** (prática): 1 chamada única. Batch é **opt-in** via `--batch` (útil pra economizar 50% em runs não urgentes; latência sobe pra 5-30 min).
  - **Etapa 3 fases 3 e 4** continuam sync (loop iterativo + 1 chamada de ranking); refactor pra batch tem ROI baixo.
- Mexer nos **prompts** de geração de prova é uma mudança de alto impacto — valide com o usuário antes. Os prompts estão em funções `system_prompt_*` / `user_prompt_*` e foram iterados com base em incidentes reais (alternativas muito longas, questões fora de escopo, etc.).
- **Parser da teórica** (`_parse_exercise_ideas_verbatim`) tolera: markdown bold/itálico nos marcadores, cabeçalhos markdown (`##`), e sinônimos comuns (`Enunciado`/`Pergunta`/`Questão` = `Texto da questão`; `Resolução`/`Solução`/`Resposta correta` = `Resposta`; `Conceito`/`Tópico` = `Conceito abordado`). O prompt em `_ask_exercise_ideas` impõe o formato estrito; o parser é rede de segurança.
- O `upload_checkpoint_alura.py` usa Playwright + JS evaluate para EasyMDE/CodeMirror (textareas escondidos no admin). Seletores chave: `select#chooseTask` (tipo de atividade, hierárquico), `input.add-alternative[data-type='emptySingleAlternative']`, `textarea[name="alternatives[N].text"]`, `textarea[name="alternatives[N].opinion"]`, `input[type="radio"][name="alternatives[N].correct"]`.
- **Flag `--indices` no uploader** (`criar_atividades_prova_teorica`): lista CSV 1-based (ex.: `--indices 1,5`) para re-subir apenas exercícios específicos após um run parcial. Aplicado antes de `--offset`/`--limite`.
- **Etapas 3.5 e 4.5 (revisão automática)**:
  - Rodam Opus 4-6 contra o TXT gerado + resumos + contexto (nível, ferramentas, perfil).
  - **Variante 2 (padrão)**: auto-corrigem exercícios/seções individuais quando o issue é mecânico. Fazem 1 tentativa por item; se falhar, marcam no relatório para revisão humana.
  - **Variante 3 (escape hatch automático)**: se ≥50% (teórica) ou ≥3 seções (prática) tiverem issues da mesma categoria, o revisor gera um "reforço de prompt" específico, salva em `_reforco_*.txt` e chama `subprocess.run(...gerar_prova_*...)` com `--reforco_extra <arquivo>`. O gerador concatena esse reforço ao system prompt. O rerun é limitado a 1 nível — flag interna `--nested` bloqueia rerun aninhado.
  - **Flag `--reforco_extra` nos geradores** (`gerar_prova_teorica_do_zero.py` e `gerar_prova_pratica_do_zero.py`): recebe caminho de arquivo TXT com um bloco de instruções extras que é concatenado ao final do system prompt via `_apply_reforco()` (teórica) ou concatenação direta (prática). Uso normal: só pelo revisor no escape hatch.
  - Saídas: `<prova>_relatorio.md` (sempre gerado) e `<prova>.pre_revisao.txt` (backup do original, só gerado se algo foi alterado).
- Os scripts não são uma biblioteca — são CLIs. Não introduzir camadas de abstração "for future use".

## Onde buscar contexto adicional

- `README.md` → onboarding humano (instalação, fluxo, exemplos).
- Comentários no topo de cada script → detalhes específicos daquele passo.
- Os projetos originais `../Tarefas/` e `../scraping_formações/` não devem ser modificados — usar só como consulta se precisar entender a origem de algum trecho.
