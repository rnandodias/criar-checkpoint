"""
=============================
Geradores do Checkpoint — 3 (Prática)
=========================================================
openai==1.102.0 | python-dotenv
=============================
scripts/gerar_prova_pratica_do_zero.py
=============================

Como rodar (exemplos):

Prática (nível 1, TXT; domínios e ferramentas padrão; resumos via CLI):
python scripts/gerar_prova_pratica_do_zero.py \
  --nivel 1 \
  --carreira "Analista de Dados" \
  --resumos_arquivo output/checkpoints/resumos_analista_de_dados_nivel_1.json

Forçar modo de dados (opcional):
  --modo_dados com        # sempre com datasets (30-120)
  --modo_dados sem        # nunca criar datasets
  --modo_dados auto       # (padrão) decide automaticamente pelo contexto

Opcional:
  --domains_arquivo trilha/domains.json
  --ferramentas_arquivo trilha/ferramentas.json
  --verbose               # mostra tempos por fase

Exemplo de uso:
python scripts/gerar_prova_pratica_do_zero.py --nivel 1 --carreira "governanca_de_dados" --resumos_arquivo output/checkpoints/resumos_governanca_de_dados_nivel_all.json --modo_dados auto --verbose


Saída:
output/cursos_checkpoint/prova_pratica_<slug_carreira>_nivel_<n>.txt
"""

from __future__ import annotations
import argparse
import csv as _csv
import io
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from openai import OpenAI
from anthropic import Anthropic

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# =========================
# Config de modelos
# =========================
# Geração da prova prática: top de linha (segue restrições rigorosas de escopo)
MODEL_GEN = "claude-opus-4-6"
TEMP = 0.0

# Alternativas:
# MODEL_GEN = "claude-sonnet-4-6"  # ~30% do custo
# MODEL_GEN = "gpt-5" / "gpt-4o-2024-08-06"
SINGLE_PASS_CHAR_LIMIT = 300_000

OUTPUT_BASE = Path(__file__).resolve().parent.parent / "output"


def _projeto_dir(carreira: str, nivel: int) -> Path:
    """Pasta do projeto: output/<slug>_nivel_<n>/. Usa _slugify definido mais abaixo."""
    return OUTPUT_BASE / f"{_slugify(carreira)}_nivel_{nivel}"


# Reforço opcional injetado via --reforco_extra (usado pelo revisor 4.5 no rerun automático).
REFORCO_EXTRA: str = ""

# Schema novo (etapa 2): resumo por curso é {tema_central, conteudos_testaveis[], ferramentas_usadas[]}.

# Regras de datasets (para carreiras de dados)
MIN_ROWS = 30
MAX_ROWS = 120

# =========================
# Domínios padrão
# =========================
DOMAINS_DEFAULT = [
    "Clínica Médica Voll - Clínica especializada em serviços médicos e exames",
    "Bytebank - Banco digital que oferece serviços bancários online",
    "Buscante - Buscador e e-commerce de livros variados",
    "Playcatch - Plataforma de streaming de música, similar ao Spotify",
    "ADOPET - Site de adoção de animais de estimação",
    "Organo - Plataforma para criação e gestão de organogramas empresariais",
    "Screen Match - Plataforma de streaming de vídeos, similar ao YouTube",
    "Techsafe - Empresa especializada em segurança tecnológica e cibersegurança",
    "Cookin'UP - Aplicativo que compartilha receitas culinárias e dicas de cozinha",
    "Meteora - Loja online de roupas e acessórios",
    "Checklist - Plataforma de gestão de tarefas e checklists para equipes",
    "CodeChella - Organização de um festival de música com diversas atrações",
    "Serenatto - Café & Bistrô que oferece uma variedade de refeições e bebidas",
    "Hermex Log - Empresa de logística especializada em serviços de entrega",
    "Gatito Petshop - Loja que oferece produtos e serviços para animais de estimação",
    "Jornada Milhas - Plataforma para compras de passagens aéreas utilizando milhas",
    "Fokus - Aplicativo para aumentar a produtividade utilizando a técnica Pomodoro",
    "Meu Pequeno Grimorio - Loja especializada em livros de literatura fantástica e esotérica",
    "Luz & Cena - Cinema com exibição e sinopses de filmes em cartaz",
    "UseDev - E-commerce de produtos geeks",
    "Petpark - E-commerce + serviços para animais com agendamento",
    "CodeConnect - Rede social para programadores",
    "Zoop - Plataforma de e-commerce com estoque e pagamentos integrados",
    "Runner Circle - Rede social para corredores",
    "HomeHub - Painel de casa inteligente (IoT)",
    "Listin - Gerenciador de listas de supermercado",
    "SwiftBank - Banco digital com serviços financeiros",
    "Indexa - Gestor inteligente de contatos",
    "Cinetopia - Catálogo e recomendações de filmes",
    "Clickbonus - Clube de vantagens e recompensas",
    "Calmaria Spas - Marketplace de spas e bem-estar",
    "Jornada Viagens - Comparador e reserva de viagens",
    "VideoFlowNow - Vídeos curtos e lives (engajamento por IA)",
    "WaveCast - Publicação/monetização de podcasts",
    "Freelando - Marketplace de freelas",
    "TRATOTECH - Classificados de eletrônicos",
    "Dev.Spot - Portfólios/link tree para devs",
]

# =========================
# Utils
# =========================
def _slugify(s: str) -> str:
    import unicodedata as _ud
    s = (s or "").strip().lower()
    # Desacenta primeiro: "Governança" -> "governanca", "Automação" -> "automacao"
    s = _ud.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_") or "geral"

def _slug_or_default(carreira: str) -> str:
    return _slugify(carreira)

def _resumos_path_default(nivel: int, carreira: str) -> Path:
    slug = _slug_or_default(carreira)
    candidate = INPUT_DIR / f"resumos_{slug}_nivel_{nivel}.json"
    if candidate.exists():
        return candidate
    # fallback legado comum
    return INPUT_DIR / f"resumos_analista_de_dados_nivel_{nivel}.json"

def _load_resumos_via_cli(path: str, nivel: int, carreira: str) -> List[Dict[str, Any]]:
    if path:
        p = Path(path)
    else:
        p = _resumos_path_default(nivel, carreira)
    if not p.exists():
        raise FileNotFoundError(f"Arquivo de resumos não encontrado: {p}")
    return json.loads(p.read_text(encoding="utf-8"))

def _resumos_compactos(resumos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Passa o resumo direto — etapa 2 já filtrou (só conteudos_testaveis)."""
    return [
        {
            "id": c.get("id"),
            "nome": c.get("nome"),
            "link": c.get("link"),
            "resumo": c.get("resumo", {}) or {},
        }
        for c in resumos
    ]


def _derivar_ferramentas_permitidas(resumos: List[Dict[str, Any]], ferramentas_cli: Optional[List[str]]) -> List[str]:
    """Consolida `ferramentas_usadas` de todos os cursos. A etapa 2 já filtrou para incluir
    APENAS ferramentas com uso real, então não há threshold/contagem aqui."""
    if isinstance(ferramentas_cli, list) and ferramentas_cli:
        return ferramentas_cli

    seen: Dict[str, str] = {}
    for c in resumos:
        for f in (c.get("resumo", {}) or {}).get("ferramentas_usadas", []) or []:
            s = str(f).strip()
            if s and s.lower() not in seen:
                seen[s.lower()] = s

    if not seen:
        # Fallback conservador: carreira sem ferramentas demonstradas
        return ["Planilhas (Excel/Google Sheets)", "Editor de texto (Word/Google Docs)", "Draw.io"]
    return sorted(seen.values())


def _perfil_carreira(resumos: List[Dict[str, Any]]) -> str:
    """Classifica a carreira por proporção de conteúdos procedimentais.
    >50% dos conteudos_testaveis com tipo 'procedimental' → programatica; senão → conceitual."""
    total = 0
    procedimentais = 0
    for c in resumos:
        for ct in (c.get("resumo", {}) or {}).get("conteudos_testaveis", []) or []:
            total += 1
            if str(ct.get("tipo", "")).strip().lower() == "procedimental":
                procedimentais += 1
    if total == 0:
        return "conceitual"
    return "programatica" if (procedimentais / total) > 0.5 else "conceitual"


def _carreira_envolve_dados(carreira: str, ferramentas: List[str], resumos: List[Dict[str, Any]]) -> bool:
    carreira_l = (carreira or "").lower()
    gatilhos_nome = ["dados", "data", "analytics", "cientist", "engenharia de dados", "bi", "etl"]
    if any(g in carreira_l for g in gatilhos_nome):
        return True
    ferr_l = [f.lower() for f in ferramentas]
    gatilhos_ferr = ["sql", "pandas", "spark", "hive", "power bi", "tableau", "dbt", "airflow"]
    if any(any(g in f for g in gatilhos_ferr) for f in ferr_l):
        return True
    # Inspeciona tópicos e habilidades dos conteudos_testaveis
    for c in resumos:
        for ct in (c.get("resumo") or {}).get("conteudos_testaveis", []) or []:
            joined = (str(ct.get("topico", "")) + " " + str(ct.get("habilidade", ""))).lower()
            if any(g in joined for g in ["sql", "dataset", "pandas", "etl", "pipeline", "visualização", "visualizacao", "bi"]):
                return True
    return False

# =========================
# Prompts (TXT direto)
# =========================
def system_prompt_aula3_txt() -> str:
    return """Você é uma pessoa especialista em desenho instrucional e avaliação prática baseada em competências.
Sua tarefa é gerar a **Aula 3 – “03.Prova prática”** de um curso de Checkpoint, utilizando **exclusivamente** as informações presentes nos **resumos dos cursos do nível** (entrada do usuário).

Regras gerais (mantenha TODAS):
- **Autoridade da lista de ferramentas (REGRA DURA)**: a lista "Ferramentas permitidas" recebida no user prompt é DEFINITIVA. Use APENAS ferramentas dessa lista. **NÃO extraia** ferramentas adicionais dos resumos. Os resumos foram pré-filtrados para você por profundidade pedagógica, mas mesmo assim podem conter itens classificados como "apenas_mencionado" — esses NÃO foram ensinados de verdade no curso e estão PROIBIDOS na prova. Se uma ferramenta não está na lista "Ferramentas permitidas", ela não foi ensinada e não pode aparecer.
- **Perfil da carreira (REGRA DURA)**: o user prompt informa o `perfil` da carreira como `programatica` ou `conceitual`.
  - Se **conceitual** (governança, papéis, processos, políticas): prefira **entregáveis documentais e diagramáticos** — planilhas, documentos, fluxogramas, organogramas, políticas escritas, glossários em planilha. Use linguagens de programação ou bibliotecas (Python, Pandas, etc.) APENAS quando a tarefa não puder ser representada de outra forma — e mesmo aí, mantenha o uso minimalista (1 etapa no máximo, com script curto).
  - Se **programática** (back-end, ciência de dados, ML): priorize código, scripts, configurações e datasets, com etapas técnicas mais densas.
- **Profundidade dos itens**: nos resumos, cada habilidade/conceito/exemplo vem com `profundidade` ∈ {`demonstrado`, `praticado`, `apenas_mencionado`}. Construa entregáveis APENAS sobre o que está como `demonstrado` ou `praticado`. Itens `apenas_mencionado` podem ser referenciados em texto, mas nunca pedidos como tarefa.
- **Cobertura do nível**: inclua ao longo das etapas ao menos **um item** que mobilize **cada curso** do nível (faça mapeamento ao final). NÃO UTILIZE FERRAMENTAS DE NUVEM (AWS, Amazon, Azure, GCP, Google Cloud Platform e qualquer serviço derivado destes grandes serviços) que geram custos para os alunos.
- **Não invente conteúdo**: não introduza ferramentas, conceitos ou técnicas que **não apareçam** nos resumos; apenas adapte e combine o que já foi visto.
- **Ferramentas e prática**: cada etapa deve **usar ao menos uma ferramenta da lista "Ferramentas permitidas"**. Se a lista contém apenas ferramentas não-programáticas (planilhas, documentos, diagramação), as etapas devem ser entregas documentais/diagramáticas — **não** force scripts Python ou consultas SQL.
- **Dados (APENAS quando aplicável)**:
  - **Somente gere datasets se a carreira envolver dados** (ex.: Análise/Engenharia de Dados, BI, Ciência de Dados). Caso contrário, **não gere datasets** e foque em artefatos coerentes (APIs, scripts, CLI, configs, deploy, testes, automações, documentação técnica).
  - Se gerar datasets, forneça **um ou mais arquivos** **in-line** (CSV ou JSON) com **entre 30 e 120 linhas de dados** (não conte o cabeçalho). Use dados **fictícios e não sensíveis**.
  - Quando houver datasets, inclua **dicionário de dados** curto em texto antes do bloco.
- **Domínio e imersão**: selecione **um único domínio** adequado ao projeto e **use o MESMO domínio em todas as etapas**; explique brevemente esse domínio no topo.
- **Pergunta-chave**: cada etapa termina com **uma pergunta-chave única**.
- **Linguagem**: neutra, sem masculino genérico; prefira “A empresa te contratou”, “A equipe que você integra”, etc.; **não** use “Você foi contratado”.
- **Escalonar dificuldade**: da 1ª para a 4ª etapa, **aumente a complexidade** e integre mais conceitos.
- **Dicas**: inclua **“Dicas de troubleshooting”** por etapa (sem resolver completamente).
- **Sem links externos**.

# === Reforço perfil profissional (2026-07) — remover este bloco para voltar ao prompt original ===
- **Análise prévia do perfil profissional (REGRA DURA)**:
  ANTES de escolher o domínio e propor o projeto, REFLITA — usando estritamente os resumos — sobre:
    (a) O dia-a-dia REAL dessa profissão: o que essa pessoa produz, para quem, com que frequência?
    (b) As ENTREGAS PROFISSIONAIS TÍPICAS (não exercícios acadêmicos):
        ex.: pipelines em produção, dashboards executivos, APIs REST em uso, modelos servidos,
        políticas de governança escritas, arquiteturas documentadas, integrações entre sistemas.
    (c) Os DESAFIOS RECORRENTES da profissão (qualidade, escala, latência, integração, custo,
        governança, observabilidade, retenção, drift, segurança) — que devem estar refletidos no
        cenário como pano de fundo realista.
  Use essa análise para propor um projeto AUTÊNTICO à profissão. Se uma pessoa profissional experiente
  lesse o cenário e reagisse com "isso não é o que a gente faz de verdade", o cenário está errado —
  proponha outro. Não gere "trabalhinho" com aparência profissional; gere um recorte fiel do que
  aquela profissão entrega no mundo real.
- **Armadilhas comuns a evitar** (falhas recorrentes de provas geradas por LLM):
  * Engenharia de Dados que vira análise exploratória em notebook (deveria ser pipeline em produção)
  * Back-end que só faz CRUD isolado (deveria integrar com outros sistemas, tratar erros, ter deploy)
  * Análise de Dados que para na limpeza (deveria entregar decisão de negócio, dashboard, relatório)
  * ML sem servir o modelo (deveria expor via API, batch, ou dashboard de predição)
  * Carreira conceitual sem entregável concreto (deveria produzir documento, política, diagrama, planilha)
  * Cenário genérico "empresa X está migrando para dados" sem definir para onde e por quê
# === fim reforço perfil ===

# === Proibição de meta-comentários (2026-07) — remover este bloco para voltar ao prompt original ===
- **Proibição absoluta de meta-comentários (REGRA DURA — sem exceções)**:
  NÃO faça observações sobre o próprio texto, sobre as regras que você está seguindo,
  ou sobre o processo de geração. As regras devem estar APLICADAS no texto — NUNCA
  citadas, explicadas ou defendidas. O texto entregue deve parecer escrito diretamente
  para a pessoa aluna, sem NENHUMA pista das diretrizes que o guiaram.

  Exemplos do que NÃO fazer (falhas já observadas em runs anteriores):
    * "A linguagem neutra é mantida: a empresa te contratou..."   ← proibido
    * "Conforme as regras, aqui usamos entregáveis documentais..." ← proibido
    * "Seguindo o formato pedido, a etapa 1 traz..."               ← proibido
    * "Note que preservamos fidelidade à aula..."                  ← proibido
    * "Aqui a empresa te contratou (linguagem inclusiva)..."      ← proibido
    * "Este projeto foi desenhado com base nos resumos..."         ← proibido
    * Qualquer sentença que descreva o texto em vez de compor o texto.

  Se quiser mencionar linguagem neutra, USE-A — não anuncie que a está usando.
  Se quiser respeitar a fidelidade à aula, RESPEITE — não afirme que está respeitando.
# === fim proibição meta-comentários ===

Formato de SAÍDA (TEXTO PURO) — siga exatamente:

# 03.Prova prática
**Domínio escolhido:** <nome do domínio> — <explicação breve do domínio>
**Ferramentas exigidas ao longo da aula:** <lista simples separada por vírgulas>

## Descrição do projeto
<parágrafos descrevendo o projeto, objetivos, entregáveis esperados, linguagem neutra>

## Antes de começar
## Dedicação
O tempo esperado para você investir no desenvolvimento do projeto é de [COLOCAR UMA ESTIMATIVA DE HORAS NO FORMATO: XX a XX] horas de dedicação. Se você estiver demorando muito mais que isso, pode ser que esteja indo longe demais.

## Dúvidas?
É normal surgirem dúvidas no meio da implementação. Peça ajuda! Utilize ferramentas de IA, Google e documentações das ferramentas, quanto do próprio [Fórum da Alura](https://cursos.alura.com.br/forum/todos/1?hasAccessMGM=true).

## Preparando o ambiente
<pré-requisitos, instalações, bibliotecas e instruções de uso (SQL/Pandas/BI/CLI/APIs/etc.)>
[Se a carreira envolver dados: listar datasets e dicionário(s) de dados; em seguida, incluir blocos de código com os arquivos:]
### <nome_do_arquivo_1.csv ou .json> — <breve descrição>
<dicionário de dados em lista curta>
```csv
<conteúdo CSV com entre 30 e 120 linhas de dados>
```
[se houver outro dataset, repetir o bloco; se a carreira NÃO envolver dados, não incluir blocos ```csv/```json]

## 1ª Etapa: <título curto da etapa>
<contexto e objetivo>
**Pergunta-chave:** "<pergunta única>"
**Sua missão:**
1. <passo>
2. <passo>
3. <passo>
**Ferramentas:** <lista curta>
---
**Dicas de troubleshooting para a 1ª etapa:**
* <dica>
* <dica>
* <dica>

## 2ª Etapa: <título curto da etapa>
... (mesmo padrão, aumentando a complexidade)

## 3ª Etapa: <título curto da etapa>
... (mesmo padrão)

## 4ª Etapa: <título curto da etapa>
... (mesmo padrão; integração de múltiplos conceitos)

## Matriz de cobertura (auditoria)
Liste cada curso do nível em uma linha, no formato:
- <nome do curso>: conceitos_alvo resumidos → etapas relacionadas (ex.: "1ª, 3ª")
"""

def user_prompt_aula3_txt(
    nivel_str: str,
    carreira_str: str,
    domains_list_formatada: str,
    ferramentas_permitidas: List[str],
    resumos_json_do_nivel: str,
    carreira_env_dados: bool,
    perfil_carreira: str = "conceitual",
) -> str:
    ferr = json.dumps(ferramentas_permitidas, ensure_ascii=False)
    return (
        f"Gere a **Aula 3 – Prova prática** seguindo integralmente as regras do sistema e retornando **apenas TEXTO** no formato exigido.\n\n"
        f"Contexto do nível:\n- Nível: {nivel_str}\n- Carreira: {carreira_str}\n- Perfil da carreira: {perfil_carreira.upper()}\n- Carreira envolve dados? {'SIM' if carreira_env_dados else 'NÃO'}\n\n"
        "Domínios disponíveis (escolha **um** e mantenha-o em todas as etapas):\n"
        + domains_list_formatada
        + "\n\nFerramentas permitidas (use apenas dentre estas):\n"
        + ferr
        + "\n\nResumos dos cursos (fonte única de verdade):\n```json\n"
        + resumos_json_do_nivel
        + "\n```\n\n"
        "Observações finais:\n"
        "IMPORTANTE: A carreira segue uma evolução em níveis (1, 2 e 3), onde o nível 1 é o mais básico, o 2 o intermediário e o 3 o nível mais avançado. Considere isso no momente de elaborar o projeto, trazendo um nível de complexidade condizente com o nível do aluno. Ou seja, o nível 3 precisa de projetos maiores e mais elaborados.\n"
        "- Se **envolver dados**, gere um ou mais datasets **in-line** (CSV/JSON) com 30–120 linhas (não sensível) em blocos ```csv/```json.\n"
        "- Se **NÃO envolver dados**, **não** gere datasets; foque em tarefas coerentes (APIs, scripts, configs, automações, testes, etc.).\n"
        "- Se o **perfil é CONCEITUAL**, prefira entregáveis documentais/diagramáticos (planilhas, organogramas, fluxogramas, glossários, políticas escritas). Use programação só se indispensável e em uma única etapa.\n"
        "- Escalone a dificuldade (1ª→4ª etapa) e inclua a Matriz de cobertura no final."
    )


def system_prompt_aula3_cases_txt() -> str:
    """System prompt ISOLADO para o formato ANÁLISE DE CASES (carreiras conceituais).
    Não substitui system_prompt_aula3_txt — é uma variante selecionada por --formato cases."""
    return """Você é uma pessoa especialista em desenho instrucional e avaliação por competências.
Sua tarefa é gerar a **Aula 3 – "03.Prova prática"** de um curso de Checkpoint no formato **ANÁLISE DE CASES**, usando **exclusivamente** as informações dos **resumos dos cursos do nível** (entrada do usuário).

Este formato é para carreiras **conceituais/analíticas**: a prova **NÃO é um projeto de implementação**. É a **análise de um caso realista** em que a pessoa **diagnostica um problema, decide com base em trade-offs e comunica** — como uma pessoa profissional faz no dia a dia.

Regras gerais (mantenha TODAS):
- **Autoridade da lista de ferramentas (REGRA DURA)**: use APENAS ferramentas da lista "Ferramentas permitidas" do user prompt. NÃO extraia ferramentas adicionais dos resumos. Se não está na lista, não foi ensinada e não pode aparecer.
- **Fidelidade aos resumos (REGRA DURA)**: não invente conceitos, técnicas, frameworks ou ferramentas que não apareçam nos resumos. Construa a análise APENAS sobre o que está como `demonstrado` ou `praticado`; itens `apenas_mencionado` podem ser citados em texto, nunca exigidos como tarefa.
- **Cobertura do nível**: ao longo das etapas, mobilize ao menos um conteúdo de CADA curso do nível (mapeie ao final na Matriz de cobertura).
- **PROIBIDO nuvem paga** (AWS, Amazon, Azure, GCP, Google Cloud e derivados que gerem custo ao aluno).
- **Linguagem neutra**, sem masculino genérico; use "a empresa te contratou", "a equipe que você integra"; **nunca** "você foi contratado".
- **Sem links externos** (exceto o Fórum da Alura no bloco padrão).
- **Sem meta-comentários (REGRA DURA)**: aplique as regras NO texto, nunca as cite, explique ou defenda. O texto deve parecer escrito diretamente para a pessoa aluna, sem pistas das diretrizes.
- **Transferência (não imitar a aula)**: o case deve ser um cenário NOVO que exige transferir o que foi aprendido — nunca reproduzir/continuar um exemplo das aulas.

Regras ESPECÍFICAS do formato ANÁLISE DE CASES (REGRAS DURAS):
- **NÃO gere datasets** (nenhum bloco ```csv/```json) e **NÃO peça código, scripts, SQL ou implementação**. Os entregáveis são **documentais/analíticos**: documentos de análise, tabelas comparativas, registros de decisão, diagramas, pareceres.
- **Um CASE ÚNICO evolutivo**: escolha um cenário realista (empresa/sistema/situação fictícia) e mantenha-o em TODAS as 4 etapas, aumentando a complexidade. Cada etapa avança o MESMO case.
- **Fluxo profissional**: estruture as 4 etapas espelhando como a profissão realmente trabalha um caso — tipicamente da **compreensão do problema** → **análise/diagnóstico** → **decisão justificada por trade-offs** → **documentação e comunicação a stakeholders**. Derive o fluxo específico dos resumos da carreira.
- **IA como copiloto (EIXO)**: SE os resumos ensinarem o uso de IA/LLM como apoio ao trabalho, então CADA etapa deve incorporar explicitamente o uso de **IA como ferramenta de apoio à análise** (ex.: usar um LLM com prompt estruturado para organizar o raciocínio, transformar entradas vagas em artefatos, gerar um rascunho e depois criticá-lo). Faça isso de forma fiel ao que os resumos mostram — não invente capacidades.
- **Análise ABERTA, sem gabarito**: não existe resposta única. NÃO forneça a resposta "correta". Em vez disso, oriente o que caracteriza uma boa análise.
- **Entregáveis são SUGESTÕES**, não obrigações: apresente-os como caminhos possíveis, dando liberdade de formato à pessoa.
- **Escalonar dificuldade** da 1ª para a 4ª etapa, integrando mais conceitos.

Formato de SAÍDA (TEXTO PURO) — siga exatamente:

# 03.Prova prática
**Domínio escolhido:** <nome do case/cenário> — <explicação breve do cenário>
**Ferramentas exigidas ao longo da aula:** <lista simples separada por vírgulas>

## Descrição do projeto
<parágrafos apresentando o case, o papel da pessoa e o que ela vai analisar, decidir e comunicar ao longo das etapas; linguagem neutra>

## Antes de começar
## Dedicação
O tempo esperado para você investir no desenvolvimento do projeto é de [COLOCAR ESTIMATIVA NO FORMATO: XX a XX] horas de dedicação. Se você estiver demorando muito mais que isso, pode ser que esteja indo longe demais.

## Dúvidas?
É normal surgirem dúvidas no meio da análise. Peça ajuda! Utilize ferramentas de IA, Google e documentações, quanto do próprio [Fórum da Alura](https://cursos.alura.com.br/forum/todos/1?hasAccessMGM=true).

## Preparando o ambiente
<o que ter à mão para a análise: acesso a uma ferramenta de IA (dentre as permitidas), ferramenta de diagramação/documento, etc. SEM instalações de código e SEM datasets.>

## 1ª Etapa: <título curto da etapa>
<contexto do case nesta etapa e o que se pede analisar>
**Pergunta-chave:** "<pergunta única de análise>"
**Sua missão:**
1. <passo de análise>
2. <passo — incluindo uso de IA como copiloto quando os resumos suportarem>
3. <passo>
**Entregáveis sugeridos:** <caminhos possíveis, NÃO obrigatórios>
**O que caracteriza uma boa análise:** <2 a 4 critérios de qualidade — orientação, nunca gabarito>
**Ferramentas:** <lista curta>
---
**Dicas para a análise da 1ª etapa:**
* <dica que orienta sem entregar a resposta>
* <dica>

## 2ª Etapa: <título curto>
... (mesmo padrão, aumentando a complexidade)

## 3ª Etapa: <título curto>
... (mesmo padrão; foco na decisão e nos trade-offs)

## 4ª Etapa: <título curto>
... (mesmo padrão; integração, documentação e comunicação a stakeholders)

## Matriz de cobertura (auditoria)
Liste cada curso do nível em uma linha, no formato:
- <nome do curso>: conceitos_alvo resumidos → etapas relacionadas (ex.: "1ª, 3ª")
"""


def user_prompt_aula3_cases_txt(
    nivel_str: str,
    carreira_str: str,
    domains_list_formatada: str,
    ferramentas_permitidas: List[str],
    resumos_json_do_nivel: str,
) -> str:
    ferr = json.dumps(ferramentas_permitidas, ensure_ascii=False)
    return (
        "Gere a **Aula 3 – Prova prática** no formato ANÁLISE DE CASES, seguindo integralmente as regras do sistema e retornando **apenas TEXTO** no formato exigido.\n\n"
        f"Contexto do nível:\n- Nível: {nivel_str}\n- Carreira: {carreira_str}\n- Formato: ANÁLISE DE CASES (sem código, sem datasets)\n\n"
        "Cenários/domínios sugeridos (escolha um como base do case e mantenha-o em todas as etapas):\n"
        + domains_list_formatada
        + "\n\nFerramentas permitidas (use apenas dentre estas):\n"
        + ferr
        + "\n\nResumos dos cursos (fonte única de verdade):\n```json\n"
        + resumos_json_do_nivel
        + "\n```\n\n"
        "Observações finais:\n"
        "- A carreira evolui em níveis (1 básico → 3 avançado); ajuste a profundidade da análise ao nível informado.\n"
        "- NÃO gere datasets nem peça código; os entregáveis são documentais/analíticos e são SUGESTÕES (não obrigatórios).\n"
        "- Incorpore o uso de IA como copiloto em cada etapa QUANDO os resumos ensinarem isso.\n"
        "- A análise é aberta: NÃO forneça gabarito; oriente o que caracteriza uma boa análise.\n"
        "- Escalone a dificuldade (1ª→4ª etapa) e inclua a Matriz de cobertura no final."
    )

# =========================
# OpenAI helper
# =========================
def _model_supports_temperature(model: str) -> bool:
    """Modelos que NÃO aceitam temperature customizada:
    OpenAI: gpt-5*, reasoning (o1, o3, o4).
    Anthropic: claude-opus-4-7+ (Sonnet 4.6 e Haiku 4.5 ainda aceitam)."""
    m = (model or "").lower()
    return not (
        m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4")
        or m.startswith("claude-opus-4-7") or m.startswith("claude-opus-4-8")
        or m.startswith("claude-opus-5")
    )


def _provider_for(model: str) -> str:
    m = (model or "").lower()
    if m.startswith("claude") or m.startswith("anthropic"):
        return "anthropic"
    return "openai"


_openai_client: Optional[OpenAI] = None
_anthropic_client: Optional[Anthropic] = None


def _get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        key = os.getenv("OPENAI_CREDENTIALS")
        if not key:
            raise RuntimeError("Defina OPENAI_CREDENTIALS no .env para usar modelos OpenAI.")
        _openai_client = OpenAI(api_key=key)
    return _openai_client


def _get_anthropic_client() -> Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("Defina ANTHROPIC_API_KEY no .env para usar modelos Claude.")
        _anthropic_client = Anthropic(api_key=key)
    return _anthropic_client


USAGE_TOTALS: Dict[str, int] = {
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "input_tokens": 0,
    "output_tokens": 0,
}


def _accumulate_usage(usage: Dict[str, int]) -> None:
    for k in USAGE_TOTALS:
        USAGE_TOTALS[k] += int(usage.get(k, 0) or 0)


_USE_BATCH = False  # ativado via flag CLI --batch (apenas Anthropic)


def _anthropic_request_params(model: str, system: str, user: str) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "model": model,
        "max_tokens": 16384,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
    }
    if _model_supports_temperature(model):
        params["temperature"] = TEMP
    return params


def _anthropic_batch_single(model: str, system: str, user: str, poll_interval: float = 30.0) -> str:
    """Submete 1 request via Message Batches API (50% off) e bloqueia até concluir."""
    client = _get_anthropic_client()
    params = _anthropic_request_params(model, system, user)
    print(f"[Batch] Submetendo 1 request para {model}...")
    batch = client.messages.batches.create(requests=[{"custom_id": "pratica", "params": params}])
    print(f"[Batch] ID: {batch.id} | aguardando processamento (poll a cada {poll_interval:.0f}s)...")
    while batch.processing_status != "ended":
        time.sleep(poll_interval)
        batch = client.messages.batches.retrieve(batch.id)
        rc = batch.request_counts
        print(f"[Batch {batch.id[:16]}] proc={rc.processing} ok={rc.succeeded} err={rc.errored} cancel={rc.canceled} exp={rc.expired}")
    print("[Batch] Concluído. Lendo resultado...")
    for entry in client.messages.batches.results(batch.id):
        if entry.result.type != "succeeded":
            raise RuntimeError(f"Batch falhou: {entry.result.type}")
        msg = entry.result.message
        text = "".join(b.text for b in msg.content if hasattr(b, "text"))
        _accumulate_usage({
            "cache_creation_input_tokens": getattr(msg.usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(msg.usage, "cache_read_input_tokens", 0) or 0,
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
        })
        return text
    raise RuntimeError("Batch concluiu sem resultados.")


def _chat(client: Any, model: str, system: str, user: str) -> str:
    """Roteia OpenAI vs Anthropic. Para Anthropic usa prompt caching no system prompt
    (que é grande e não muda entre runs). Quando _USE_BATCH=True, usa Message Batches API (50% off)."""
    if _provider_for(model) == "anthropic":
        if _USE_BATCH:
            return _anthropic_batch_single(model, system, user)
        kwargs_a = _anthropic_request_params(model, system, user)
        resp = _get_anthropic_client().messages.create(**kwargs_a)
        text = "".join(b.text for b in resp.content if hasattr(b, "text"))
        _accumulate_usage({
            "cache_creation_input_tokens": getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        })
        return text
    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if _model_supports_temperature(model):
        kwargs["temperature"] = TEMP
    resp = _get_openai_client().chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


def _print_usage_summary() -> None:
    if not any(USAGE_TOTALS.values()):
        return
    cache_read = USAGE_TOTALS["cache_read_input_tokens"]
    cache_create = USAGE_TOTALS["cache_creation_input_tokens"]
    inp = USAGE_TOTALS["input_tokens"]
    out = USAGE_TOTALS["output_tokens"]
    print()
    print("=" * 60)
    print("[Uso Anthropic — totais]")
    print(f"  Input (não cacheado):       {inp:>10,} tokens")
    print(f"  Cache create (escrita):     {cache_create:>10,} tokens")
    print(f"  Cache read (hit):           {cache_read:>10,} tokens (~90% off)")
    print(f"  Output:                     {out:>10,} tokens")
    print("=" * 60)

# =========================
# Ajuste LOCAL de datasets (CSV/JSON) no TXT
# =========================
def _looks_like_date(s: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", s.strip()))

def _parse_csv(csv_text: str) -> Tuple[List[str], List[List[str]]]:
    buf = io.StringIO(csv_text.strip())
    reader = _csv.reader(buf)
    rows = list(reader)
    if not rows:
        return [], []
    header = rows[0]
    data = rows[1:] if len(rows) > 1 else []
    return header, data

def _to_csv(header: List[str], rows: List[List[str]]) -> str:
    out = io.StringIO()
    writer = _csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()

def _infer_col_types(header: List[str], rows: List[List[str]]) -> List[str]:
    types = []
    for col_i, _ in enumerate(header):
        col_vals = [r[col_i] for r in rows if col_i < len(r)]
        t = "text"
        if col_vals and all(re.fullmatch(r"-?\d+", (v or "").strip() or "0") for v in col_vals):
            t = "int"
        elif col_vals and all(re.fullmatch(r"-?\d+(\.\d+)?", (v or "").strip() or "0.0") for v in col_vals):
            t = "float"
        elif col_vals and all(_looks_like_date(v) for v in col_vals if (v or "").strip()):
            t = "date"
        else:
            uniq = set([(v or "").strip() for v in col_vals if (v or "").strip()])
            if 0 < len(uniq) <= 15:
                t = "cat"
        types.append(t)
    return types

def _col_ranges(types: List[str], rows: List[List[str]]) -> Dict[int, Tuple[Any, Any]]:
    ranges: Dict[int, Tuple[Any, Any]] = {}
    for i, t in enumerate(types):
        vals = []
        for r in rows:
            if i >= len(r):
                continue
            v = (r[i] or "").strip()
            if not v:
                continue
            if t == "int":
                vals.append(int(v))
            elif t == "float":
                vals.append(float(v))
            elif t == "date":
                try:
                    vals.append(datetime.strptime(v, "%Y-%m-%d"))
                except Exception:
                    pass
        if vals:
            ranges[i] = (min(vals), max(vals))
    return ranges

def _synthesize_value(t: str, rng: Tuple[Any, Any], cats: List[str], base_text: str, row_idx: int) -> str:
    if t == "int":
        lo, hi = rng if rng else (0, 100)
        if lo == hi: hi = lo + 10
        return str(random.randint(lo, hi))
    if t == "float":
        lo, hi = rng if rng else (0.0, 100.0)
        if lo == hi: hi = lo + 10.0
        return f"{random.uniform(lo, hi):.2f}"
    if t == "date":
        lo, hi = rng if rng else (datetime(2023,1,1), datetime(2023,3,1))
        span = (hi - lo).days or 30
        d = lo + timedelta(days=row_idx % (span+1))
        return d.strftime("%Y-%m-%d")
    if t == "cat" and cats:
        return random.choice(cats)
    base = base_text or "valor"
    return f"{base}_{row_idx}"

def _extend_or_trim_csv(csv_text: str) -> str:
    header, data = _parse_csv(csv_text)
    if not header:
        return csv_text.strip()
    data = [row for row in data if any((c or "").strip() for c in row)]
    if len(data) > MAX_ROWS:
        data = data[:MAX_ROWS]
        return _to_csv(header, data)
    if MIN_ROWS <= len(data) <= MAX_ROWS:
        return _to_csv(header, data)
    types = _infer_col_types(header, data) if data else ["text"] * len(header)
    ranges = _col_ranges(types, data) if data else {}
    cats_by_col: Dict[int, List[str]] = {}
    for i, t in enumerate(types):
        if t == "cat":
            cats_by_col[i] = list({(r[i].strip() if (i < len(r) and r[i]) else "") for r in data if (i < len(r) and r[i] and r[i].strip())})
    base_texts = [re.sub(r"\W+", "_", (h or "").lower()).strip("_") for h in header]
    if not data:
        for r_idx in range(MIN_ROWS):
            row = []
            for col_i, t in enumerate(types):
                row.append(_synthesize_value(t, ranges.get(col_i), cats_by_col.get(col_i, []), base_texts[col_i], r_idx))
            data.append(row)
        return _to_csv(header, data)
    seed_n = len(data)
    # Colunas-chave: valores todos distintos nos dados originais (provável id/código).
    # Ao ampliar, DUPLICAMOS linhas inteiras (preservando a correlação entre colunas —
    # essencial p/ tabelas de referência) e variamos APENAS as colunas-chave, para não
    # criar duplicatas de chave. Sintetizar cada coluna de forma independente (como antes)
    # embaralhava os campos e corrompia datasets de referência.
    uniq_cols = set()
    for col_i in range(len(header)):
        vals = [(r[col_i].strip() if col_i < len(r) and r[col_i] else "") for r in data]
        nonempty = [v for v in vals if v]
        if nonempty and len(set(nonempty)) == len(nonempty):
            uniq_cols.add(col_i)
    if not uniq_cols:
        # Sem coluna-chave: duplicar linhas só geraria duplicatas exatas (ex.: tabela de
        # referência pequena). Melhor manter o dataset coerente, ainda que < MIN_ROWS,
        # do que inflá-lo com redundância ou incoerência.
        return _to_csv(header, data)
    for r_idx in range(seed_n, MIN_ROWS):
        base = data[r_idx % seed_n]
        row = []
        for col_i, t in enumerate(types):
            base_val = base[col_i] if col_i < len(base) else ""
            if col_i in uniq_cols:
                if t in ("int", "float", "date"):
                    row.append(_synthesize_value(t, ranges.get(col_i), [], base_texts[col_i], r_idx))
                else:
                    base_clean = (base_val or "").strip() or base_texts[col_i] or "valor"
                    row.append(f"{base_clean}_{r_idx}")
            else:
                row.append(base_val)  # copia da linha base → preserva correlação entre colunas
        data.append(row)
    return _to_csv(header, data[:MAX_ROWS])

def _extend_or_trim_json_records(json_text: str) -> str:
    try:
        arr = json.loads(json_text)
        if not isinstance(arr, list):
            return json_text
        arr = [x for x in arr if isinstance(x, dict) and x]
        if len(arr) > MAX_ROWS:
            arr = arr[:MAX_ROWS]
            return json.dumps(arr, ensure_ascii=False)
        if MIN_ROWS <= len(arr) <= MAX_ROWS:
            return json.dumps(arr, ensure_ascii=False)
        if not arr:
            arr = [{"id": i+1, "valor": f"valor_{i+1}"} for i in range(MIN_ROWS)]
            return json.dumps(arr, ensure_ascii=False)
        keys = set()
        for o in arr:
            keys.update(o.keys())
        keys = list(keys)
        types: Dict[str, str] = {}
        for k in keys:
            vals = [o.get(k) for o in arr if k in o]
            t = "text"
            if vals and all(isinstance(v, int) for v in vals):
                t = "int"
            elif vals and all(isinstance(v, (int, float)) for v in vals):
                t = "float"
            elif vals and all(isinstance(v, str) and _looks_like_date(v) for v in vals):
                t = "date"
            elif vals and len({v for v in vals if v is not None}) <= 15:
                t = "cat"
            types[k] = t
        # Chaves com valores todos distintos = provável id. Ao ampliar, COPIAMOS o objeto
        # base inteiro (preserva correlação entre campos) e variamos só as chaves-id.
        uniq_keys = set()
        for k in keys:
            vals = [o.get(k) for o in arr if k in o and o.get(k) is not None]
            if vals and len({str(v) for v in vals}) == len(vals):
                uniq_keys.add(k)
        if not uniq_keys:
            # Sem chave-id: manter coerente (ainda que < MIN_ROWS) em vez de recombinar campos.
            return json.dumps(arr[:MAX_ROWS], ensure_ascii=False)
        seed = list(arr)
        while len(arr) < MIN_ROWS:
            i = len(arr)
            base = seed[i % len(seed)]
            new_obj: Dict[str, Any] = dict(base)  # copia → preserva correlação
            for k in uniq_keys:
                t = types.get(k, "text")
                if t == "int":
                    new_obj[k] = random.randint(0, 1000)
                elif t == "float":
                    new_obj[k] = round(random.uniform(0, 1000), 2)
                elif t == "date":
                    new_obj[k] = (datetime(2023, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
                else:
                    new_obj[k] = f"{base.get(k, k)}_{i}"
            arr.append(new_obj)
        return json.dumps(arr[:MAX_ROWS], ensure_ascii=False)
    except Exception as e:
        print(f"[AVISO] _extend_or_trim_json_records: JSON inválido mantido como está ({type(e).__name__}: {e})")
        return json_text

def _fix_datasets_in_txt(txt: str) -> str:
    """
    Procura blocos ```csv ... ``` e ```json ... ``` e ajusta 30–120 linhas.
    Retorna o TXT com blocos substituídos quando aplicável.
    """
    def repl_csv(m):
        content = m.group(1)
        fixed = _extend_or_trim_csv(content)
        return "```csv\n" + fixed + "```"

    def repl_json(m):
        content = m.group(1)
        fixed = _extend_or_trim_json_records(content)
        return "```json\n" + fixed + "```"

    # Ajusta CSV
    txt = re.sub(r"```csv\s+([\s\S]*?)```", lambda m: repl_csv(m), txt, flags=re.IGNORECASE)
    # Ajusta JSON
    txt = re.sub(r"```json\s+([\s\S]*?)```", lambda m: repl_json(m), txt, flags=re.IGNORECASE)
    return txt

# =========================
# Barra de progresso simples
# =========================
def _render_bar(current: int, total: int, width: int = 28) -> str:
    if total <= 0:
        total = 1
    ratio = max(0.0, min(1.0, current / total))
    filled = int(ratio * width)
    bar = "█" * filled + " " * (width - filled)
    pct = int(ratio * 100)
    return f"|{bar}| {current}/{total} ({pct}%)"

def _progress(title: str, current: int, total: int):
    print(f"\r{title} {_render_bar(current, total)}", end="", flush=True)

def _progress_done():
    print()

# =========================
# Núcleo
# =========================
# =========================
# Conclusão — gerada por LLM usando o texto padrão como estrutura fixa
# =========================
# Marcador que delimita a Conclusão no TXT (a Etapa 5 extrai o conteúdo após ele).
CONCLUSAO_MARKER = "<!-- CONCLUSAO -->"

# Texto-base (carreira de Análise de Dados). Serve como ESTRUTURA/ESQUELETO fixo:
# o LLM adapta só o específico de carreira (nome, nível, ferramentas, artefatos, hashtags).
CONCLUSAO_BASE = """## **Parabéns, Analista! Sua jornada continua!**

Chegamos ao fim deste **Checkpoint para o Nível 1 da Carreira de Análise de Dados**! Se você chegou até aqui, significa que dominou as ferramentas essenciais e as habilidades analíticas fundamentais para um Analista de Dados iniciante. **Parabéns!** Você transformou dados brutos em insights valiosos, resolveu problemas complexos e comunicou suas descobertas de forma eficaz. Isso é o que um verdadeiro profissional de dados faz!

### **Compartilhe seu sucesso!**

Este projeto é uma prova concreta das suas habilidades. Não guarde-o só para você! Compartilhe seu trabalho e aprendizado:

*   **GitHub:** Crie um repositório no GitHub para este projeto. Inclua seu código Python, as consultas SQL, os arquivos CSV (se permitido) e, o mais importante, um `README.md` detalhado. No `README`, explique o cenário de negócio, as perguntas que você respondeu, as ferramentas que utilizou, os desafios que enfrentou (e como os superou!), e os principais insights e recomendações que você gerou. Adicione screenshots do seu dashboard no Power BI!
*   **LinkedIn:** Publique sobre sua experiência com este projeto no LinkedIn. Descreva o que você aprendeu, os desafios superados e como você aplicou seus conhecimentos para resolver um problema de negócio. Marque as ferramentas que você utilizou (Pandas, MySQL, Power BI, Matplotlib) e use hashtags relevantes como #Alura #AnaliseDeDados #DataAnalytics #PowerBI #MySQL #Pandas #CarreiraEmDados.
*   **Portfólio:** Se você tem um portfólio online, adicione este projeto como um dos seus destaques. Ele demonstra sua capacidade de ir do dado bruto à recomendação estratégica.

### **O próximo nível da carreira**

Este projeto é apenas o começo. O mundo dos dados é vasto e cheio de oportunidades. Continue aprimorando suas habilidades e explorando novas áreas.

> Lembre-se: a curiosidade e a paixão por resolver problemas são os maiores combustíveis para um **Analista de Dados**. Continue aprendendo, continue construindo e continue crescendo!

**Sua jornada como Analista de Dados está apenas começando. Avance para o próximo nível!**"""


def gerar_conclusao(client, carreira: str, nivel: int, ferramentas: List[str], ultimo_nivel: bool = False) -> str:
    """Adapta o CONCLUSAO_BASE (Análise de Dados) para a carreira/nível via LLM.
    Preserva estrutura, seções, tom e formatação markdown; troca só o que é específico
    de carreira (nome, nível, ferramentas citadas, artefatos do GitHub, hashtags).
    Se `ultimo_nivel`, a Conclusão celebra o fim da carreira em vez de apontar 'próximo nível'."""
    if ultimo_nivel:
        regra_nivel = (
            "- ATENÇÃO — este é o ÚLTIMO nível desta carreira (a formação TERMINA aqui). NÃO fale em "
            "'próximo nível' nem 'avance para o próximo nível' e NÃO diga que a jornada 'está apenas "
            "começando'. Reescreva a seção que no texto-base é `### **O próximo nível da carreira**` com "
            "um título de encerramento (ex.: `### **E agora? Continue evoluindo**`), focando em aplicar "
            "tudo o que aprendeu no mercado, aprofundar-se e buscar novos desafios profissionais — não um "
            "próximo nível do checkpoint. A frase final deve CELEBRAR a conclusão da carreira/formação.\n"
        )
    else:
        regra_nivel = (
            "- Este NÃO é o último nível da carreira: mantenha o incentivo a avançar para o próximo "
            "nível, como no texto-base.\n"
        )
    system = (
        "Você adapta o texto de CONCLUSÃO de um checkpoint (fechamento motivacional de curso online) "
        "para uma carreira e nível específicos. Receberá um TEXTO-BASE (escrito para a carreira de "
        "Análise de Dados) e deve reescrevê-lo trocando SOMENTE o que é específico de carreira: o nome "
        "da carreira, o número do nível, as ferramentas citadas, os artefatos sugeridos para o GitHub "
        "(o que a pessoa sobe no repositório) e as hashtags do LinkedIn.\n\n"
        "REGRAS DURAS:\n"
        "- Preserve EXATAMENTE a mesma estrutura, as mesmas seções, a mesma ordem, o mesmo tom "
        "entusiasmado e a MESMA formatação markdown (títulos `##`/`###`, itens `*`, citação `>`, "
        "**negrito**). O resultado deve ter o mesmo esqueleto do texto-base.\n"
        "- Cite APENAS ferramentas da lista fornecida (não use ferramentas de outra carreira, como "
        "Power BI, SQL ou Pandas, a menos que estejam na lista).\n"
        "- Linguagem neutra e inclusiva ('profissional', 'pessoa'); nunca masculino genérico nem "
        "'você foi contratado'.\n"
        "- Não invente fatos, números, preços ou nomes de produtos fora da lista de ferramentas.\n"
        f"{regra_nivel}"
        "- Retorne APENAS o markdown final da conclusão, sem comentários nem cercas de código."
    )
    ferr = ", ".join(ferramentas) if ferramentas else "(não especificadas)"
    user = (
        f"Carreira: {carreira or '(não informada)'}\n"
        f"Nível: {nivel}{'  (ÚLTIMO nível da carreira)' if ultimo_nivel else ''}\n"
        f"Ferramentas desta carreira (use estas ao citar ferramentas e ao montar as hashtags): {ferr}\n\n"
        "TEXTO-BASE (mantenha esta estrutura e formatação; adapte só o conteúdo específico de carreira):\n\n"
        f"{CONCLUSAO_BASE}"
    )
    return _chat(client, MODEL_GEN, system, user).strip()


def gerar_aula3_txt(
    nivel: int,
    carreira: str,
    resumos_arquivo: str,
    domains: List[str],
    ferramentas_cli: Optional[List[str]],
    modo_dados: str = "auto",  # "auto" | "com" | "sem"
    perfil_modo: str = "auto",  # "auto" | "programatica" | "conceitual"
    formato: str = "projeto",  # "projeto" | "cases"
    ultimo_nivel: bool = False,
    verbose: bool = False,
) -> str:
    t_phase = {}
    t_start = time.perf_counter()

    load_dotenv()
    # Clients OpenAI/Anthropic são lazy — instanciados em _chat() conforme o MODEL.
    client = None

    # Fase 1 — Preparar insumos
    print("Fase 1/4: Preparando insumos...")
    t1 = time.perf_counter()
    resumos = _load_resumos_via_cli(resumos_arquivo, nivel, carreira)
    ferramentas = _derivar_ferramentas_permitidas(resumos, ferramentas_cli)
    resumos_json = json.dumps(_resumos_compactos(resumos), ensure_ascii=False)
    if len(resumos_json) > SINGLE_PASS_CHAR_LIMIT:
        resumos_json = resumos_json[:SINGLE_PASS_CHAR_LIMIT]
    if not domains:
        domains = DOMAINS_DEFAULT
    domains_list_formatada = "\n".join([f"- {d}" for d in domains])

    # Decide se envolve dados
    envolve_dados_auto = _carreira_envolve_dados(carreira, ferramentas, resumos)
    if modo_dados == "com":
        envolve_dados = True
    elif modo_dados == "sem":
        envolve_dados = False
    else:
        envolve_dados = envolve_dados_auto

    # Classifica perfil da carreira (programatica vs conceitual)
    if perfil_modo in ("programatica", "conceitual"):
        perfil = perfil_modo
        print(f"  → Perfil da carreira: {perfil} (override --perfil) | envolve dados: {envolve_dados}")
    else:
        perfil = _perfil_carreira(resumos)
        print(f"  → Perfil da carreira: {perfil} (auto) | envolve dados: {envolve_dados}")

    _progress("  → Insumos prontos", 1, 1)
    _progress_done()
    t_phase["prep"] = time.perf_counter() - t1

    # Fase 2 — Geração (1 chamada)
    print(f"Fase 2/4: Gerando texto da prova ({MODEL_GEN}, formato={formato})...")
    t2 = time.perf_counter()
    _progress("  → Solicitando ao modelo", 0, 1)
    if formato == "cases":
        _system = system_prompt_aula3_cases_txt()
        _user = user_prompt_aula3_cases_txt(
            nivel_str=f"Nível {nivel}",
            carreira_str=carreira or "",
            domains_list_formatada=domains_list_formatada,
            ferramentas_permitidas=ferramentas,
            resumos_json_do_nivel=resumos_json,
        )
    else:
        _system = system_prompt_aula3_txt()
        _user = user_prompt_aula3_txt(
            nivel_str=f"Nível {nivel}",
            carreira_str=carreira or "",
            domains_list_formatada=domains_list_formatada,
            ferramentas_permitidas=ferramentas,
            resumos_json_do_nivel=resumos_json,
            carreira_env_dados=envolve_dados,
            perfil_carreira=perfil,
        )
    if REFORCO_EXTRA:
        _system = f"{_system}\n\n# === REFORÇO INJETADO PELO REVISOR (rerun) ===\n{REFORCO_EXTRA}\n# === fim reforço injetado ==="
    txt = _chat(
        client,
        MODEL_GEN,
        _system,
        _user,
    )
    _progress("  → Solicitando ao modelo", 1, 1)
    _progress_done()
    t_phase["gen"] = time.perf_counter() - t2

    # Fase 3 — Pós-processo local (ajuste datasets 30–120, se houver)
    print("Fase 3/4: Ajustando datasets (se houver)...")
    t3 = time.perf_counter()
    # Contabiliza blocos antes para barra
    csv_blocks = len(re.findall(r"```csv\s+([\s\S]*?)```", txt, flags=re.IGNORECASE))
    json_blocks = len(re.findall(r"```json\s+([\s\S]*?)```", txt, flags=re.IGNORECASE))
    total_blocks = csv_blocks + json_blocks
    done_blocks = 0
    if total_blocks == 0:
        _progress("  → Nenhum dataset detectado", 1, 1)
        _progress_done()
    else:
        # processa CSV
        def repl_csv_progress(m):
            nonlocal done_blocks
            fixed = _extend_or_trim_csv(m.group(1))
            done_blocks += 1
            _progress("  → Ajustando datasets", done_blocks, total_blocks)
            return "```csv\n" + fixed + "```"
        txt = re.sub(r"```csv\s+([\s\S]*?)```", lambda m: repl_csv_progress(m), txt, flags=re.IGNORECASE)
        # processa JSON
        def repl_json_progress(m):
            nonlocal done_blocks
            fixed = _extend_or_trim_json_records(m.group(1))
            done_blocks += 1
            _progress("  → Ajustando datasets", done_blocks, total_blocks)
            return "```json\n" + fixed + "```"
        txt = re.sub(r"```json\s+([\s\S]*?)```", lambda m: repl_json_progress(m), txt, flags=re.IGNORECASE)
        if done_blocks < total_blocks:
            _progress("  → Ajustando datasets", total_blocks, total_blocks)
        _progress_done()
    t_phase["post"] = time.perf_counter() - t3

    # Fase 4 — Finalização: Conclusão personalizada (LLM adapta o texto-base à carreira/nível)
    print("Fase 4/4: Finalizando (gerando Conclusão personalizada)...")
    try:
        conclusao = gerar_conclusao(client, carreira or "", nivel, ferramentas, ultimo_nivel=ultimo_nivel)
        txt = txt.rstrip() + "\n\n" + CONCLUSAO_MARKER + "\n\n" + conclusao.strip() + "\n"
    except Exception as e:
        print(f"  ⚠ Falha ao gerar Conclusão ({type(e).__name__}: {e}); TXT segue sem conclusão — a Etapa 5 usa o fallback padrão.")
    t_phase["total"] = time.perf_counter() - t_start
    if verbose:
        print(f"[Tempos] prep={t_phase['prep']:.1f}s | gen={t_phase['gen']:.1f}s | post={t_phase['post']:.1f}s | total={t_phase['total']:.1f}s")
    return txt

# =========================
# CLI
# =========================
def main():
    parser = argparse.ArgumentParser(description="Gerar Aula 3 (Prova prática) — TXT, datasets apenas quando fizer sentido para a carreira, ajuste local 30–120, progresso e tempo total.")
    parser.add_argument("--nivel", type=int, choices=[1,2,3], required=True)
    parser.add_argument("--carreira", type=str, default="")
    parser.add_argument("--resumos_arquivo", type=str, default="", help="Caminho para JSON de resumos. Se omitido, deriva de output/<slug>_nivel_<n>/resumos.json.")
    parser.add_argument("--reforco_extra", type=str, default="", help="(Interno — usado pelo revisor) Caminho para arquivo com texto de reforço a concatenar ao system prompt.")
    parser.add_argument("--domains_arquivo", type=str, default="")
    parser.add_argument("--ferramentas_arquivo", type=str, default="")
    parser.add_argument("--modo_dados", type=str, choices=["auto","com","sem"], default="auto", help="auto (padrão): detecta pelo contexto; com: força datasets; sem: nunca gera datasets.")
    parser.add_argument("--perfil", type=str, choices=["auto","programatica","conceitual"], default="auto", help="auto (padrão): heurística por %% de conteúdos procedimentais; programatica: força entregáveis práticos (código/scripts/automação); conceitual: força entregáveis documentais/diagramáticos.")
    parser.add_argument("--ultimo-nivel", dest="ultimo_nivel", action="store_true", help="Marca este como o ÚLTIMO nível da carreira: a Conclusão celebra o fim da formação em vez de apontar para um próximo nível (default: assume que há próximo nível).")
    parser.add_argument("--formato", type=str, choices=["projeto","cases"], default="projeto", help="projeto (padrão): projeto prático de implementação (fluxo testado); cases: prova de ANÁLISE DE CASES p/ carreiras conceituais (sem código/datasets, IA como copiloto). Combine com --perfil conceitual --modo_dados sem.")
    parser.add_argument("--batch", action="store_true", help="Anthropic apenas: usa Message Batches API (50%% off, latência 5-30min vs ~3min sync).")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.batch:
        if _provider_for(MODEL_GEN) != "anthropic":
            raise RuntimeError("--batch só funciona com modelos Anthropic (claude-*).")
        global _USE_BATCH
        _USE_BATCH = True
        print("[Config] Modo batch ativado (50% off, latência maior).")

    # Reforço opcional (usado pelo revisor 4.5 no rerun automático)
    global REFORCO_EXTRA
    if args.reforco_extra:
        p = Path(args.reforco_extra)
        if not p.exists():
            raise FileNotFoundError(f"Arquivo de reforço não encontrado: {p}")
        REFORCO_EXTRA = p.read_text(encoding="utf-8").strip()
        print(f"[Reforço] Injetado a partir de {p} ({len(REFORCO_EXTRA)} chars).")

    # Domínios
    if args.domains_arquivo:
        p = Path(args.domains_arquivo)
        if not p.exists():
            raise FileNotFoundError(f"Arquivo de domínios não encontrado: {p}")
        domains = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(domains, list):
            raise ValueError("O arquivo de domínios deve conter uma lista JSON de strings.")
    else:
        domains = DOMAINS_DEFAULT

    # Ferramentas permitidas
    if args.ferramentas_arquivo:
        p = Path(args.ferramentas_arquivo)
        if not p.exists():
            raise FileNotFoundError(f"Arquivo de ferramentas não encontrado: {p}")
        ferramentas_cli = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(ferramentas_cli, list):
            raise ValueError("O arquivo de ferramentas deve conter uma lista JSON de strings.")
    else:
        ferramentas_cli = None

    projeto_dir = _projeto_dir(args.carreira, args.nivel)
    projeto_dir.mkdir(parents=True, exist_ok=True)
    resumos_arquivo = args.resumos_arquivo or str(projeto_dir / "resumos.json")

    t0 = time.perf_counter()
    txt = gerar_aula3_txt(
        nivel=args.nivel,
        carreira=args.carreira,
        resumos_arquivo=resumos_arquivo,
        domains=domains,
        ferramentas_cli=ferramentas_cli,
        modo_dados=args.modo_dados,
        perfil_modo=args.perfil,
        formato=args.formato,
        ultimo_nivel=args.ultimo_nivel,
        verbose=args.verbose,
    )
    elapsed = time.perf_counter() - t0

    out_path = projeto_dir / "prova_pratica.txt"
    out_path.write_text(txt, encoding="utf-8")
    print(f"[OK] TXT salvo em: {out_path}")

    mins = int(elapsed // 60)
    secs = int(elapsed % 60)
    print(f"[Tempo total] {mins} min {secs} s")
    _print_usage_summary()

if __name__ == "__main__":
    main()
