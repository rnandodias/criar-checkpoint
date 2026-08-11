"""
ETAPA 4.7 — Turbinador de dataset da prova prática.

Substitui o dataset inline (30-120 linhas, escrito pelo LLM na etapa 4) por um
dataset gerado programaticamente, com volume e estrutura estatística reais.

NÃO inventa o cenário: a etapa 4 continua definindo domínio, colunas e semântica.
Este script apenas ELEVA a qualidade do que já foi proposto.

Divisão de trabalho:
  - o LLM **especifica** (lê enunciado + dataset proposto → JSON de especificação);
  - o código **gera** (numpy/pandas, semente fixa) e **valida** (esquema, correlações,
    proporções, integridade referencial, e se um modelo simples aprende);
  - o LLM faz uma **checagem semântica final** (valores plausíveis no domínio? o
    dataset é consistente com o que o enunciado afirma?);
  - só depois disso o TXT da prova é alterado.

Se qualquer camada reprovar, o TXT NÃO é tocado e o motivo é reportado.
Se a prova não tiver dataset (ex.: --formato cases), o script sai sem fazer nada.

Saídas na pasta do projeto:
  gerar_dataset.py        script determinístico de geração (vai no ZIP do coordenador)
  dataset/<nome>.csv      arquivos já gerados (o coordenador pode subir direto)
  prova_pratica.pre_turbo.txt   backup do TXT antes da substituição

No TXT, cada dataset inline vira: marcador de link + dicionário de dados + amostra.
O coordenador sobe os CSVs na nuvem e cola os links nos marcadores.

Uso:
    python scripts/turbinar_dataset_pratica.py --carreira "Engenharia de Machine Learning" --nivel 1
    python scripts/turbinar_dataset_pratica.py --carreira "..." --nivel 1 --linhas 15000
    python scripts/turbinar_dataset_pratica.py --carreira "..." --nivel 1 --dry-run
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

from gerar_prova_pratica_do_zero import (  # noqa: E402
    _chat,
    _print_usage_summary,
    _slugify,
    OUTPUT_BASE,
)

MODEL_TURBO = "claude-opus-4-6"

LINHAS_PADRAO = 12000
MARCADOR_LINK = "[INSERIR AQUI O LINK DE DOWNLOAD]"

# Correlação mínima para considerar que um efeito declarado de fato apareceu nos dados.
# Abaixo disso o fator existe na fórmula mas é irrelevante na prática — e o exercício
# de análise de importância de variáveis fica sem sentido.
CORRELACAO_MINIMA = 0.12
# Amostra exibida no TXT da prova, para a pessoa aluna ver o formato.
LINHAS_AMOSTRA = 10


# =========================
# Extração do dataset proposto
# =========================

_RE_BLOCO_DADOS = re.compile(r"```\s*(csv|json)\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)


def extrair_datasets(txt: str) -> List[Dict[str, Any]]:
    """Devolve os blocos ```csv/```json do TXT, com posição, para substituição posterior."""
    blocos = []
    for m in _RE_BLOCO_DADOS.finditer(txt):
        blocos.append({
            "formato": m.group(1).lower(),
            "conteudo": m.group(2).strip(),
            "inicio": m.start(),
            "fim": m.end(),
            "trecho_original": m.group(0),
        })
    return blocos


def _contexto_do_bloco(txt: str, inicio: int, janela: int = 1200) -> str:
    """Texto que antecede o bloco — costuma trazer o dicionário de dados e a
    descrição do que aquele arquivo representa no cenário."""
    return txt[max(0, inicio - janela):inicio]


# =========================
# Fase 1 — LLM especifica
# =========================

def system_prompt_especificacao() -> str:
    return """Você é uma pessoa especialista em geração de dados sintéticos realistas para avaliação educacional.

Receberá o enunciado de uma prova prática e o(s) dataset(s) de exemplo que foram escritos à mão nela
(pequenos, apenas ilustrativos). Sua tarefa é produzir uma ESPECIFICAÇÃO ESTRUTURADA que permita gerar
programaticamente uma versão desses dados com volume real e estrutura estatística coerente.

VOCÊ NÃO ESCREVE DADOS. Você descreve COMO os dados devem ser gerados.

REGRAS:
- Preserve o cenário: mesmas colunas, mesmos nomes, mesma semântica, mesmo papel no enunciado.
  Você pode acrescentar uma coluna APENAS se o enunciado a mencionar e ela estiver faltando.
- As relações entre variáveis devem refletir o que o ENUNCIADO afirma ou pressupõe. Se o enunciado
  diz que o tempo cresce com a distância, declare esse efeito. Não invente relações que o cenário
  não sustenta.

- **PLAUSIBILIDADE CAUSAL (o ponto mais importante desta tarefa).** As relações declaradas precisam
  corresponder ao que se observa no mundo real do domínio, tanto no SENTIDO quanto na FORÇA RELATIVA.
  Quem for resolver a prova vai analisar importância de variáveis e correlações — se os dados
  contradisserem a intuição do domínio, a pessoa chegará a conclusões absurdas e o exercício perde o
  valor. Alguns exemplos do tipo de raciocínio esperado: quando a renda cresce, o consumo tende a
  crescer; quando a demanda por um bem aumenta, seu preço tende a subir; a demanda de ontem é um
  preditor forte da demanda de hoje; chuva reduz circulação de pessoas; distância maior aumenta tempo
  de deslocamento.

- **ORDENE OS EFEITOS POR IMPORTÂNCIA.** No campo "importancia" de cada termo, declare de 1 a 5 quão
  determinante aquele fator é para o alvo NO MUNDO REAL (5 = fator dominante, 1 = influência marginal).
  A variável que a experiência de domínio aponta como principal preditor deve receber a maior
  importância. Os coeficientes devem ser coerentes com essa ordenação, considerando a escala de cada
  coluna: um coeficiente pequeno sobre uma coluna de valores grandes pode pesar mais que um
  coeficiente grande sobre uma coluna de valores pequenos. Pense no efeito final, não no número.

- No campo "explicacao" de cada termo, justifique a relação em uma frase, como faria ao defender a
  escolha para uma pessoa especialista do setor.
- Se o enunciado menciona defeitos nos dados (valores ausentes, outliers, desbalanceamento, duplicatas),
  declare-os na proporção que o texto indicar. Se não menciona, use proporções discretas e realistas.
- Faixas e categorias devem ser plausíveis no domínio real descrito (uma entrega urbana não tem 400 km).
- Para múltiplos arquivos com relação entre si, declare a chave e qual arquivo é o "pai".

- **A QUEM O VALOR PERTENCE.** Antes de descrever uma coluna, pergunte de quem é aquele valor. Se ele
  é da entidade e não muda com o tempo (a área da loja, a densidade da região), marque "por_entidade".
  Se ele é do calendário e vale para todas as entidades naquela data (feriado nacional, cotação do dia,
  campanha em rede nacional), marque "por_periodo". Errar isso produz uma base que se contradiz por
  dentro: a mesma data sendo feriado numa região e dia útil na vizinha, ou uma região que muda de
  tamanho todo dia. Quem for resolver a prova cruza essas colunas e percebe.

  Quando a coluna marca um evento de calendário que existe de verdade — feriado nacional, data
  comercial conhecida —, não basta sortear com uma probabilidade: liste as datas reais em
  "datas_marcadas", dentro do período coberto. Uma base que aponta feriado num dia comum, ou que
  ignora o 1º de janeiro, é conferida contra o calendário em segundos.

- **COLUNAS DERIVADAS.** Se uma coluna é extraída de outra — mês, ano, dia da semana ou hora obtidos
  de uma coluna de data —, declare isso em "derivada_de". Colunas assim NÃO podem ser sorteadas de
  forma independente: quem resolver a prova vai conferir, e um mês que não corresponde à data
  denuncia dado inconsistente e invalida qualquer análise temporal.

- **SÉRIES TEMPORAIS (leia com atenção se o cenário tem datas).** Se cada linha é a observação de uma
  entidade num instante — vendas por loja por dia, demanda por região por dia, sensor por hora —,
  preencha "serie_temporal" e "periodo_declarado".

  Você NÃO decide o tamanho da base. Declare apenas: a coluna de data, a coluna que identifica a
  entidade, quantas entidades o CENÁRIO descreve (se o enunciado diz "cinco regiões", são cinco) e a
  frequência. Em "periodo_declarado", copie a data inicial e a final que o ENUNCIADO afirma — não
  invente outras, e não estenda o calendário para alcançar volume. O código calcula quantos períodos
  cabem entre essas datas e ajusta o restante.

- **DEPENDÊNCIA TEMPORAL — descreva o comportamento, não o mecanismo.** Se o alvo é uma grandeza
  observada repetidamente ao longo do tempo (demanda, tráfego, consumo, ocupação, falhas, preço),
  ele quase nunca é um sorteio independente a cada período: carrega o nível do período anterior e
  costuma repetir ciclos. Preencha "dependencia_temporal" no alvo com o que o DOMÍNIO justifica:
  o quanto um período influencia o seguinte, quais ciclos existem e qual o comprimento de cada um
  (em número de períodos: 7 para um ciclo semanal em dados diários, 12 para um ciclo anual em dados
  mensais), e se há tendência de crescimento ou queda ao longo da série.

  Declare isso SOMENTE quando o domínio sustentar. Um cadastro de clientes, uma tabela de transações
  independentes ou uma base de imagens não têm dependência temporal — nesse caso, use null.

  **Isso vale para as FEATURES também, não só para o alvo.** Se uma coluna explicativa é ela própria
  uma grandeza acompanhada ao longo do tempo — pedidos recebidos, tráfego, temperatura, preço,
  ocupação —, declare "dependencia_temporal" nela com o mesmo formato. Uma feature sorteada de forma
  independente a cada período contamina tudo o que depende dela: se o alvo é função de uma variável
  sem memória, o alvo também não terá memória, por mais persistência que você declare nele.

- **RELAÇÕES QUE NÃO PODEM SER VIOLADAS.** Alguns pares de colunas têm uma relação que o mundo real
  não permite quebrar: entregas realizadas não passam de pedidos recebidos, aprovados não passam de
  inscritos, cliques não passam de impressões, valor pago não passa de valor total. Sorteio aleatório
  viola essas relações em parte das linhas, e uma única linha impossível desmoraliza a base inteira
  para quem estiver analisando. Declare cada uma dessas relações em "restricoes".

- **NÃO CRIE UM ATALHO PARA O ALVO.** Nenhuma coluna pode ser o alvo disfarçado — uma variável que,
  sozinha, determina o resultado quase por completo (por exemplo, "pedidos recebidos" quando o alvo é
  "pedidos entregues" e a taxa entre os dois é praticamente constante). Uma base assim é resolvida por
  uma única variável, e todo o trabalho de análise, engenharia de atributos e comparação de modelos
  perde a razão de existir. Se o cenário mencionar uma variável muito forte, mantenha-a, mas garanta
  que outros fatores respondam por parte relevante da variação.

- **DEFASAGENS DO ALVO (lag).** Se o cenário tem uma coluna do tipo "valor do período anterior"
  (vendas de ontem, demanda da semana passada), ela NÃO é uma variável independente: é o próprio alvo
  deslocado no tempo. Declare-a com "derivada_de": {"coluna": "<alvo>", "parte": "lag_1"} — o código
  a calculará a partir do histórico real, dentro de cada entidade. Nunca a inclua na fórmula do alvo,
  pois isso criaria uma definição circular. O mesmo vale para médias móveis.

- **ESCOLHA A DEFASAGEM PELO USO, NÃO PELO HÁBITO.** O tamanho do lag precisa ser compatível com a
  antecedência que a decisão do cenário exige. Se a previsão serve para dimensionar equipe, turno ou
  frota, a decisão é tomada com dias de antecedência e o modelo NÃO terá o valor de ontem disponível:
  use "lag_7" (ou maior). Um lag_1 só se justifica quando a decisão é tomada no próprio dia. Prefira
  "lag_7" quando o enunciado falar em planejamento, escala ou dimensionamento.

REGRAS DE SAÍDA (invioláveis):
1. Responda APENAS com UM objeto JSON válido. Sem texto antes ou depois, sem crases de markdown.
2. Primeiro caractere `{`, último `}`.

Schema:
{
  "arquivos": [
    {
      "nome": "<nome_do_arquivo.csv>",
      "papel": "<o que representa no cenário, 1 frase>",
      "linhas": <número de linhas — use o valor de referência do user prompt. Ignorado quando houver serie_temporal: nesse caso o código deriva o volume do período e das entidades>,
      "serie_temporal": {"coluna_data": "<coluna de data>", "coluna_entidade": "<coluna que identifica a entidade repetida ao longo do tempo, ou null>", "entidades": <quantas entidades distintas o cenário descreve>, "frequencia": "<diaria|semanal|mensal>"} ou null,
      "periodo_declarado": {"inicio": "<YYYY-MM-DD que o enunciado afirma>", "fim": "<YYYY-MM-DD que o enunciado afirma>"} ou null,
      "chave_primaria": "<coluna|null>",
      "chave_estrangeira": {"coluna": "<col>", "referencia_arquivo": "<arquivo.csv>", "referencia_coluna": "<col>"} ou null,
      "colunas": [
        {
          "nome": "<nome>",
          "tipo": "<int|float|categoria|data|datetime|bool|id>",
          "descricao": "<significado, 1 frase>",
          "distribuicao": "<uniforme|normal|lognormal|poisson|categorica|sequencial — 'sequencial' SÓ para identificador/chave primária, nunca para uma feature>",
          "parametros": {"min": <n>, "max": <n>, "media": <n>, "desvio": <n>, "lambda": <n>, "inicio": "<YYYY-MM-DD>", "fim": "<YYYY-MM-DD>"},
          "categorias": [{"valor": "<v>", "peso": <0-1>}],
          "decimais": <inteiro>,
          "papel": "<feature|alvo|identificador|temporal|auxiliar>",
          "por_entidade": <true se o valor é um atributo fixo da entidade, que não muda a cada período (a área de uma loja, a densidade de uma região); false ou ausente caso contrário>,
          "por_periodo": <true se o valor é um atributo do CALENDÁRIO, igual para todas as entidades naquela data (feriado nacional, cotação do dia, campanha veiculada em rede); false ou ausente caso contrário>,
          "dependencia_temporal": <mesmo formato do campo no alvo, quando esta feature também for uma grandeza que carrega o próprio passado; null ou ausente caso contrário>,
          "datas_marcadas": [<lista de datas "YYYY-MM-DD" em que esta coluna vale 1, para marcadores de calendário conhecidos como feriados; nas demais datas vale 0. Só para colunas 0/1 com por_periodo=true>],
          "derivada_de": {"coluna": "<coluna de origem>", "parte": "<ano|mes|dia|dia_semana|hora|semana|lag_1|lag_7|media_movel_7|media_movel_28>"} ou null
        }
      ],
      "alvo": {
        "coluna": "<nome da coluna alvo|null>",
        "tipo_tarefa": "<regressao|classificacao|null>",
        "formula": [
          {"termo": "<constante|coluna|categoria|temporal|interacao>", "coluna": "<nome ou null>", "coeficiente": <n>, "importancia": <1-5, quão determinante este fator é no mundo real>, "condicao": "<descrição da condição, ou null>", "explicacao": "<por que esta relação existe no domínio, em 1 frase>"}
        ],
        "ruido_fracao": <0.2-0.5 — quanto do desvio do sinal vira ruído; use 0.35 salvo motivo>,
        "limiar_classificacao": <n ou null>,
        "proporcao_classe_positiva": <0-1 ou null>,
        "dependencia_temporal": {
          "persistencia": <0 a 0.9 — quanto do nível de um período permanece no seguinte. 0.7-0.85 para demanda/tráfego/consumo, que mudam devagar; 0.2-0.4 para grandezas mais erráticas; 0 se cada período é independente>,
          "ciclos": [{"periodo_em_passos": <comprimento do ciclo em número de períodos>, "amplitude_relativa": <0 a 1, o tamanho da oscilação em relação à variação total>, "explicacao": "<que ciclo do mundo real é este>"}],
          "tendencia_relativa": <-0.5 a 0.5 — crescimento ou queda acumulados ao longo de toda a série; 0 se o nível é estável>,
          "explicacao": "<por que esta grandeza carrega o próprio passado, em 1 frase>"
        } ou null
      },
      "restricoes": [
        {"coluna": "<coluna limitada>", "tipo": "<menor_igual|maior_igual>", "referencia": "<coluna de referência>", "explicacao": "<por que o mundo real não permite violar isto>"}
      ],
      "defeitos": {
        "nulos": [{"coluna": "<col>", "proporcao": <0-1>, "motivo": "<falha de coleta etc>"}],
        "outliers": {"proporcao": <0-1>, "colunas": ["<col>"], "descricao": "<o que representam>"},
        "duplicatas": <proporcao 0-1>,
        "vazamento": {"coluna": "<col>", "descricao": "<por que essa coluna vaza o alvo>"} ou null
      }
    }
  ],
  "observacoes": "<qualquer coisa relevante para quem for gerar, 1-3 frases>"
}
"""


def user_prompt_especificacao(enunciado: str, blocos: List[Dict[str, Any]], txt: str, linhas: int) -> str:
    partes = [
        f"VOLUME DE REFERÊNCIA: gere especificação para aproximadamente {linhas} linhas no arquivo principal.",
        "",
        "ENUNCIADO DA PROVA (contexto do cenário):",
        "```",
        enunciado[:14000],
        "```",
        "",
        f"DATASET(S) DE EXEMPLO ESCRITOS NA PROVA ({len(blocos)}):",
    ]
    for i, b in enumerate(blocos, 1):
        ctx = _contexto_do_bloco(txt, b["inicio"])
        partes += [
            "",
            f"--- Dataset {i} (formato {b['formato']}) ---",
            "Texto que antecede o bloco na prova (dicionário de dados / descrição):",
            "```",
            ctx[-1200:],
            "```",
            "Conteúdo do bloco:",
            "```",
            b["conteudo"][:3000],
            "```",
        ]
    partes += ["", "Retorne SOMENTE o JSON da especificação."]
    return "\n".join(partes)


# =========================
# Fase 1.5 — o código assume as decisões estruturais
# =========================

_PASSO_DIAS = {"diaria": 1, "semanal": 7, "mensal": 30}
# Abaixo disto a base não sustenta separação temporal, validação cruzada nem
# comparação entre modelos — vale mais reprovar do que entregar dado inútil.
LINHAS_MINIMAS = 300


def _data_ou_none(v: Any):
    import pandas as pd
    try:
        return pd.Timestamp(v) if v else None
    except (ValueError, TypeError):
        return None


def normalizar_spec(spec: Dict[str, Any], linhas_alvo: int) -> Tuple[List[str], List[str]]:
    """Reescreve na especificação o que é decisão de estrutura, não de domínio.

    O modelo continua dono do conhecimento do cenário — quais fatores existem, com
    que sinal, com que força. Mas quantas linhas gerar, que calendário cobrir e se
    uma coluna pode ser um contador são consequências aritméticas do que o enunciado
    já afirma. Delegar isso ao modelo produziu séries de 33 anos e features que eram
    o índice da linha disfarçado.

    Devolve (avisos, erros). Erros abortam antes de gerar qualquer dado.
    """
    avisos: List[str] = []
    erros: List[str] = []

    for arq in spec.get("arquivos") or []:
        nome = arq.get("nome", "?")
        st = arq.get("serie_temporal") or {}

        # --- volume e calendário: derivados do que o enunciado declara ---
        if st.get("coluna_data"):
            freq = (st.get("frequencia") or "diaria").lower()
            passo = _PASSO_DIAS.get(freq, 1)
            per = arq.get("periodo_declarado") or {}
            ini, fim = _data_ou_none(per.get("inicio")), _data_ou_none(per.get("fim"))

            if ini and fim and fim > ini:
                periodos = int((fim - ini).days // passo) + 1
            else:
                periodos = int(st.get("periodos") or 0) or 365
                avisos.append(f"{nome}: o enunciado não declara um período legível; "
                              f"assumindo {periodos} períodos a partir de hoje-{periodos}")
            entidades = max(1, int(st.get("entidades") or 1))

            st["inicio"] = ini.strftime("%Y-%m-%d") if ini else st.get("inicio")
            st["fim"] = fim.strftime("%Y-%m-%d") if fim else None
            st["periodos"] = periodos
            st["entidades"] = entidades
            arq["serie_temporal"] = st

            total = periodos * entidades
            arq["linhas"] = total
            if total < LINHAS_MINIMAS:
                erros.append(
                    f"{nome}: o período e as entidades declarados no enunciado rendem apenas "
                    f"{total} linhas ({entidades} entidade(s) × {periodos} período(s)). "
                    f"Abaixo de {LINHAS_MINIMAS} a base não sustenta o que a prova pede. "
                    f"O enunciado precisa declarar um histórico maior.")
            elif total < linhas_alvo * 0.5:
                avisos.append(
                    f"{nome}: {total} linhas — abaixo do volume de referência ({linhas_alvo}), "
                    f"mas fiel ao período e às entidades que o enunciado declara. "
                    f"Volume é consequência do cenário, não meta.")

        # --- natureza mecânica das colunas ---
        for col in arq.get("colunas") or []:
            papel = (col.get("papel") or "").lower()
            eh_id = papel == "identificador" or col.get("tipo") == "id" or col["nome"] == arq.get("chave_primaria")
            if (col.get("distribuicao") or "").lower() == "sequencial" and not eh_id:
                p = col.get("parametros") or {}
                col["distribuicao"] = "normal" if p.get("media") is not None else "uniforme"
                avisos.append(
                    f"{nome}.{col['nome']}: distribuição 'sequencial' rebaixada para "
                    f"'{col['distribuicao']}' — só identificadores podem ser contadores. "
                    f"Uma feature sequencial vira o índice da linha e passa a prever o alvo "
                    f"por artefato da ordenação.")

        # --- coerência interna da fórmula do alvo ---
        # O efeito de uma coluna é o AGREGADO dos seus termos. Uma variável categórica
        # costuma aparecer uma vez por categoria, e um coeficiente zero ali é a categoria
        # neutra — perfeitamente legítimo. Só há contradição quando a coluna inteira soma
        # efeito nulo apesar de declarada como determinante.
        alvo = arq.get("alvo") or {}
        por_coluna: Dict[str, Dict[str, float]] = {}
        for termo in list(alvo.get("formula") or []):
            col = termo.get("coluna")
            if not col:
                continue
            agr = por_coluna.setdefault(col, {"soma_abs": 0.0, "imp": 0.0, "termos": 0})
            agr["soma_abs"] += abs(float(termo.get("coeficiente") or 0))
            agr["imp"] = max(agr["imp"], float(termo.get("importancia") or 0))
            agr["termos"] += 1
        for col, agr in por_coluna.items():
            if agr["soma_abs"] == 0 and agr["imp"] >= 3:
                erros.append(
                    f"{nome}.{col}: declarado com importância {agr['imp']:.0f} mas todos os "
                    f"{int(agr['termos'])} termo(s) têm coeficiente zero. A especificação se "
                    f"contradiz — o fator não teria efeito nenhum nos dados.")
            if agr["termos"] > 1:
                avisos.append(f"{nome}.{col}: {int(agr['termos'])} termos na fórmula; "
                              f"os efeitos serão somados.")

    return avisos, erros


# =========================
# Fase 2 — código gera o script
# =========================

CABECALHO_GERADOR = '''"""
Gerador do dataset da prova prática — {carreira}, nível {nivel}.

Gerado automaticamente pela etapa 4.7 do pipeline de checkpoints.
Determinístico: a mesma semente produz sempre exatamente os mesmos arquivos,
de modo que todas as pessoas estudantes trabalhem sobre os mesmos dados.

Uso:
    pip install numpy pandas
    python gerar_dataset.py            # grava os CSVs em ./dataset/
    python gerar_dataset.py --saida /caminho/desejado
"""
from __future__ import annotations
import argparse
import json
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

SEMENTE = {semente}
'''


def _py_literal(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def gerar_codigo(spec: Dict[str, Any], carreira: str, nivel: int, semente: int = 42) -> str:
    """Traduz a especificação num script Python determinístico e legível."""
    linhas: List[str] = [CABECALHO_GERADOR.format(carreira=carreira, nivel=nivel, semente=semente)]
    # A especificação vai embutida como JSON e é parseada em tempo de execução: escrevê-la
    # como literal Python quebraria em null/true/false, que não existem na linguagem.
    spec_json = json.dumps(spec, ensure_ascii=False, indent=2)
    linhas.append('SPEC = json.loads(r"""\n' + spec_json + '\n""")')
    linhas.append('''

def _coluna(rng, col, n, df):
    """Gera uma coluna a partir da especificação."""
    tipo = col.get("tipo")
    dist = col.get("distribuicao") or "uniforme"
    p = col.get("parametros") or {}
    # Datas primeiro: uma coluna de data marcada como "sequencial" viraria
    # um contador inteiro se a ordem destas condições fosse invertida.
    if tipo in ("data", "datetime"):
        ini = pd.Timestamp(p.get("inicio") or "2025-01-01")
        fim = pd.Timestamp(p.get("fim") or "2025-06-30")
        segundos = rng.integers(0, max(1, int((fim - ini).total_seconds())), size=n)
        s = ini + pd.to_timedelta(segundos, unit="s")
        return s.floor("min") if tipo == "datetime" else s.normalize()
    if tipo == "id" or dist == "sequencial":
        return np.arange(1, n + 1)
    if tipo == "categoria" or dist == "categorica":
        cats = col.get("categorias") or []
        if not cats:
            return np.array(["NA"] * n)
        valores = [c["valor"] for c in cats]
        pesos = np.array([float(c.get("peso") or 0) for c in cats], dtype=float)
        pesos = np.ones(len(valores)) / len(valores) if pesos.sum() <= 0 else pesos / pesos.sum()
        return rng.choice(valores, size=n, p=pesos)
    if tipo == "bool":
        return rng.random(n) < float(p.get("media") or 0.5)
    if dist == "normal":
        v = rng.normal(float(p.get("media") or 0), max(1e-9, float(p.get("desvio") or 1)), n)
    elif dist == "lognormal":
        media = max(1e-9, float(p.get("media") or 1))
        v = rng.lognormal(np.log(media), max(1e-9, float(p.get("desvio") or 0.4)), n)
    elif dist == "poisson":
        v = rng.poisson(max(1e-9, float(p.get("lambda") or p.get("media") or 1)), n).astype(float)
    else:
        v = rng.uniform(float(p.get("min") or 0), float(p.get("max") or 1), n)
    lo, hi = p.get("min"), p.get("max")
    if lo is not None:
        v = np.maximum(v, float(lo))
    if hi is not None:
        v = np.minimum(v, float(hi))
    if tipo == "int":
        return np.rint(v).astype(int)
    return np.round(v, int(col.get("decimais") if col.get("decimais") is not None else 2))


def _aplicar_formula(rng, df, alvo):
    """Constrói o alvo a partir dos termos declarados.

    Duas decisões importantes aqui:

    1. Cada efeito é acumulado SEPARADAMENTE, para que se possa medir quanto ele
       de fato contribui para a variação do alvo. Somar tudo numa variável só
       esconde efeitos que, por diferença de escala entre as colunas, acabam
       irrelevantes — o caso clássico é um coeficiente alto sobre uma coluna de
       faixa estreita perder para um coeficiente baixo sobre uma coluna de faixa
       ampla.

    2. O ruído é calibrado PELA FORÇA DO SINAL, não por um valor absoluto. Um
       desvio fixo pode ser irrelevante num alvo de escala grande e destruir toda
       a correlação num alvo de escala pequena. Aqui ele passa a ser uma fração
       do desvio do sinal, de modo que os efeitos declarados sempre apareçam nos
       dados — que é justamente o que dá valor ao exercício.
    """
    n = len(df)
    y = np.zeros(n, dtype=float)
    contribuicoes = {}
    efeitos = []
    for termo in (alvo.get("formula") or []):
        coef = float(termo.get("coeficiente") or 0)
        col = termo.get("coluna")
        kind = (termo.get("termo") or "").lower()
        if kind == "constante" or not col:
            y += coef
            continue
        if col not in df.columns:
            continue
        serie = df[col]
        # pd.api.types em vez de np.issubdtype: este último quebra com dtypes
        # próprios do pandas (StringDtype, por exemplo).
        eh_texto = pd.api.types.is_object_dtype(serie) or pd.api.types.is_string_dtype(serie)
        eh_data = pd.api.types.is_datetime64_any_dtype(serie)
        eh_numero = pd.api.types.is_numeric_dtype(serie) or pd.api.types.is_bool_dtype(serie)
        if kind == "categoria" or eh_texto:
            # efeito por categoria: cada valor recebe um deslocamento estável
            cats = pd.Series(serie).astype(str)
            # ordena numericamente quando as categorias são números: sorted() em texto
            # colocaria "10" antes de "2" e destruiria a progressão do efeito
            try:
                unicos = sorted(cats.unique(), key=lambda v: float(v))
            except (TypeError, ValueError):
                unicos = sorted(cats.unique())
            mapa = {c: coef * (i - (len(unicos) - 1) / 2) for i, c in enumerate(unicos)}
            efeito = cats.map(mapa).to_numpy(dtype=float)
        elif eh_data:
            dias = (serie - serie.min()).dt.total_seconds() / 86400.0
            efeito = coef * dias.to_numpy(dtype=float)
        elif eh_numero:
            efeito = coef * pd.to_numeric(serie, errors="coerce").fillna(0).to_numpy(dtype=float)
        else:
            continue
        efeitos.append({"coluna": col, "valores": efeito,
                        "importancia": float(termo.get("importancia") or 0)})
    # Reponderação pela importância declarada. Sem isto, quem manda na variação do
    # alvo é a escala bruta de cada coluna: um fator secundário sobre valores grandes
    # abafa o preditor que o domínio considera principal, e a análise de importância
    # de variáveis passa a contradizer a realidade do negócio. Aqui cada efeito é
    # normalizado e reescalado para pesar conforme sua importância, preservando o
    # sinal (a direção da relação) declarado no coeficiente.
    com_peso = [e for e in efeitos if e["importancia"] > 0]
    if len(com_peso) >= 2:
        for e in efeitos:
            desvio_e = float(np.std(e["valores"]))
            if desvio_e <= 0:
                continue
            peso = e["importancia"] if e["importancia"] > 0 else 1.0
            e["valores"] = e["valores"] / desvio_e * peso
    for e in efeitos:
        y += e["valores"]
        contribuicoes[e["coluna"]] = contribuicoes.get(e["coluna"], 0.0) + float(np.std(e["valores"]))

    # Ruído proporcional ao sinal: garante que os efeitos declarados apareçam,
    # independentemente da escala do alvo. A fração pode ser ajustada na spec via
    # "ruido_fracao"; o padrão de 0,35 deixa o dado realista sem apagar as relações.
    sinal = float(np.std(y))
    fracao = float(alvo.get("ruido_fracao") or 0.35)
    desvio = sinal * fracao if sinal > 0 else max(1e-9, float(alvo.get("ruido_desvio") or 1))
    y = y + rng.normal(0, max(1e-9, desvio), n)
    return y, contribuicoes


def _processo_temporal(rng, df, dep, col_data, col_ent):
    """Componente do alvo que vem do próprio passado, e não das features.

    Sem isto, cada período é um sorteio independente: o alvo não guarda memória,
    e qualquer atributo defasado ou média móvel que se calcule a partir dele é
    ruído. Séries reais não se comportam assim — demanda, tráfego e consumo
    carregam o nível do período anterior e repetem ciclos. É essa estrutura que
    torna a defasagem um preditor legítimo em vez de uma coluna decorativa.

    Três componentes, todos declarados pelo domínio na especificação:
    persistência (o quanto de um período permanece no seguinte), ciclos (de
    comprimento declarado, em número de períodos) e tendência.
    """
    n = len(df)
    if not dep or n == 0:
        return np.zeros(n)
    persistencia = float(np.clip(float(dep.get("persistencia") or 0), 0.0, 0.9))
    tendencia = float(np.clip(float(dep.get("tendencia_relativa") or 0), -0.5, 0.5))
    ciclos = dep.get("ciclos") or []

    ordem = [c for c in (col_ent, col_data) if c and c in df.columns]
    idx_orig = df.index
    df_ord = df.sort_values(ordem) if ordem else df
    serie = pd.Series(0.0, index=df_ord.index)

    # Os ciclos são do CALENDÁRIO, não da entidade: segunda-feira é segunda-feira em
    # todas as regiões, e dezembro é dezembro em todas as lojas. Se cada entidade
    # recebesse a própria fase, os ciclos se cancelariam no agregado e o dia da semana
    # deixaria de prever qualquer coisa — que é exatamente o que a prova pede para a
    # pessoa aluna descobrir.
    datas_ord = (pd.to_datetime(df_ord[col_data], errors="coerce")
                 if col_data and col_data in df_ord.columns else None)
    if datas_ord is not None:
        passo_cal = (datas_ord - datas_ord.min()).dt.days.to_numpy(dtype=float)
    else:
        passo_cal = None

    grupos = ([g for _, g in df_ord.groupby(col_ent, sort=False)]
              if col_ent and col_ent in df_ord.columns else [df_ord])
    for g in grupos:
        m = len(g)
        if m == 0:
            continue
        # nível persistente: passeio amortecido em torno de zero. Cada entidade tem o
        # próprio nível, que é o que faz a defasagem prever dentro da entidade.
        choques = rng.normal(0, 1, m)
        nivel = np.zeros(m)
        acc = rng.normal(0, 1)
        for i in range(m):
            acc = persistencia * acc + choques[i]
            nivel[i] = acc
        if persistencia > 0:
            # reescala para desvio 1: a variância de um AR(1) cresce com a persistência,
            # e sem isso a força do componente dependeria do valor declarado
            desvio = float(np.std(nivel))
            nivel = nivel / desvio if desvio > 0 else nivel
        else:
            nivel = np.zeros(m)

        passo = np.arange(m, dtype=float)
        # posição no calendário quando houver data; senão, posição na sequência
        rel = passo_cal[df_ord.index.get_indexer(g.index)] if passo_cal is not None else passo
        sazonal = np.zeros(m)
        for ciclo in ciclos:
            p = float(ciclo.get("periodo_em_passos") or 0)
            amp = float(np.clip(float(ciclo.get("amplitude_relativa") or 0), 0.0, 1.0))
            if p >= 2 and amp > 0:
                sazonal += amp * np.sin(2 * np.pi * rel / p)

        linha = tendencia * (passo / max(1.0, m - 1)) * 2.0
        serie.loc[g.index] = nivel + sazonal + linha

    return serie.reindex(idx_orig).to_numpy(dtype=float)


def _aplicar_defeitos(rng, df, defeitos, protegidas):
    """Insere os defeitos declarados: duplicatas, outliers e valores ausentes."""
    dup = float(defeitos.get("duplicatas") or 0)
    if dup > 0:
        k = int(len(df) * dup)
        if k > 0:
            df = pd.concat([df, df.sample(k, random_state=SEMENTE)], ignore_index=True)
    out = defeitos.get("outliers") or {}
    prop = float(out.get("proporcao") or 0)
    if prop > 0:
        alvos = [c for c in (out.get("colunas") or []) if c in df.columns]
        idx = rng.choice(len(df), size=max(1, int(len(df) * prop)), replace=False)
        for c in alvos:
            if pd.api.types.is_numeric_dtype(df[c]):
                # teto de plausibilidade: outlier é valor extremo, não absurdo.
                # Sem isso, uma operação de 300 entregas/dia ganha registros de 4.000.
                base = df[c].astype(float)
                fator = rng.uniform(1.8, 2.6, size=len(idx))
                novos = base.iloc[idx].to_numpy() * fator
                teto = float(base.quantile(0.99)) * 3.0
                novos = np.minimum(novos, teto)
                # coluna inteira não aceita float no pandas 2.x: mantém o dtype original
                if pd.api.types.is_integer_dtype(df[c]):
                    novos = np.rint(novos).astype(df[c].dtype)
                df.loc[df.index[idx], c] = novos
    for nulo in (defeitos.get("nulos") or []):
        c, p = nulo.get("coluna"), float(nulo.get("proporcao") or 0)
        if c in df.columns and c not in protegidas and p > 0:
            idx = rng.choice(len(df), size=int(len(df) * p), replace=False)
            df.loc[df.index[idx], c] = np.nan
    return df


def _grid_temporal(st, arq):
    """Monta o esqueleto entidade x período. Cada linha passa a ser uma observação
    real no calendário, e não um sorteio solto: é isso que permite calcular
    defasagens e médias móveis com sentido."""
    freq = {"diaria": "D", "semanal": "W", "mensal": "MS"}.get((st.get("frequencia") or "diaria").lower(), "D")
    periodos = int(st.get("periodos") or 730)
    n_ent = max(1, int(st.get("entidades") or 1))
    col_data = st.get("coluna_data")
    col_ent = st.get("coluna_entidade")

    inicio = st.get("inicio")
    for c in arq["colunas"]:
        if c["nome"] == col_data:
            inicio = inicio or (c.get("parametros") or {}).get("inicio")
    try:
        base = pd.Timestamp(inicio) if inicio else pd.Timestamp("2023-01-01")
    except (ValueError, TypeError):
        base = pd.Timestamp("2023-01-01")
    datas = pd.date_range(start=base, periods=periodos, freq=freq)

    if col_ent:
        # Se o enunciado nomeia as entidades ("Norte", "Sul", "Centro"...), são esses os
        # valores que precisam sair no arquivo. Numerar de 1 a N criaria uma base que
        # contradiz o texto que a pessoa aluna está lendo — e transformaria uma coluna
        # categórica do cenário num inteiro ordenável que não significa nada.
        espec_ent = next((c for c in arq["colunas"] if c["nome"] == col_ent), {})
        rotulos = [c.get("valor") for c in (espec_ent.get("categorias") or []) if c.get("valor") is not None]
        ents = np.array(rotulos[:n_ent]) if len(rotulos) >= n_ent else np.arange(1, n_ent + 1)
        idx = pd.MultiIndex.from_product([ents, datas], names=[col_ent, col_data])
        df = idx.to_frame(index=False)
    else:
        df = pd.DataFrame({col_data: datas})
    return df.sort_values([c for c in (col_ent, col_data) if c]).reset_index(drop=True)


def gerar_arquivo(arq, gerados):
    # crc32 e não hash(): o hash de strings em Python é salteado por processo, então a
    # "semente fixa" mudava a cada execução e a base deixava de ser reproduzível — o
    # oposto do que este script promete a quem for regerar os dados.
    rng = np.random.default_rng(SEMENTE + zlib.crc32(arq["nome"].encode("utf-8")) % 10_000)
    st = arq.get("serie_temporal")
    if st and st.get("coluna_data"):
        df = _grid_temporal(st, arq)
        n = len(df)
        col_ent = st.get("coluna_entidade")
        col_data = st.get("coluna_data")
        # atributos da entidade são fixos no tempo: densidade da região não muda
        # a cada dia. Sortear por linha produziria uma região que muda de tamanho
        # diariamente, o que nenhuma análise por entidade sustentaria.
        for col in arq["colunas"]:
            if col["nome"] in df.columns or (col.get("papel") or "") == "alvo" or col.get("derivada_de"):
                continue
            # Atributo da entidade: um valor por entidade, repetido em todos os períodos.
            if col.get("por_entidade") and col_ent:
                ents = df[col_ent].unique()
                valores = _coluna(rng, col, len(ents), df)
                df[col["nome"]] = pd.Series(df[col_ent]).map(dict(zip(ents, valores)))
            # Atributo do calendário: um valor por data, igual para todas as entidades.
            # Sem isto, o mesmo dia sai como feriado numa região e dia útil na vizinha.
            elif col.get("por_periodo") and col_data in df.columns:
                marcadas = col.get("datas_marcadas") or []
                if marcadas:
                    # Marcador de calendário real (feriado, data comercial): as datas são
                    # dadas, não sorteadas. Um 1º de janeiro que não é feriado se descobre
                    # na primeira conferência.
                    alvo_datas = pd.to_datetime(pd.Series(list(marcadas)), errors="coerce").dropna()
                    df[col["nome"]] = pd.to_datetime(df[col_data]).dt.normalize().isin(
                        set(alvo_datas.dt.normalize())).astype(int)
                else:
                    datas_unicas = df[col_data].unique()
                    valores = _coluna(rng, col, len(datas_unicas), df)
                    df[col["nome"]] = pd.Series(df[col_data]).map(dict(zip(datas_unicas, valores)))
    else:
        n = int(arq.get("linhas") or 5000)
        df = pd.DataFrame()

    for col in arq["colunas"]:
        if (col.get("papel") or "") == "alvo" or col.get("derivada_de") or col["nome"] in df.columns:
            continue
        df[col["nome"]] = _coluna(rng, col, n, df)

    # Memória temporal não é privilégio do alvo. Uma feature que também é grandeza
    # observada ao longo do tempo — pedidos recebidos, tráfego, temperatura — carrega o
    # próprio passado. Se ela for sorteada de forma independente a cada período e o alvo
    # depender dela, o alvo herda a ausência de memória, e qualquer defasagem calculada
    # sobre ele vira ruído por mais que o alvo declare persistência.
    if st and st.get("coluna_data"):
        for col in arq["colunas"]:
            dep_c = col.get("dependencia_temporal")
            nome_c = col["nome"]
            if not dep_c or nome_c not in df.columns or (col.get("papel") or "") == "alvo":
                continue
            if not pd.api.types.is_numeric_dtype(df[nome_c]) or col.get("por_entidade"):
                continue
            _cdata, _cent = st.get("coluna_data"), st.get("coluna_entidade")
            if col.get("por_periodo"):
                # Atributo do calendário: a série existe no eixo das datas, uma
                # observação por período. Gerar por linha devolveria valores diferentes
                # para a mesma data em cada entidade e desfaria o que a coluna é.
                datas_unicas = pd.Index(pd.unique(df[_cdata])).sort_values()
                aux = pd.DataFrame({_cdata: datas_unicas})
                comp_datas = _processo_temporal(rng, aux, dep_c, _cdata, None)
                comp = pd.Series(df[_cdata]).map(dict(zip(datas_unicas, comp_datas))).to_numpy(dtype=float)
            else:
                comp = _processo_temporal(rng, df, dep_c, _cdata, _cent)
            if float(np.std(comp)) <= 0:
                continue
            base = pd.to_numeric(df[nome_c], errors="coerce").astype(float)
            media_b, desvio_b = float(base.mean()), float(base.std())
            if desvio_b <= 0:
                continue
            amplitude = sum(float(c.get("amplitude_relativa") or 0) for c in (dep_c.get("ciclos") or []))
            peso = float(np.clip(float(dep_c.get("persistencia") or 0) + amplitude, 0.0, 0.85))
            z_base = (base - media_b) / desvio_b
            z_temp = comp / float(np.std(comp))
            mistura = z_base * (1 - peso) + z_temp * peso
            valores = mistura / (float(np.std(mistura)) or 1.0) * desvio_b + media_b
            p_c = col.get("parametros") or {}
            if p_c.get("min") is not None:
                valores = np.maximum(valores, float(p_c["min"]))
            if p_c.get("max") is not None:
                valores = np.minimum(valores, float(p_c["max"]))
            df[nome_c] = (np.rint(valores).astype(int) if col.get("tipo") == "int"
                          else np.round(valores, int(col.get("decimais") if col.get("decimais") is not None else 2)))
            print(f"    {nome_c}: estrutura temporal aplicada (peso {peso:.2f})")

    # Colunas derivadas de data: calculadas a partir da origem, nunca sorteadas.
    # Um "mês" que não bate com a data quebra a coerência interna do dataset.
    _PARTES = {"ano": "year", "mes": "month", "dia": "day",
               "dia_semana": "dayofweek", "hora": "hour", "semana": "isocalendar"}
    for col in arq["colunas"]:
        d = col.get("derivada_de")
        if not d or not d.get("coluna") or d["coluna"] not in df.columns:
            continue
        if str(d.get("parte") or "").startswith(("lag_", "media_movel_")):
            continue  # dependem do alvo: calculadas depois que ele existe
        origem = pd.to_datetime(df[d["coluna"]], errors="coerce")
        parte = _PARTES.get((d.get("parte") or "").lower())
        if parte == "isocalendar":
            df[col["nome"]] = origem.dt.isocalendar().week.astype(int)
        elif parte:
            df[col["nome"]] = getattr(origem.dt, parte).astype(int)

    fk = arq.get("chave_estrangeira")
    if fk and fk.get("referencia_arquivo") in gerados:
        pai = gerados[fk["referencia_arquivo"]]
        ref = fk.get("referencia_coluna")
        if ref in pai.columns:
            df[fk["coluna"]] = rng.choice(pai[ref].to_numpy(), size=len(df))

    alvo = arq.get("alvo") or {}
    if alvo.get("coluna"):
        y, contribuicoes = _aplicar_formula(rng, df, alvo)
        # Componente que vem do próprio passado da série. Entra com peso derivado do que
        # o domínio declarou: quanto maior a persistência e a amplitude dos ciclos, mais
        # o histórico manda no resultado — que é o que se observa em demanda real e o
        # que dá sentido a uma feature defasada.
        dep = alvo.get("dependencia_temporal")
        if dep and st and st.get("coluna_data"):
            comp = _processo_temporal(rng, df, dep, st.get("coluna_data"), st.get("coluna_entidade"))
            if float(np.std(comp)) > 0:
                imps = [float(t.get("importancia") or 0) for t in (alvo.get("formula") or [])]
                maior = max(imps) if imps else 3.0
                amplitude = sum(float(c.get("amplitude_relativa") or 0) for c in (dep.get("ciclos") or []))
                peso = maior * min(1.2, float(dep.get("persistencia") or 0) + amplitude)
                comp = comp / float(np.std(comp)) * peso
                y = y + comp
                contribuicoes["(histórico e sazonalidade)"] = float(np.std(comp))
        for _c, _v in sorted(contribuicoes.items(), key=lambda kv: -kv[1]):
            print(f"    efeito de {_c}: desvio {_v:.2f}")
        if (alvo.get("tipo_tarefa") or "") == "classificacao":
            prop = alvo.get("proporcao_classe_positiva")
            limiar = np.quantile(y, 1 - float(prop)) if prop else float(alvo.get("limiar_classificacao") or np.median(y))
            df[alvo["coluna"]] = (y >= limiar).astype(int)
        else:
            especificacao = next((c for c in arq["colunas"] if c["nome"] == alvo["coluna"]), {})
            p = especificacao.get("parametros") or {}
            # Reescala para a faixa declarada em vez de cortar nos limites.
            # Cortar satura uma fatia grande dos registros num mesmo valor e destrói
            # as correlações — todos os saturados passam a ter o alvo idêntico,
            # independentemente das features. Reescalar é transformação linear:
            # move a distribuição para a faixa certa e preserva as relações.
            media_alvo, desvio_alvo = p.get("media"), p.get("desvio")

            # Quando o alvo é limitado por outra coluna (entregas <= pedidos), o que se
            # modela é a RAZÃO entre os dois, não o valor absoluto. Gerar o alvo com
            # escala própria e depois cortar no teto empilha uma fatia dos registros no
            # mesmo valor: as correlações se achatam e o corte vira o fenômeno dominante.
            # Multiplicar a referência por uma taxa faz a restrição valer por construção,
            # e o sinal das features passa a modular a taxa — que é como a coisa funciona
            # no mundo real, onde o que varia é a eficiência, não o limite físico.
            limite = next((r for r in (arq.get("restricoes") or [])
                           if r.get("coluna") == alvo["coluna"]
                           and (r.get("tipo") or "menor_igual").lower() == "menor_igual"
                           and r.get("referencia") in df.columns
                           and pd.api.types.is_numeric_dtype(df[r["referencia"]])), None)
            if limite is not None and float(np.std(y)) > 0:
                ref = pd.to_numeric(df[limite["referencia"]], errors="coerce").to_numpy(dtype=float)
                media_ref = float(np.nanmean(ref)) or 1.0
                taxa_media = float(np.clip((float(media_alvo) / media_ref) if media_alvo else 0.8,
                                           0.05, 0.95))
                z = (y - np.mean(y)) / np.std(y)
                # A dispersão da taxa é calibrada, não fixa: pouca variação faz o alvo
                # virar múltiplo da referência (e a prova, uma regra de três); variação
                # demais empurra a taxa contra o teto e satura. Procura-se o ponto em que
                # a referência continua sendo o fator principal sem explicar tudo sozinha.
                # Uma taxa média muito alta não deixa folga: encostada no teto, ela quase
                # não pode variar, a referência passa a explicar o alvo inteiro e os demais
                # fatores somem da análise. Quando isso acontece, baixa-se a taxa para abrir
                # espaço — a média do alvo fica menor que a declarada, e a reconciliação
                # atualiza o enunciado com o número real.
                melhor = None
                for escala in (1.0, 0.85, 0.7, 0.55):
                    tm = float(np.clip(taxa_media * escala, 0.05, 0.95))
                    folga = min(tm, 1.0 - tm)
                    for fator in (0.4, 0.6, 0.8, 1.0, 1.2, 1.5):
                        taxa_t = np.clip(tm + z * (folga * fator), 0.02, 1.0)
                        y_t = ref * taxa_t
                        saturado = float(np.mean(taxa_t >= 0.999))
                        if saturado > 0.05 or np.std(y_t) <= 0:
                            continue
                        r2 = float(np.corrcoef(ref, y_t)[0, 1] ** 2)
                        if melhor is None or abs(r2 - 0.45) < abs(melhor[1] - 0.45):
                            melhor = (y_t, r2, tm, fator, saturado)
                    # Só aceita parar quando a referência deixa espaço de verdade: com ela
                    # explicando mais que isso, os demais fatores declarados ficam abaixo do
                    # limiar de detecção e o validador — com razão — os acusa de ausentes.
                    if melhor is not None and melhor[1] <= 0.60:
                        break
                if melhor is None:
                    taxa_t = np.clip(taxa_media * 0.7 + z * 0.05, 0.02, 1.0)
                    melhor = (ref * taxa_t, float("nan"), taxa_media * 0.7, 0.0,
                              float(np.mean(taxa_t >= 0.999)))
                y, _r2, _tm, _f, _sat = melhor
                print(f"    alvo modelado como razão de {limite['referencia']} "
                      f"(taxa média {_tm:.2f}, dispersão x{_f}, "
                      f"R² da referência {_r2:.2f}, {_sat:.1%} no teto)")
            elif media_alvo is not None and desvio_alvo and float(np.std(y)) > 0:
                y = (y - np.mean(y)) / np.std(y) * float(desvio_alvo) + float(media_alvo)
            elif p.get("min") is not None and p.get("max") is not None and float(np.std(y)) > 0:
                lo, hi = float(p["min"]), float(p["max"])
                y = lo + (y - y.min()) / (y.max() - y.min() + 1e-12) * (hi - lo)
            # Piso físico (uma contagem não é negativa), mas por compressão, não por corte.
            # Igualar ao mínimo tudo o que ficou abaixo empilha uma barra no histograma
            # muitas vezes mais alta que a vizinhança — quem plotar a distribuição vê um
            # pico artificial e tenta explicá-lo como fenômeno do negócio. Aqui a cauda
            # inferior é comprimida numa faixa estreita acima do piso: a ordem entre os
            # registros se mantém e nenhum valor concentra massa.
            if p.get("min") is not None:
                piso = float(p["min"])
                abaixo = y < piso
                if abaixo.any():
                    menor = float(y.min())
                    prop = float(abaixo.mean())
                    acima = y[~abaixo]
                    # A faixa de destino acompanha a massa que precisa caber nela: espremer
                    # 8% dos registros no mesmo punhado de valores inteiros recria o pico
                    # que a compressão existe para evitar. O alvo é a densidade dos dados
                    # logo acima do piso.
                    if len(acima):
                        topo = float(np.quantile(acima, min(0.6, max(prop, 0.02))))
                    else:
                        topo = piso * 1.2
                    faixa = max(topo - piso, abs(piso) * 0.12, 1e-9)
                    span = piso - menor
                    y = np.where(abaixo,
                                 piso + ((y - menor) / (span if span > 0 else 1.0)) * faixa,
                                 y)
            # contagens são inteiras: um alvo "número de entregas" com casas decimais
            # denuncia dado sintético e confunde quem for analisar
            if especificacao.get("tipo") == "int":
                df[alvo["coluna"]] = np.rint(y).astype(int)
            else:
                df[alvo["coluna"]] = np.round(y, int(especificacao.get("decimais") or 2))

    # Relações que o domínio não permite violar (entregas <= pedidos, pagos <= devidos).
    for restricao in (arq.get("restricoes") or []):
        c, ref = restricao.get("coluna"), restricao.get("referencia")
        tipo = (restricao.get("tipo") or "menor_igual").lower()
        if c not in df.columns or ref not in df.columns:
            continue
        if not (pd.api.types.is_numeric_dtype(df[c]) and pd.api.types.is_numeric_dtype(df[ref])):
            continue
        antes = len(df)
        if tipo == "menor_igual":
            violando = df[c] > df[ref]
            df.loc[violando, c] = df.loc[violando, ref]
        else:
            violando = df[c] < df[ref]
            df.loc[violando, c] = df.loc[violando, ref]
        if violando.any():
            print(f"    restrição {c} {tipo} {ref}: {int(violando.sum())} de {antes} linhas ajustadas")
        if pd.api.types.is_integer_dtype(df[ref]) or (arq_tipo_int := next(
                (x.get("tipo") for x in arq["colunas"] if x["nome"] == c), None)) == "int":
            df[c] = np.rint(pd.to_numeric(df[c], errors="coerce")).astype("Int64").astype(int)

    protegidas = {alvo.get("coluna"), arq.get("chave_primaria")} - {None}
    df = _aplicar_defeitos(rng, df, arq.get("defeitos") or {}, protegidas)

    # Reaplica as restrições: os outliers acabaram de mexer no alvo e podem ter
    # recriado combinações impossíveis (mais entregas do que pedidos).
    for restricao in (arq.get("restricoes") or []):
        c, ref = restricao.get("coluna"), restricao.get("referencia")
        tipo = (restricao.get("tipo") or "menor_igual").lower()
        if c in df.columns and ref in df.columns and \
                pd.api.types.is_numeric_dtype(df[c]) and pd.api.types.is_numeric_dtype(df[ref]):
            viola = (df[c] > df[ref]) if tipo == "menor_igual" else (df[c] < df[ref])
            if viola.any():
                df.loc[viola, c] = df.loc[viola, ref]
                if pd.api.types.is_integer_dtype(df[ref]):
                    df[c] = np.rint(pd.to_numeric(df[c], errors="coerce")).astype(df[ref].dtype)

    # Defasagens e janelas móveis por ÚLTIMO, quando o alvo já tem seu valor final —
    # depois dos defeitos e das restrições. Calculá-las antes fazia a defasagem divergir
    # do histórico assim que qualquer etapa posterior alterasse o alvo: quem resolvesse a
    # prova faria shift() e obteria outros números. E são sempre tiradas do histórico
    # real, dentro de cada entidade e em ordem de tempo.
    if st and st.get("coluna_data"):
        col_data, col_ent = st.get("coluna_data"), st.get("coluna_entidade")
        df = df.sort_values([c for c in (col_ent, col_data) if c]).reset_index(drop=True)
        for col in arq["colunas"]:
            d = col.get("derivada_de") or {}
            parte = str(d.get("parte") or "")
            origem = d.get("coluna")
            if not origem or origem not in df.columns:
                continue
            grupo = df.groupby(col_ent)[origem] if col_ent else df[origem]
            if parte.startswith("lag_"):
                k = int(parte.split("_")[1] or 1)
                df[col["nome"]] = grupo.shift(k)
            elif parte.startswith("media_movel_"):
                j = int(parte.split("_")[-1] or 7)
                # shift(1) antes da janela: sem isso a média inclui o próprio dia
                # e vaza o alvo para dentro da feature
                base = grupo.shift(1)
                df[col["nome"]] = (base.groupby(df[col_ent]).rolling(j, min_periods=1).mean()
                                   .reset_index(level=0, drop=True) if col_ent
                                   else base.rolling(j, min_periods=1).mean())
            else:
                continue
            esp = next((c for c in arq["colunas"] if c["nome"] == col["nome"]), {})
            if esp.get("tipo") == "int":
                df[col["nome"]] = df[col["nome"]].round()

    temporais = [c["nome"] for c in arq["colunas"] if c.get("tipo") in ("data", "datetime") and c["nome"] in df.columns]
    if temporais:
        df = df.sort_values(temporais[0]).reset_index(drop=True)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--saida", default="dataset")
    args = ap.parse_args()
    destino = Path(args.saida)
    destino.mkdir(parents=True, exist_ok=True)

    gerados = {}
    # arquivos sem chave estrangeira primeiro, para que os "pais" existam
    ordenados = sorted(SPEC["arquivos"], key=lambda a: 1 if a.get("chave_estrangeira") else 0)
    for arq in ordenados:
        df = gerar_arquivo(arq, gerados)
        gerados[arq["nome"]] = df
        caminho = destino / arq["nome"]
        df.to_csv(caminho, index=False, encoding="utf-8")
        print(f"[OK] {caminho}  ({len(df)} linhas, {len(df.columns)} colunas)")


if __name__ == "__main__":
    main()
''')
    return "\n".join(linhas)


# =========================
# Fase 3 — validação por código
# =========================

def validar(spec: Dict[str, Any], pasta: Path) -> Tuple[bool, List[str]]:
    """Confere o que foi gerado contra o que foi especificado.
    Retorna (aprovado, mensagens). Mensagens com prefixo 'ERRO' reprovam."""
    import pandas as pd
    import numpy as np

    msgs: List[str] = []
    ok = True
    dfs: Dict[str, Any] = {}

    for arq in spec["arquivos"]:
        caminho = pasta / arq["nome"]
        if not caminho.exists():
            msgs.append(f"ERRO: arquivo {arq['nome']} não foi gerado")
            ok = False
            continue
        df = pd.read_csv(caminho)
        dfs[arq["nome"]] = df

        esperadas = {c["nome"] for c in arq["colunas"]}
        faltando = esperadas - set(df.columns)
        if faltando:
            msgs.append(f"ERRO: {arq['nome']} sem as colunas {sorted(faltando)}")
            ok = False

        # numa série temporal o tamanho é entidades x períodos, não o campo "linhas"
        _st = arq.get("serie_temporal") or {}
        if _st.get("coluna_data"):
            esperado = max(1, int(_st.get("entidades") or 1)) * int(_st.get("periodos") or 1)
        else:
            esperado = int(arq.get("linhas") or 5000)
        alvo_min = esperado * 0.8
        if len(df) < alvo_min:
            msgs.append(f"ERRO: {arq['nome']} tem {len(df)} linhas, abaixo do mínimo de {int(alvo_min)} (esperado {esperado})")
            ok = False
        else:
            msgs.append(f"ok: {arq['nome']} — {len(df)} linhas, {len(df.columns)} colunas")

        # proporção de nulos declarada
        for nulo in ((arq.get("defeitos") or {}).get("nulos") or []):
            c, p = nulo.get("coluna"), float(nulo.get("proporcao") or 0)
            if c in df.columns and p > 0:
                obs = df[c].isna().mean()
                if abs(obs - p) > max(0.05, p * 0.5):
                    msgs.append(f"ERRO: {arq['nome']}.{c} com {obs:.1%} de nulos; esperado ~{p:.1%}")
                    ok = False
                else:
                    msgs.append(f"ok: nulos em {c} = {obs:.1%}")

        # integridade referencial
        fk = arq.get("chave_estrangeira")
        if fk and fk.get("referencia_arquivo") in dfs:
            pai = dfs[fk["referencia_arquivo"]]
            col, ref = fk.get("coluna"), fk.get("referencia_coluna")
            if col in df.columns and ref in pai.columns:
                orfas = ~df[col].isin(pai[ref])
                if orfas.any():
                    msgs.append(f"ERRO: {arq['nome']}.{col} tem {orfas.sum()} chaves sem correspondência em {fk['referencia_arquivo']}")
                    ok = False
                else:
                    msgs.append(f"ok: integridade referencial {arq['nome']}.{col} → {fk['referencia_arquivo']}")

        # Nenhuma feature pode ser o índice da linha disfarçado. Uma coluna que só cresce
        # acompanha o tempo e passa a "prever" qualquer alvo que tenha tendência — a análise
        # de importância de variáveis então elege um contador como fator de negócio.
        for col in arq["colunas"]:
            nome_c = col["nome"]
            if nome_c not in df.columns or (col.get("papel") or "") != "feature":
                continue
            if not pd.api.types.is_numeric_dtype(df[nome_c]) or df[nome_c].nunique() < 3:
                continue
            r_ordem = float(pd.Series(np.arange(len(df)), index=df.index).corr(df[nome_c]))
            if not np.isnan(r_ordem) and abs(r_ordem) > 0.95:
                msgs.append(f"ERRO: {arq['nome']}.{nome_c} é monótona na ordem das linhas "
                            f"(correlação {r_ordem:+.2f} com o índice) — é um contador, não uma variável do cenário")
                ok = False

        # Relações impossíveis no domínio: uma única linha com mais entregas do que pedidos
        # desmoraliza a base inteira para quem estiver analisando.
        for restricao in (arq.get("restricoes") or []):
            c, ref = restricao.get("coluna"), restricao.get("referencia")
            tipo = (restricao.get("tipo") or "menor_igual").lower()
            if c not in df.columns or ref not in df.columns:
                continue
            if not (pd.api.types.is_numeric_dtype(df[c]) and pd.api.types.is_numeric_dtype(df[ref])):
                continue
            sub = df[[c, ref]].dropna()
            viola = (sub[c] > sub[ref]) if tipo == "menor_igual" else (sub[c] < sub[ref])
            if viola.any():
                msgs.append(f"ERRO: {arq['nome']} viola '{c} {tipo} {ref}' em {int(viola.sum())} linha(s) "
                            f"— relação impossível no domínio descrito")
                ok = False
            else:
                # Empatar com a referência é o rastro de quem foi cortado para caber. Muitos
                # empates significam que as escalas das duas colunas são incompatíveis na
                # especificação: uma fatia dos registros fica saturada no mesmo valor, as
                # correlações se achatam e o dado deixa de ser analisável.
                empate = float((sub[c] == sub[ref]).mean())
                if empate > 0.10:
                    msgs.append(
                        f"ERRO: {empate:.0%} das linhas têm {c} exatamente igual a {ref} — a escala "
                        f"declarada para {c} não cabe sob {ref} e o corte saturou a distribuição. "
                        f"Ajuste a média/desvio de {c} na especificação.")
                    ok = False
                else:
                    msgs.append(f"ok: restrição {c} {tipo} {ref} respeitada "
                                f"({empate:.1%} das linhas no limite)")

        # Atributo do calendário tem de valer para todas as entidades naquela data, e
        # atributo da entidade tem de ser estável ao longo do tempo. Violar isso produz
        # uma base que se contradiz por dentro — feriado numa região e dia útil na vizinha.
        _stx = arq.get("serie_temporal") or {}
        _cd, _ce = _stx.get("coluna_data"), _stx.get("coluna_entidade")
        for col in arq["colunas"]:
            nome_c = col["nome"]
            if nome_c not in df.columns:
                continue
            # nunique ignora nulos de propósito: um valor ausente é falha de coleta —
            # o sensor de uma região pode falhar num dia sem que a coluna deixe de ser
            # um atributo do calendário. Ausência já é validada como defeito declarado.
            if col.get("por_periodo") and _cd in df.columns:
                variam = df.groupby(_cd)[nome_c].nunique()
                if (variam > 1).any():
                    msgs.append(f"ERRO: {arq['nome']}.{nome_c} é atributo do calendário mas varia "
                                f"entre entidades em {(variam > 1).sum()} data(s)")
                    ok = False
            if col.get("por_entidade") and _ce and _ce in df.columns:
                variam = df.groupby(_ce)[nome_c].nunique()
                if (variam > 1).any():
                    msgs.append(f"ERRO: {arq['nome']}.{nome_c} é atributo da entidade mas muda ao "
                                f"longo do tempo em {(variam > 1).sum()} entidade(s)")
                    ok = False

        # O calendário gerado não pode extrapolar o que o enunciado declara.
        per = arq.get("periodo_declarado") or {}
        _st = arq.get("serie_temporal") or {}
        if per.get("inicio") and per.get("fim") and _st.get("coluna_data") in df.columns:
            d = pd.to_datetime(df[_st["coluna_data"]], errors="coerce")
            ini_d, fim_d = pd.Timestamp(per["inicio"]), pd.Timestamp(per["fim"])
            folga = pd.Timedelta(days=31)
            if d.min() < ini_d - folga or d.max() > fim_d + folga:
                msgs.append(f"ERRO: {arq['nome']} cobre de {d.min():%d/%m/%Y} a {d.max():%d/%m/%Y}, "
                            f"fora do período declarado no enunciado "
                            f"({ini_d:%d/%m/%Y} a {fim_d:%d/%m/%Y})")
                ok = False
            else:
                msgs.append(f"ok: período gerado dentro do declarado "
                            f"({d.min():%d/%m/%Y} a {d.max():%d/%m/%Y})")

        # coerência das defasagens: o lag precisa bater com o histórico real
        st = arq.get("serie_temporal") or {}
        col_ent = st.get("coluna_entidade")
        for col in arq["colunas"]:
            d = col.get("derivada_de") or {}
            parte = str(d.get("parte") or "")
            origem = d.get("coluna")
            if not parte.startswith("lag_") or not origem or origem not in df.columns:
                continue
            if col["nome"] not in df.columns:
                continue
            k = int(parte.split("_")[1] or 1)
            esperado = (df.groupby(col_ent)[origem].shift(k) if col_ent and col_ent in df.columns
                        else df[origem].shift(k))
            comparavel = df[[col["nome"]]].join(esperado.rename("_esp")).dropna()
            if len(comparavel) < 10:
                continue
            divergentes = (comparavel[col["nome"]].round() != comparavel["_esp"].round()).mean()
            if divergentes > 0.02:
                msgs.append(f"ERRO: {col['nome']} não corresponde a {origem} defasado em {k} "
                            f"({divergentes:.0%} das linhas divergem do histórico real)")
                ok = False
            else:
                msgs.append(f"ok: {col['nome']} confere com {origem} defasado em {k}")

        alvo = arq.get("alvo") or {}
        alvo_col = alvo.get("coluna")
        if not alvo_col or alvo_col not in df.columns:
            continue

        # Correlação declarada vs. observada. Não basta o sinal estar certo: o efeito
        # precisa APARECER nos dados, e os fatores que o domínio considera mais
        # determinantes precisam correlacionar mais forte que os secundários — senão
        # a análise de importância de variáveis leva a conclusões contrárias à realidade.
        # Agrega os termos por coluna antes de validar: o modelo costuma declarar
        # vários efeitos para a mesma variável (faixas sazonais, por exemplo), e o
        # que se observa na correlação é sempre o efeito LÍQUIDO — validar termo a
        # termo compararia a mesma correlação contra coeficientes parciais.
        por_coluna = {}
        for termo in (alvo.get("formula") or []):
            c = termo.get("coluna")
            if not c:
                continue
            agr = por_coluna.setdefault(c, {"coef": 0.0, "imp": 0.0})
            agr["coef"] += float(termo.get("coeficiente") or 0)
            agr["imp"] = max(agr["imp"], float(termo.get("importancia") or 0))

        observados = []
        for c, agr in por_coluna.items():
            coef = agr["coef"]
            imp = agr["imp"]
            if c not in df.columns or coef == 0:
                continue
            if not pd.api.types.is_numeric_dtype(df[c]):
                continue
            sub = df[[c, alvo_col]].dropna()
            if len(sub) < 50 or sub[c].nunique() < 3:
                continue
            r = float(sub[c].corr(sub[alvo_col]))
            if np.isnan(r):
                continue
            observados.append({"coluna": c, "coef": coef, "r": r, "importancia": imp})

            # Importância 1 é a declaração de que o fator é marginal. Cobrar dele efeito
            # visível — ou sinal estável, que a esta magnitude é ruído — seria punir a
            # especificação por ter sido honesta sobre o que pouco importa.
            if imp and imp <= 1 and abs(r) < 0.05:
                msgs.append(f"ok: {c} ↔ {alvo_col} r={r:+.2f} — efeito marginal, "
                            f"como a importância {imp:.0f} declara")
                continue

            if (coef > 0 and r < 0) or (coef < 0 and r > 0):
                msgs.append(f"ERRO: sinal invertido — {c} tem coeficiente {coef:+.2f} mas correlação {r:+.2f} com {alvo_col}")
                ok = False
                continue
            # Limiar proporcional à importância: um fator declarado como marginal
            # (importância 1) DEVE correlacionar fracamente — exigir dele a mesma força
            # de um fator dominante seria punir o comportamento correto.
            limiar = max(0.04, CORRELACAO_MINIMA * (imp / 3.0)) if imp > 0 else CORRELACAO_MINIMA
            if abs(r) < limiar:
                msgs.append(
                    f"ERRO: efeito ausente — {c} tem importância {imp:.0f} declarada, mas correlação "
                    f"de apenas {r:+.2f} com {alvo_col} (mínimo esperado {limiar:.2f}). "
                    f"O fator não influencia os dados na prática.")
                ok = False
            else:
                msgs.append(f"ok: {c} ↔ {alvo_col} r={r:+.2f} (coef {coef:+.2f}, importância {imp:.0f})")

        # coerência entre a importância declarada e a força observada
        com_imp = [o for o in observados if o["importancia"] > 0]
        if len(com_imp) >= 2:
            principal = max(com_imp, key=lambda o: o["importancia"])
            mais_forte = max(com_imp, key=lambda o: abs(o["r"]))
            if principal["coluna"] != mais_forte["coluna"] and                abs(mais_forte["r"]) > abs(principal["r"]) * 1.5:
                msgs.append(
                    f"ERRO: hierarquia invertida — '{principal['coluna']}' foi declarado o fator mais "
                    f"determinante (importância {principal['importancia']:.0f}) mas correlaciona "
                    f"{principal['r']:+.2f}, enquanto '{mais_forte['coluna']}' correlaciona "
                    f"{mais_forte['r']:+.2f}. A análise de importância de variáveis contradiria o domínio.")
                ok = False
            else:
                msgs.append(f"ok: hierarquia coerente — fator principal '{principal['coluna']}' r={principal['r']:+.2f}")

        # Nenhuma feature isolada pode resolver o problema sozinha. Um alvo que se explica
        # por uma única coluna dispensa análise, engenharia de atributos e comparação de
        # modelos — o exercício inteiro perde a razão de existir.
        if pd.api.types.is_numeric_dtype(df[alvo_col]):
            for c in df.columns:
                if c == alvo_col or not pd.api.types.is_numeric_dtype(df[c]):
                    continue
                if str(((next((x for x in arq["colunas"] if x["nome"] == c), {})).get("derivada_de") or {})
                       .get("parte") or "").startswith(("lag_", "media_movel_")):
                    continue  # o histórico do próprio alvo pode (e deve) ser forte
                sub = df[[c, alvo_col]].dropna()
                if len(sub) < 50 or sub[c].nunique() < 3:
                    continue
                r = float(sub[c].corr(sub[alvo_col]))
                if not np.isnan(r) and r ** 2 > 0.90:
                    msgs.append(
                        f"ERRO: {c} explica {r ** 2:.0%} da variação de {alvo_col} sozinha — "
                        f"é o alvo disfarçado. A prova ficaria resolvível com uma única variável.")
                    ok = False

        # Defasagens e médias móveis não entram na fórmula (seriam circulares), então nunca
        # foram avaliadas como preditores. Mas é justamente para elas que a prova pede
        # engenharia de atributos: se o alvo não guarda memória, a feature é decorativa.
        for col in arq["colunas"]:
            d = col.get("derivada_de") or {}
            parte = str(d.get("parte") or "")
            if not parte.startswith(("lag_", "media_movel_")) or col["nome"] not in df.columns:
                continue
            cols_lag = [col["nome"], alvo_col] + ([col_ent] if col_ent and col_ent in df.columns else [])
            sub = df[cols_lag].dropna()
            if len(sub) < 50:
                continue
            # A correlação tem de ser medida DENTRO da entidade, descontando a média de
            # cada uma. Sem isso, uma defasagem passa no teste apenas por carregar o nível
            # médio da região — ela estaria funcionando como identificador da entidade, e
            # não como memória temporal, que é o que a prova quer que a pessoa descubra.
            if col_ent and col_ent in sub.columns:
                a = sub[col["nome"]] - sub.groupby(col_ent)[col["nome"]].transform("mean")
                b = sub[alvo_col] - sub.groupby(col_ent)[alvo_col].transform("mean")
            else:
                a, b = sub[col["nome"]], sub[alvo_col]
            r = float(a.corr(b))
            if np.isnan(r) or abs(r) < CORRELACAO_MINIMA:
                msgs.append(
                    f"ERRO: {col['nome']} correlaciona {r:+.2f} com {alvo_col} dentro da própria "
                    f"entidade — o alvo não guarda memória entre períodos, então a defasagem não "
                    f"prevê nada além do nível médio. Declare 'dependencia_temporal' no alvo "
                    f"(persistência e ciclos) ou remova a coluna.")
                ok = False
            else:
                msgs.append(f"ok: {col['nome']} ↔ {alvo_col} r={r:+.2f} dentro da entidade "
                            f"(há memória temporal real)")

        # Ciclo declarado precisa ser visível: sem isso a sazonalidade é uma promessa do
        # enunciado que os dados não cumprem.
        dep = alvo.get("dependencia_temporal") or {}
        col_data_v = (arq.get("serie_temporal") or {}).get("coluna_data")
        if dep.get("ciclos") and col_data_v in df.columns and pd.api.types.is_numeric_dtype(df[alvo_col]):
            d = pd.to_datetime(df[col_data_v], errors="coerce")
            for ciclo in dep["ciclos"]:
                p = int(float(ciclo.get("periodo_em_passos") or 0))
                if p < 2:
                    continue
                fase = ((d - d.min()).dt.days % p)
                medias = df.groupby(fase)[alvo_col].mean()
                if len(medias) < 2:
                    continue
                # desvio entre as fases, e não amplitude bruta: com ciclos longos cada fase
                # tem poucas observações, e max-min mediria sobretudo o ruído amostral
                amplitude = float(medias.std() / (df[alvo_col].std() or 1))
                if amplitude < 0.05:
                    msgs.append(f"ERRO: o ciclo de {p} períodos foi declarado mas não aparece nos "
                                f"dados (variação de apenas {amplitude:.2f} desvio entre as fases)")
                    ok = False
                else:
                    msgs.append(f"ok: ciclo de {p} períodos visível ({amplitude:.2f} desvio entre fases)")

        # Nenhum valor isolado do alvo pode concentrar massa. Um pico no histograma é a
        # assinatura de corte — piso, teto ou limite —, e quem for analisar tentará
        # explicá-lo como comportamento do negócio, que é o oposto do que a base ensina.
        if pd.api.types.is_numeric_dtype(df[alvo_col]) and df[alvo_col].nunique() > 20:
            freq = df[alvo_col].value_counts(normalize=True)
            if len(freq) and float(freq.iloc[0]) > 0.03:
                msgs.append(
                    f"ERRO: o valor {freq.index[0]} concentra {freq.iloc[0]:.1%} das linhas de "
                    f"{alvo_col} — pico artificial de saturação (piso, teto ou limite), "
                    f"visível como uma barra isolada no histograma")
                ok = False
            else:
                msgs.append(f"ok: {alvo_col} sem pico de saturação "
                            f"(valor mais frequente em {float(freq.iloc[0]):.1%} das linhas)")

        # o alvo tem sinal aprendível?
        tarefa = (alvo.get("tipo_tarefa") or "").lower()
        if tarefa == "classificacao":
            prop = df[alvo_col].mean()
            if prop <= 0.01 or prop >= 0.99:
                msgs.append(f"ERRO: alvo {alvo_col} degenerado ({prop:.1%} da classe positiva)")
                ok = False
            else:
                msgs.append(f"ok: classe positiva em {prop:.1%}")
        elif tarefa == "regressao":
            if float(pd.to_numeric(df[alvo_col], errors="coerce").std() or 0) <= 0:
                msgs.append(f"ERRO: alvo {alvo_col} é constante")
                ok = False
            else:
                msgs.append(f"ok: alvo {alvo_col} com desvio {df[alvo_col].std():.2f}")

    return ok, msgs


# =========================
# Fase 4 — checagem semântica (LLM, escopo estreito)
# =========================

def system_prompt_semantica() -> str:
    return """Você confere se um dataset gerado faz sentido no cenário descrito por uma prova prática.

Seu escopo é ESTREITO. Responda apenas a duas perguntas:
1. Os VALORES são plausíveis no domínio real descrito? (ex.: uma entrega urbana com 400 km, uma idade
   de 180 anos, um preço negativo, uma data fora do período que o enunciado menciona)
2. O dataset é CONSISTENTE com o que o enunciado afirma? (ex.: o texto fala em quatro lojas e o
   arquivo tem onze; o texto promete dados de seis meses e há apenas duas semanas)

REGRA DA AUTORREFUTAÇÃO: se ao analisar você concluir que não há problema, NÃO registre nada.
Suspeita investigada e descartada não é achado — é ruído. Retornar lista vazia é o resultado esperado
quando o dataset está bom.

NÃO comente estatística, distribuição, correlação, volume ou qualidade preditiva: isso já foi
verificado por código antes de você. NÃO sugira melhorias de cenário. NÃO opine sobre a prova.

REGRAS DE SAÍDA: responda APENAS com um objeto JSON válido, começando com { e terminando com }.

Schema:
{
  "problemas": [
    {
      "analise": "<o que você verificou e a conclusão>",
      "procede": <true|false — registre apenas os true>,
      "arquivo": "<nome do arquivo>",
      "gravidade": "<alta|media|baixa>",
      "descricao": "<1 frase objetiva sobre o defeito confirmado>"
    }
  ]
}
"""


def user_prompt_semantica(enunciado: str, resumos_csv: str) -> str:
    return f"""ENUNCIADO DA PROVA (define o cenário):
```
{enunciado[:12000]}
```

DATASET GERADO — estatísticas e amostra de cada arquivo:
```
{resumos_csv[:12000]}
```

Retorne SOMENTE o JSON.
"""


_RE_ANULACAO = re.compile(
    r"desconsider|nenhum problema|n[ãa]o h[áa] problema|est[áa] (?:correto|adequado|plaus[íi]vel)|ap[óo]s reanálise",
    re.IGNORECASE,
)


def filtrar_autorrefutados(problemas: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    mantidos, descartados = [], 0
    for p in problemas:
        texto = f"{p.get('descricao', '')} {p.get('analise', '')}"
        if p.get("procede") is False or _RE_ANULACAO.search(p.get("descricao", "")):
            descartados += 1
            continue
        mantidos.append(p)
    return mantidos, descartados


def resumir_para_llm(spec: Dict[str, Any], pasta: Path) -> str:
    import pandas as pd
    partes = []
    for arq in spec["arquivos"]:
        caminho = pasta / arq["nome"]
        if not caminho.exists():
            continue
        df = pd.read_csv(caminho)
        partes.append(f"=== {arq['nome']} ({len(df)} linhas) ===")
        partes.append("Estatísticas:")
        partes.append(df.describe(include="all").to_string()[:2500])
        partes.append(f"Amostra ({LINHAS_AMOSTRA} linhas):")
        partes.append(df.head(LINHAS_AMOSTRA).to_string()[:2000])
        partes.append("")
    return "\n".join(partes)


# =========================
# Fase 4.5 — reconciliar o enunciado com os dados reais
# =========================

def system_prompt_reconciliacao() -> str:
    return """Você corrige a descrição de uma base de dados dentro do enunciado de uma prova.

A base foi regerada com volume e estrutura maiores, e o texto do enunciado ficou descrevendo a versão
antiga. Frases como "~100 linhas", "8 valores ausentes", "3 outliers acima de 950" ou "uma região"
tornaram-se falsas. Sua tarefa é reescrever esse trecho para que ele descreva com exatidão os dados
que realmente existem.

REGRAS:
- Reescreva SOMENTE o que descreve a base: período coberto, quantidade de registros, granularidade,
  significado das colunas e defeitos presentes. NÃO altere o cenário de negócio, as tarefas pedidas,
  nem o tom do texto.
- **Percorra o trecho afirmação por afirmação.** Toda frase que faz uma alegação verificável sobre os
  dados — quantidade, proporção, faixa de valores, distribuição, presença de um defeito — precisa ser
  conferida contra os fatos apresentados. Se a alegação não se sustenta, corrija o número; se o
  fenômeno simplesmente não existe mais na base nova, **remova a frase inteira**. Deixar uma afirmação
  falsa no texto é pior do que não descrever: quem for resolver a prova é instruído a conferir os
  defeitos documentados logo no primeiro passo, e vai encontrar outra coisa.
- Use os números REAIS informados. Prefira proporções a contagens absolutas quando o defeito for
  distribuído ("cerca de 1% dos registros" em vez de "8 registros"), porque a base pode ser regerada.
- Se a granularidade mudou (passou a ter várias entidades ao longo do tempo), descreva isso com
  clareza: quantas entidades, quantos períodos, e que cada linha é uma combinação das duas.
- Se alguma coluna passou a ser derivada de outra (o valor de períodos anteriores, por exemplo),
  explique isso no dicionário — inclusive que as primeiras linhas de cada entidade não têm esse valor.
- Mantenha a formatação markdown existente (títulos, listas, negrito).

REGRAS DE SAÍDA: responda APENAS com o texto corrigido, pronto para substituir o trecho original.
Sem comentários, sem crases de markdown envolvendo, sem explicação do que mudou."""


def user_prompt_reconciliacao(trecho: str, resumo_real: str) -> str:
    return f"""TRECHO ATUAL DO ENUNCIADO (descreve a base antiga):
```
{trecho}
```

DADOS QUE REALMENTE FORAM GERADOS:
```
{resumo_real}
```

Reescreva o trecho para descrever com exatidão os dados reais."""


def system_prompt_mencoes() -> str:
    return """Você localiza, no enunciado de uma prova prática, as frases que descrevem características
da base de dados que MUDARAM, e propõe a correção exata de cada uma.

A base foi regerada. A seção que a descreve já foi atualizada. Mas o enunciado fala dos dados em outros
pontos — nas tarefas, nas dicas, nas perguntas — e essas menções podem ter ficado falsas. Exemplos do
que procurar: uma tarefa que manda tratar um defeito que não existe mais; uma dica que cita um número
que mudou; uma instrução que manda conferir uma característica que a base nova não tem.

REGRAS:
- Você NÃO reescreve o documento. Você devolve substituições pontuais.
- Cada substituição precisa citar o trecho ORIGINAL **exatamente como ele aparece**, caractere por
  caractere, e ser curta o bastante para ser inequívoca (uma frase ou um item de lista).
- Corrija apenas o que a base nova tornou falso. Não melhore texto, não mude estilo, não altere o
  cenário de negócio nem o nível de exigência das tarefas.
- Se um defeito deixou de existir, a tarefa que o tratava não deve simplesmente sumir: adapte-a ao que
  a base realmente tem, preservando a habilidade avaliada. Uma tarefa de tratamento de outliers, numa
  base sem outliers plantados, vira uma tarefa de investigar a distribuição e decidir, com critério
  declarado, se há valores a tratar.
- Se nada estiver falso, devolva uma lista vazia.

REGRAS DE SAÍDA (invioláveis):
1. Responda APENAS com UM objeto JSON válido. Sem texto antes ou depois, sem crases de markdown.
2. Primeiro caractere `{`, último `}`.

Schema:
{"substituicoes": [{"trecho": "<texto original exato>", "substituto": "<texto corrigido>", "motivo": "<o que ficou falso, 1 frase>"}]}
"""


def user_prompt_mencoes(txt: str, resumo_real: str) -> str:
    return f"""FATOS REAIS DA BASE GERADA:
```
{resumo_real}
```

ENUNCIADO COMPLETO DA PROVA:
```
{txt[:24000]}
```

Localize as menções à base que ficaram falsas e devolva as substituições."""


def corrigir_mencoes(txt: str, spec: Dict[str, Any], pasta: Path) -> Tuple[str, List[str]]:
    """Acerta as menções aos dados espalhadas pelas tarefas e dicas.

    A reconciliação cuida da seção que descreve a base. Mas o enunciado volta a falar
    dos dados nas etapas — mandando tratar um defeito, conferir uma proporção — e essas
    frases também precisam ser verdadeiras. Aqui a alteração é cirúrgica: só substituições
    literais que casem uma única vez no texto, para que nada seja reescrito por engano.
    """
    raw = _chat(None, MODEL_TURBO, system_prompt_mencoes(),
                user_prompt_mencoes(txt, resumo_factual(spec, pasta)))
    parsed = _parse_json_tolerante(raw) or {}
    aplicadas: List[str] = []
    for sub in (parsed.get("substituicoes") or []):
        trecho, novo = sub.get("trecho"), sub.get("substituto")
        if not trecho or novo is None or trecho == novo:
            continue
        ocorrencias = txt.count(trecho)
        if ocorrencias == 1:
            txt = txt.replace(trecho, novo)
            aplicadas.append(f"✓ {sub.get('motivo', 'menção corrigida')}")
        elif ocorrencias == 0:
            aplicadas.append(f"⚠ não localizei literalmente: “{trecho[:60]}…” — revisar à mão")
        else:
            aplicadas.append(f"⚠ “{trecho[:60]}…” aparece {ocorrencias}x; não substituí para não "
                             f"alterar o ponto errado — revisar à mão")
    return txt, aplicadas


def resumo_factual(spec: Dict[str, Any], pasta: Path) -> str:
    """Fatos verificáveis sobre o que foi gerado — insumo da reconciliação."""
    import pandas as pd
    linhas = []
    for arq in spec["arquivos"]:
        caminho = pasta / arq["nome"]
        if not caminho.exists():
            continue
        df = pd.read_csv(caminho)
        st = arq.get("serie_temporal") or {}
        linhas.append(f"Arquivo: {arq['nome']}")
        linhas.append(f"  Registros: {len(df)}")
        if st.get("coluna_data") and st["coluna_data"] in df.columns:
            d = pd.to_datetime(df[st["coluna_data"]], errors="coerce")
            linhas.append(f"  Período: {d.min():%d/%m/%Y} a {d.max():%d/%m/%Y}")
        if st.get("coluna_entidade") and st["coluna_entidade"] in df.columns:
            ce = st["coluna_entidade"]
            contagem = df[ce].value_counts()
            linhas.append(f"  Entidades distintas em '{ce}': {df[ce].nunique()}")
            linhas.append(f"  Registros por entidade: " +
                          ", ".join(f"{k}={v}" for k, v in contagem.items()))
            if contagem.nunique() == 1:
                linhas.append(f"  ATENÇÃO: a cobertura é UNIFORME — todas as entidades têm "
                              f"exatamente {int(contagem.iloc[0])} registros. Qualquer afirmação de "
                              f"desbalanceamento entre entidades no texto é falsa e deve sair.")
            linhas.append(f"  Granularidade: uma linha por entidade e período")
        for c in df.columns:
            n = int(df[c].isna().sum())
            if n:
                linhas.append(f"  Valores ausentes em '{c}': {n} ({n/len(df):.1%})")
        for col in arq["colunas"]:
            d = col.get("derivada_de") or {}
            if str(d.get("parte") or "").startswith(("lag_", "media_movel_")):
                linhas.append(f"  '{col['nome']}' é derivada de '{d.get('coluna')}' ({d.get('parte')}) — "
                              f"calculada do histórico, ausente nas primeiras linhas de cada entidade")
        alvo = (arq.get("alvo") or {}).get("coluna")
        if alvo and alvo in df.columns:
            linhas.append(f"  Alvo '{alvo}': min {df[alvo].min():.0f}, max {df[alvo].max():.0f}, "
                          f"média {df[alvo].mean():.0f}")
        linhas.append("")
    return chr(10).join(linhas)


def reconciliar_enunciado(txt: str, blocos: List[Dict[str, Any]], spec: Dict[str, Any], pasta: Path) -> str:
    """Reescreve o trecho que descreve a base para bater com o que foi gerado.
    Sem isto, enunciado e dados se contradizem — e a contradição é visível para
    quem for resolver a prova."""
    if not blocos:
        return txt
    # A janela precisa cobrir a SEÇÃO que descreve a base — período, dicionário de
    # colunas e defeitos. Ancorar no último parágrafo em branco pegava só a linha vazia
    # colada no bloco de dados: o trecho saía vazio, a reconciliação era silenciosamente
    # abandonada e o enunciado seguia descrevendo a base antiga.
    inicio_bloco = blocos[0]["inicio"]
    janela = txt[max(0, inicio_bloco - 6000):inicio_bloco]
    marcadores = [m.start() for m in re.finditer(r"(?m)^#{2,4}\s+\S", janela)]
    if marcadores:
        inicio_desc = max(0, inicio_bloco - 6000) + marcadores[-1]
    else:
        corte = txt.rfind(chr(10) * 2, max(0, inicio_bloco - 2000), inicio_bloco)
        inicio_desc = corte + 2 if corte > 0 else max(0, inicio_bloco - 2000)
    trecho = txt[inicio_desc:inicio_bloco]
    if len(trecho.strip()) < 80:
        print("  ⚠ não localizei a seção que descreve a base; o enunciado seguirá "
              "descrevendo o dataset antigo e a checagem semântica deve reprovar")
        return txt
    novo = _chat(None, MODEL_TURBO, system_prompt_reconciliacao(),
                 user_prompt_reconciliacao(trecho, resumo_factual(spec, pasta))).strip()
    novo = re.sub(r"^```[a-z]*" + chr(10) + r"|" + chr(10) + r"```$", "", novo).strip()
    if len(novo) < 60:
        print("  ⚠ reconciliação devolveu texto curto demais; trecho original mantido")
        return txt
    return txt[:inicio_desc] + novo + chr(10)*2 + txt[blocos[0]["inicio"]:]


# =========================
# Fase 5 — substituição no TXT
# =========================

def bloco_substituto(arq: Dict[str, Any], df) -> str:
    """Marcador de link + dicionário de dados + amostra, no lugar do dataset inline."""
    linhas = [
        f"📥 **Base de dados: `{arq['nome']}`** — {MARCADOR_LINK}",
        "",
        f"*{arq.get('papel', '')}*" if arq.get("papel") else "",
        "",
        f"O arquivo tem **{len(df):,} registros** e as seguintes colunas:".replace(",", "."),
        "",
        "| Coluna | Tipo | Descrição |",
        "|---|---|---|",
    ]
    for col in arq["colunas"]:
        if col["nome"] not in df.columns:
            continue
        linhas.append(f"| `{col['nome']}` | {col.get('tipo', '')} | {col.get('descricao', '')} |")
    linhas += [
        "",
        f"Amostra das primeiras {LINHAS_AMOSTRA} linhas, para conferência do formato:",
        "",
        "```csv",
        df.head(LINHAS_AMOSTRA).to_csv(index=False).strip(),
        "```",
    ]
    return "\n".join([x for x in linhas if x is not None])


def substituir_no_txt(txt: str, blocos: List[Dict[str, Any]], spec: Dict[str, Any], pasta: Path) -> str:
    import pandas as pd
    arquivos = spec["arquivos"]
    novo = txt
    # de trás para frente, para não invalidar os offsets
    for i in range(len(blocos) - 1, -1, -1):
        if i >= len(arquivos):
            continue
        arq = arquivos[i]
        caminho = pasta / arq["nome"]
        if not caminho.exists():
            continue
        df = pd.read_csv(caminho)
        b = blocos[i]
        novo = novo[:b["inicio"]] + bloco_substituto(arq, df) + novo[b["fim"]:]
    return novo


# =========================
# CLI
# =========================

def _parse_json_tolerante(raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    for tentativa in (raw.strip(),):
        try:
            return json.loads(tentativa)
        except Exception:
            pass
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    ini = raw.find("{")
    if ini < 0:
        return None
    prof, dentro, esc = 0, False, False
    for j in range(ini, len(raw)):
        c = raw[j]
        if dentro:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                dentro = False
        else:
            if c == '"':
                dentro = True
            elif c == "{":
                prof += 1
            elif c == "}":
                prof -= 1
                if prof == 0:
                    try:
                        return json.loads(raw[ini:j + 1])
                    except Exception:
                        return None
    return None


def main():
    ap = argparse.ArgumentParser(description="Etapa 4.7 — turbina o dataset da prova prática.")
    ap.add_argument("--carreira", required=True)
    ap.add_argument("--nivel", type=int, choices=[1, 2, 3], required=True)
    ap.add_argument("--linhas", type=int, default=LINHAS_PADRAO,
                    help=f"Volume de referência do arquivo principal (padrão {LINHAS_PADRAO}).")
    ap.add_argument("--semente", type=int, default=42)
    ap.add_argument("--dry-run", action="store_true",
                    help="Gera e valida, mas NÃO altera a prova.")
    args = ap.parse_args()
    load_dotenv()

    projeto = OUTPUT_BASE / f"{_slugify(args.carreira)}_nivel_{args.nivel}"
    txt_path = projeto / "prova_pratica.txt"
    if not txt_path.exists():
        print(f"[ERRO] Não encontrei {txt_path}")
        sys.exit(1)

    txt = txt_path.read_text(encoding="utf-8")

    # Rodar duas vezes sobre a mesma prova duplicaria a seção: o bloco substituto inclui
    # uma amostra em ```csv, que a segunda passada leria como se fosse o dataset inline
    # original. Em vez de tentar distinguir os dois, recusa-se a reprocessar — o caminho
    # para refazer é restaurar o backup, que existe justamente para isso.
    if MARCADOR_LINK in txt:
        print("[4.7] Esta prova já foi turbinada (o marcador de link está presente).")
        print(f"      Para refazer, restaure {txt_path.parent / 'prova_pratica.pre_turbo.txt'} "
              f"sobre prova_pratica.txt e rode de novo.")
        return

    blocos = extrair_datasets(txt)
    if not blocos:
        print("[4.7] A prova não tem dataset inline (provavelmente formato cases). Nada a fazer.")
        return
    print(f"[4.7] {len(blocos)} bloco(s) de dados encontrado(s) na prova.")

    print("[Fase 1] Pedindo especificação ao modelo...")
    raw = _chat(None, MODEL_TURBO, system_prompt_especificacao(),
                user_prompt_especificacao(txt, blocos, txt, args.linhas))
    spec = _parse_json_tolerante(raw)
    if not spec or not spec.get("arquivos"):
        print("[ERRO] Não consegui obter uma especificação válida. TXT preservado.")
        sys.exit(2)
    print(f"  → especificação com {len(spec['arquivos'])} arquivo(s): "
          + ", ".join(a["nome"] for a in spec["arquivos"]))

    print("[Fase 1.5] Fixando a estrutura (volume, calendário e natureza das colunas)...")
    avisos, erros_spec = normalizar_spec(spec, args.linhas)
    for a in avisos:
        print("  ⚠ " + a)
    for e in erros_spec:
        print("  ✗ " + e)
    if erros_spec:
        print("[ERRO] A especificação é internamente inconsistente. TXT preservado.")
        sys.exit(2)

    print("[Fase 2] Gerando o script determinístico...")
    codigo = gerar_codigo(spec, args.carreira, args.nivel, args.semente)
    script_path = projeto / "gerar_dataset.py"
    script_path.write_text(codigo, encoding="utf-8")
    print(f"  → {script_path}")

    print("[Fase 3] Executando e validando...")
    destino = projeto / "dataset"
    r = subprocess.run([sys.executable, str(script_path), "--saida", str(destino)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("[ERRO] O script de geração falhou. TXT preservado.")
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        sys.exit(3)
    for linha in r.stdout.strip().splitlines():
        print("  " + linha)

    aprovado, msgs = validar(spec, destino)
    for m in msgs:
        print(("  ✗ " if m.startswith("ERRO") else "  ✓ ") + m)
    if not aprovado:
        print("[ERRO] Validação por código reprovou. TXT preservado.")
        sys.exit(4)

    # Reconcilia ANTES de validar a semântica: comparar os dados novos com a
    # descrição da base antiga reprovaria sempre, e pelo motivo errado.
    print("[Fase 3.5] Reconciliando a descrição da base com os dados reais...")
    txt_reconciliado = reconciliar_enunciado(txt, blocos, spec, destino)
    if txt_reconciliado != txt:
        print("  ✓ descrição atualizada (período, volume, granularidade e defeitos)")
    else:
        print("  ⚠ descrição não foi alterada")

    # A base também é mencionada fora da seção que a descreve — nas tarefas e nas dicas.
    # Uma etapa que manda tratar um defeito inexistente contradiz os dados tanto quanto
    # um número errado no dicionário, e é onde a pessoa aluna trava primeiro.
    print("[Fase 3.6] Conferindo as menções aos dados nas tarefas e dicas...")
    txt_reconciliado, mencoes = corrigir_mencoes(txt_reconciliado, spec, destino)
    for m in mencoes:
        print("  " + m)
    if not mencoes:
        print("  ✓ nenhuma menção ficou desatualizada")

    # A checagem semântica precisa ver a prova COMO ELA FICARÁ — com o bloco inline já
    # trocado pelo link, o dicionário e a amostra dos dados novos. Avaliar o texto ainda
    # com o dataset antigo faz o revisor cobrar que os dados novos contenham as linhas de
    # exemplo antigas, e reprovar por uma contradição que a própria fase 5 desfaria.
    blocos_atuais = extrair_datasets(txt_reconciliado)
    txt_final = substituir_no_txt(txt_reconciliado, blocos_atuais, spec, destino)

    # A prévia é gravada ANTES da checagem: se ela reprovar, é justamente este texto
    # que se precisa ler para entender o motivo.
    (projeto / "prova_pratica.reconciliada.preview.txt").write_text(txt_final, encoding="utf-8")
    print("  → prévia da prova já com o dataset novo em prova_pratica.reconciliada.preview.txt")

    print("[Fase 4] Checagem semântica...")
    raw2 = _chat(None, MODEL_TURBO, system_prompt_semantica(),
                 user_prompt_semantica(txt_final, resumir_para_llm(spec, destino)))
    parsed = _parse_json_tolerante(raw2) or {"problemas": []}
    problemas, descartados = filtrar_autorrefutados(parsed.get("problemas") or [])
    if descartados:
        print(f"  {descartados} apontamento(s) autorrefutado(s) descartado(s).")
    graves = [p for p in problemas if (p.get("gravidade") or "").lower() == "alta"]
    for p in problemas:
        print(f"  [{p.get('gravidade', '?')}] {p.get('arquivo', '')}: {p.get('descricao', '')}")
    if graves:
        print("[ERRO] Checagem semântica encontrou problema grave. TXT preservado.")
        sys.exit(5)
    if not problemas:
        print("  ✓ nenhum problema semântico.")

    if args.dry_run:
        print("[4.7] --dry-run: TXT não alterado. Script e CSVs disponíveis para conferência.")
        _print_usage_summary()
        return

    print("[Fase 5] Atualizando a prova...")
    (projeto / "prova_pratica.pre_turbo.txt").write_text(txt, encoding="utf-8")
    txt_path.write_text(txt_final, encoding="utf-8")
    print(f"  → backup em prova_pratica.pre_turbo.txt")
    print(f"  → {len(blocos_atuais)} bloco(s) substituído(s) por marcador de link + dicionário + amostra")
    print("\n[4.7] Concluído. O coordenador deve subir os CSVs e preencher os marcadores "
          f"'{MARCADOR_LINK}' na prova.")
    _print_usage_summary()


if __name__ == "__main__":
    main()
