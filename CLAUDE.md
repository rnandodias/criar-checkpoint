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
4.7) turbinar_dataset_pratica.py       → gerar_dataset.py + dataset/*.csv (só se a prática tiver dataset)
4.9) empacotar_para_coordenador.py     → instrucoes_coordenador.txt + revisao_coordenador_*.zip (handoff, sem LLM)
5)   upload_checkpoint_alura.py        → publica seções/atividades no admin Alura (Playwright)
```

A etapa **4.9** é o handoff pré-publicação: gera um documento de instruções para o coordenador (folha de seleção das 10 questões teóricas + pendências da prática extraídas dos relatórios de QA) e empacota provas + relatórios num ZIP. É determinística (não usa LLM) e **ciente do formato da prática** (`_detectar_formato_pratica` → cases vs projeto: não menciona datasets/CSV numa prova cases, e só cita relatórios de QA se os `.md` existirem no pacote); roda depois do QA e antes do upload. O coordenador revisa/aprova o ZIP e só então roda-se a Etapa 5.

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
- **Datasets inline:** só aparecem na prova prática quando a carreira envolve dados (heurística automática, override via `--modo_dados com|sem|auto`). CSVs/JSONs devem ter 30–120 linhas — há pós-processamento local (`_extend_or_trim_csv`/`_extend_or_trim_json_records`) que corta acima de 120 e amplia abaixo de 30. **A ampliação duplica linhas inteiras** (preservando a correlação entre colunas) e varia só colunas-chave (valores únicos, ex.: um id/código); datasets **sem** coluna-chave (tabelas de referência pequenas) são mantidos coerentes mesmo abaixo de 30, em vez de inflados. Não recombina campos entre colunas (isso corrompia tabelas de referência — bug corrigido em 2026-07).
- **Formato da prova prática — `--formato projeto|cases` (etapa 4):** `projeto` (default) = projeto prático de implementação (fluxo testado, `system_prompt_aula3_txt`). `cases` = prova de **ANÁLISE DE CASES** para carreiras conceituais/analíticas, via `system_prompt_aula3_cases_txt` + `user_prompt_aula3_cases_txt` (funções **isoladas** — não tocam no prompt de projeto). O formato cases gera **um case único evolutivo** (sem código/datasets), entregáveis documentais como **sugestões** (não checklist), **análise aberta** (sem gabarito — traz "O que caracteriza uma boa análise" em cada etapa) e **IA como copiloto crítico** por etapa. Reusa o mesmo esqueleto de seções, então a etapa 5 publica sem alterar o parser. Combine com `--perfil conceitual --modo_dados sem`. Estreou em Arquitetura de Soluções com IA n1 (2026-07). No QA cego dessa prova, use **rubrica de ANÁLISE** (coerência do case entre etapas, qualidade das tarefas, IA-copiloto calibrada, sem gabarito vazado), NÃO a de executabilidade de dataset — e a **cobertura dos cursos é avaliada por quem tem os resumos** (o revisor cego não os vê).
  - **Cases em carreira técnica → exija ARTEFATOS TRANSCRITOS (padrão validado).** Em carreiras de infra/ops o formato cases degenera em ensaio genérico sobre boas práticas se as tarefas não tiverem sobre o que morder. A técnica que funciona (DevOps n2 e os três níveis de IA aplicada a Operações e Infraestrutura) é passar um `--reforco_extra` exigindo **6-9 artefatos técnicos transcritos integralmente no enunciado** — YAML, código, consultas, tabelas de números, logs, documentos mal escritos — com **defeitos plantados de gravidade variada**, incluindo ao menos um trecho **correto que parece suspeito** (pune a crítica automática). A pessoa lê, diagnostica e propõe correção como especificação documental; nunca executa nada.
  - **A regra antivazamento precisa ser explícita e enumerada.** Só dizer "não sinalize os defeitos" **não basta** — o gerador, por didatismo, lista os defeitos plantados nas missões e na seção "O que caracteriza uma boa análise", transformando diagnóstico em transcrição. A formulação que funcionou: *"NÃO enumere os defeitos nas missões, nas dicas ou na seção 'O que caracteriza uma boa análise' — descrever o que procurar é aceitável; entregar a lista do que está errado, não."* Confira isso na leitura do QA mesmo com o reforço aplicado.
  - **Case com números exige conferência aritmética manual.** Numa prova de custo/capacidade, tabela que não fecha inviabiliza a tarefa. Recalcule: subtotais somam o total; valores derivados batem com a fórmula declarada; e os artefatos são **coerentes entre si** (o que um relatório agrega tem de bater com o que a tabela de detalhe soma; capacidade declarada tem de comportar os requests declarados).

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

## Etapa 4.7 — turbinador de dataset (opcional, só quando a prática tem dados)

Roda **entre a 4.5 e a 4.9**, e só quando a prova prática contém bloco ```csv/```json — em provas
`--formato cases` sai sem fazer nada. Existe porque o dataset inline que o LLM escreve na etapa 4 tem
30-120 linhas e nenhuma estrutura estatística real: não dá para treinar, validar nem detectar
overfitting com ele.

**Divisão de trabalho — o LLM especifica, o código gera e valida:**
1. LLM lê enunciado + dataset proposto → devolve uma **especificação** JSON (colunas, distribuições,
   fórmula do alvo, defeitos declarados, chaves entre arquivos). Não escreve dados.
2. Código gera `gerar_dataset.py` (determinístico, semente fixa) e o executa.
3. Código **valida**: esquema, volume, proporção de nulos, integridade referencial, e principalmente
   se os efeitos declarados **aparecem** nos dados e na **hierarquia** correta.
4. LLM faz checagem semântica de escopo estreito (valores plausíveis? coerente com o enunciado?).
5. Só então substitui o bloco inline por: marcador de link + dicionário de colunas + amostra.

Qualquer reprovação **preserva o TXT**. Use `--dry-run` para gerar e conferir sem alterar nada.

**PLAUSIBILIDADE CAUSAL é o ponto central.** Não basta a correlação existir: ela precisa ter o sinal e
a força que a intuição do domínio espera. A demanda de ontem deve prever a de hoje melhor que a
temperatura; chuva reduz circulação; distância aumenta tempo. Se os dados contradizem isso, a análise
de importância de variáveis leva a conclusões absurdas e o exercício perde o valor. Por isso a spec
tem o campo `importancia` (1-5) por termo, os efeitos são **reponderados** por ele (a escala bruta das
colunas sozinha faz um fator secundário abafar o principal), e o validador reprova hierarquia
invertida e efeito ausente, com limiar proporcional à importância declarada.

**Entrega ao aluno:** o CSV NÃO vai inline. O coordenador sobe o arquivo na nuvem e cola o link no
marcador `[INSERIR AQUI O LINK DE DOWNLOAD]` deixado na prova — assim quem não domina Python não trava
na geração. O `empacotar_para_coordenador.py` detecta a pasta `dataset/`, inclui os CSVs e o script no
ZIP, e acrescenta às instruções a seção explicando o procedimento.

**PENDENTE:** o enunciado ainda descreve o dataset antigo ("~100 linhas", "8 valores ausentes",
"3 outliers acima de 950"). Ao crescer a base, essas menções ficam falsas e a checagem semântica
reprova — corretamente. Falta a fase que reescreve essas quantidades no texto.

## Regra de operação (assistentes): parâmetros são decisão do usuário

**NUNCA escolha por conta própria parâmetros que afetem custo, latência ou escopo de um run, e NUNCA infira a urgência ou a intenção do usuário.** Isso inclui `--max_questoes`, `--min_por_curso` / `--max_por_curso`, `--perfil`, `--formato`, `--modo_dados`, `--ultimo-nivel`, `--escape-hatch`.

**Exceção — `--batch` é o padrão e não se pergunta:** sempre passe `--batch` na etapa 4 (nas etapas 2 e 3 já é automático). Custa 50% menos; a latência de 5-30 min é aceitável. Só rode síncrono se o usuário pedir explicitamente.

Um "pode rodar" autoriza **a etapa**, não os **parâmetros**. Se o usuário não especificou um valor com trade-off, pergunte — mesmo que o custo pareça baixo e mesmo que a escolha pareça óbvia. Não escreva justificativas do tipo "escolhi X porque você quer Y".

## Diretrizes para quem for editar o código

- Adicionar uma nova carreira/nível: editar o dicionário em `scripts/carreiras_niveis.py`. Não duplicar IDs em múltiplos lugares.
- **Estrutura de output/**: uma pasta por projeto (`output/<slug>_nivel_<n>/`). O slug vem de `_slugify(carreira)` aplicado ao nome oficial da carreira (o mesmo em todos os scripts). Todos os scripts (etapas 2, 3, 3.5, 4, 4.5, 5) leem e escrevem nessa pasta.
- **Modelo padrão atual:** Opus 4-6 em TODAS as etapas LLM (`MODEL`, `MODEL_IDEAS`, `MODEL_FORMAT`, `MODEL_GEN`, `MODEL_RANK`, e `MODEL_REVISOR` nas etapas 3.5/4.5). Preferência do usuário registrada em memória — usar outro modelo só se for pedido explicitamente. **Não voltar para Opus 4-8** (gera enunciados curtos demais na teórica — decisão do usuário). **Provider detectado pelo prefixo**: `gpt-*` ou `o1/o3/o4-*` → OpenAI, `claude-*` → Anthropic. Modelos sem suporte a `temperature` customizada (gpt-5, o-series, claude-opus-4-7+, claude-opus-4-8, claude-opus-5) são tratados automaticamente em `_model_supports_temperature`.
- **Cache + Batch Anthropic:**
  - **Etapa 2** (resumos) e **etapa 3** (teórica, fases 1 e 2): Message Batches API (50% off) **automática** quando MODEL é Anthropic. Cache (90% off em hits) sempre ativo — funciona se os blocks estáticos atingirem 1024 tokens (Sonnet/Opus) ou 2048 (Haiku). Flag `--no-batch` força sync (debug).
  - **Batch NUNCA cai para síncrono sozinho (regra do usuário, 2026-09-23).** Antes, um `APIConnectionError` ao consultar o status abandonava o batch (que seguia rodando e era cobrado) e refazia tudo em sync — a etapa 2 de IA Automação n3 custou ~2,7x o previsto por isso. Agora, nas etapas 2, 3 e 4: queda de conexão/429/5xx no polling ou na leitura dos resultados → espera e consulta de novo (até `BATCH_MAX_FALHAS_SEGUIDAS` = 40 seguidas; depois aborta citando o batch id, cujos resultados ficam no servidor por 29 dias); request `errored`/`expired` → reenviado **uma vez em novo batch** (se falhar de novo, fica vazio com `[ATENÇÃO]` no log). Não reintroduzir fallback síncrono: sync só com `--no-batch`, por decisão explícita do usuário.
  - **Etapa 4** (prática): 1 chamada única. Batch é **opt-in** via `--batch` (útil pra economizar 50% em runs não urgentes; latência sobe pra 5-30 min).
  - **Etapa 3 fases 3 e 4** continuam sync (loop iterativo + 1 chamada de ranking); refactor pra batch tem ROI baixo.
- Mexer nos **prompts** de geração de prova é uma mudança de alto impacto — valide com o usuário antes. Os prompts estão em funções `system_prompt_*` / `user_prompt_*` e foram iterados com base em incidentes reais (alternativas muito longas, questões fora de escopo, etc.).
- **Teórica em nível com POUCOS CURSOS gera questões redundantes — checar SEMPRE antes do upload.** Quanto menos cursos, pior: com 2 cursos apareceram 3 pares redundantes; com 1 curso, 5 e 7 de 20. A fase 3 ("completando até atingir o alvo") produz variações do mesmo conceito trocando empresa e formulação. **Regerar com reforço antirredundância NÃO resolve** (testado — a segunda versão veio igualmente redundante); o que funciona é **substituição cirúrgica**: identificar os pares, escolher tópicos ainda descobertos e regerar só aquelas questões com script ad hoc reusando `_chat`/`resumo_to_transcription_text` do próprio gerador. Dois checks obrigatórios: (a) **títulos duplicados** — quebram `desativar_atividades_prova_teorica`, que casa atividades por título; (b) **similaridade da ALTERNATIVA CORRETA entre pares** — pega a redundância que títulos diferentes escondem. Comparar só o cenário dá falso positivo: cenário reaproveitado com pergunta diferente é legítimo. Ao gerar questão avulsa fora do pipeline, **inclua regra dura de ortografia com acentos** — sem ela o modelo devolve texto sem nenhum diacrítico.
- **Parser da teórica** (`_parse_exercise_ideas_verbatim`) tolera: markdown bold/itálico nos marcadores, cabeçalhos markdown (`##`), e sinônimos comuns (`Enunciado`/`Pergunta`/`Questão` = `Texto da questão`; `Resolução`/`Solução`/`Resposta correta` = `Resposta`; `Conceito`/`Tópico` = `Conceito abordado`). O prompt em `_ask_exercise_ideas` impõe o formato estrito; o parser é rede de segurança.
- O `upload_checkpoint_alura.py` usa Playwright + JS evaluate para EasyMDE/CodeMirror (textareas escondidos no admin). Seletores chave: `select#chooseTask` (tipo de atividade, hierárquico), `input.add-alternative[data-type='emptySingleAlternative']`, `textarea[name="alternatives[N].text"]`, `textarea[name="alternatives[N].opinion"]`, `input[type="radio"][name="alternatives[N].correct"]`.
- **Flag `--indices` no uploader** (`criar_atividades_prova_teorica`): lista CSV 1-based (ex.: `--indices 1,5`) para re-subir apenas exercícios específicos após um run parcial. Aplicado antes de `--offset`/`--limite`.
- **INSPECIONE O CURSO ANTES DE ESCREVER (etapa 5).** Os cursos de checkpoint costumam ser **reciclados de outra carreira** e chegam com seções antigas (`Apresentação-old`, `Prova teórica-old`, …) cheias de atividades de outro conteúdo — e a plataforma **não apaga atividade**. Levante seções + atividades existentes e confira se algum nome colide com `Apresentação` / `Prova teórica` / `Prova prática` antes de criar qualquer coisa. O `_section_id_por_nome` hoje exige casamento **exato** e se recusa a escolher entre candidatos ambíguos (proteção criada após o incidente do curso 6503), mas a inspeção é o que permite decidir com informação — inclusive conferir se o **nome do curso** corresponde mesmo à carreira (em 2026-08 o checkpoint da carreira "IA aplicada a Operações e Infraestrutura" estava num curso chamado "Checkpoint AI Agent Ops"). O que estiver inativo, deixe inativo.
- **`criar_secoes` deixa a seção "Prova teórica" INATIVA.** Efeito colateral de `marcar_prova_teorica`, que salva o formulário da seção ao marcar "É prova?". Aconteceu em 100% dos checkpoints observados (Eng ML n1 e os três da carreira IA aplicada a Operações e Infraestrutura) — trate como comportamento esperado, não acidente. **Não reative antes de ativar as questões:** com as 20 teóricas em INACTIVE, uma seção ativa mostra ao aluno uma prova vazia. Ordem correta: ativar as 10 questões escolhidas → depois ativar a seção.
- **Falso negativo do botão Salvar (`_criar_atividade_explicacao`).** O `page.click` pode estourar `PWTimeoutError` quando o próprio clique dispara a navegação; os seletores seguintes não acham mais o botão (a página já foi para `/tasks`) e o script aborta com `RuntimeError: Botão 'Salvar' não encontrado` **mesmo tendo salvo a atividade com o conteúdo completo**. **Nunca re-execute do zero após esse erro** — verifique o estado real primeiro (listar as atividades da seção e conferir o tamanho do conteúdo salvo de cada uma) e retome com `--offset N`, que **funciona também em `criar_atividades_prova_pratica`**. Re-rodar sem offset duplica atividades que não têm como ser apagadas.
- **Prova teórica nasce INACTIVE (default do projeto):** `criar_atividades_prova_teorica` cria as questões e, ao final, passa **todas** para `INACTIVE` — o coordenador ativa depois as questões escolhidas (as provas sobem 20 questões das quais só ~10 ficam ativas). Para criar já ativas, passe `--manter-teoricas-ativas`. **Fluxo limpo de ativação seletiva das 10 escolhidas (sem mexer à mão no admin):** crie as 20 **já ativas** com `--manter-teoricas-ativas` e depois rode `desativar_atividades_prova_teorica` com os 10 títulos em `output/<slug>_nivel_<n>/manter_ativos.txt` — as demais viram INACTIVE (sanity-check aborta se algum título da lista não existir na seção). **Atenção:** `desativar_atividades_prova_teorica` só DESATIVA (nunca ativa); se as questões nascerem no default INACTIVE, ele não as ativa — por isso crie-as ativas primeiro. Validado em Governança de Dados n2 → curso 5257: 10 ativas / 10 inativas, zero pendência manual.
- **Conclusão personalizada da prova prática:** a Etapa 4, na finalização, gera via LLM uma **Conclusão** adaptada à carreira/nível — o `_chat` de `gerar_conclusao` recebe o texto-base fixo `CONCLUSAO_BASE` (o texto de Análise de Dados) como estrutura e troca só o específico (carreira, nível, ferramentas citadas, artefatos do GitHub, hashtags), preservando seções/tom/markdown. O resultado é anexado ao TXT após o marcador `<!-- CONCLUSAO -->`. A Etapa 5 (`criar_atividades_prova_pratica`) extrai essa Conclusão via `_extrair_conclusao`; se o TXT não tiver o marcador (provas geradas antes desse recurso), cai no fallback `PRATICA_CONCLUSAO_HARDCODED`. Falha na geração não aborta a Etapa 4 (segue sem conclusão → Etapa 5 usa fallback). **Flag `--ultimo-nivel`:** quando este é o último nível da carreira, a Conclusão celebra o fim da formação (sem "próximo nível"). É opt-in explícito (não deduzido do mapa `carreiras_niveis.py`, que costuma estar incompleto) — passe `--ultimo-nivel` na Etapa 4 quando for o nível final. **Formato `cases` — já tratado na origem:** `CONCLUSAO_BASE` é o texto de Análise de Dados (orientado a código) e vazava artefatos incoerentes com cases (scripts Python/SQL, hashtags de código) — ocorreu em Governança de Dados n2 → curso 5257 e foi corrigido à mão no TXT. O fix sistêmico **já existe**: `gerar_conclusao` recebe `formato` e, quando `cases`, injeta uma regra que troca os artefatos por documentais (análises, tabelas comparativas, ADRs, diagramas) e proíbe citar script/Dockerfile/YAML/notebook; e as ferramentas citadas vêm de `_ferramentas_reais_da_prova` (cabeçalho "Ferramentas exigidas ao longo da aula" do TXT gerado), não da lista de permitidas. Continua valendo conferir a Conclusão no QA, mas como verificação, não como correção esperada.
- **Posição da resposta correta na teórica NÃO importa:** o gerador tende a montar a correta sempre na letra A. Isso **não é bug** — a plataforma da Alura **randomiza a ordem das alternativas por aluno** a cada carregamento. Não "corrigir" o gerador para embaralhar, não re-subir provas por isso. (Se um QA cego levantar "correta sempre em A", responder que a plataforma randomiza.)
- **Cuidado ao rodar o uploader (etapa 5) no shell:** NÃO canalizar a saída para `| head` — quando o `head` fecha o pipe cedo, o Python recebe SIGPIPE e **morre no meio da operação** (já interrompeu uma desativação de teóricas em 18/20). Rode sem `head`, filtre com `grep` (que consome tudo) ou rode em background. Se a criação/desativação de teóricas for interrompida, rode `desativar_atividades_prova_teorica` (idempotente, com `manter_ativos.txt` vazio para desativar todas) para garantir o estado.
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
