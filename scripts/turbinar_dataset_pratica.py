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

- **COLUNAS DERIVADAS.** Se uma coluna é extraída de outra — mês, ano, dia da semana ou hora obtidos
  de uma coluna de data —, declare isso em "derivada_de". Colunas assim NÃO podem ser sorteadas de
  forma independente: quem resolver a prova vai conferir, e um mês que não corresponde à data
  denuncia dado inconsistente e invalida qualquer análise temporal.

- **SÉRIES TEMPORAIS (leia com atenção se o cenário tem datas).** Se cada linha é a observação de uma
  entidade num instante — vendas por loja por dia, demanda por região por dia, sensor por hora —,
  preencha "serie_temporal". O número de linhas passa a ser `entidades × periodos`: declare quantas
  entidades e quantos períodos de forma que o produto se aproxime do volume de referência pedido, e
  que ambos sejam plausíveis (730 dias em 2 anos; 16 regiões numa operação metropolitana).

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
      "linhas": <número inteiro de linhas sugerido; use o valor de referência informado no user prompt>,
      "serie_temporal": {"coluna_data": "<coluna de data>", "inicio": "<YYYY-MM-DD, primeira data da série>", "coluna_entidade": "<coluna que identifica a entidade repetida ao longo do tempo, ou null>", "entidades": <quantas entidades distintas>, "periodos": <quantos períodos de tempo>, "frequencia": "<diaria|semanal|mensal>"} ou null,
      "chave_primaria": "<coluna|null>",
      "chave_estrangeira": {"coluna": "<col>", "referencia_arquivo": "<arquivo.csv>", "referencia_coluna": "<col>"} ou null,
      "colunas": [
        {
          "nome": "<nome>",
          "tipo": "<int|float|categoria|data|datetime|bool|id>",
          "descricao": "<significado, 1 frase>",
          "distribuicao": "<uniforme|normal|lognormal|poisson|categorica|sequencial>",
          "parametros": {"min": <n>, "max": <n>, "media": <n>, "desvio": <n>, "lambda": <n>, "inicio": "<YYYY-MM-DD>", "fim": "<YYYY-MM-DD>"},
          "categorias": [{"valor": "<v>", "peso": <0-1>}],
          "decimais": <inteiro>,
          "papel": "<feature|alvo|identificador|temporal|auxiliar>",
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
        "proporcao_classe_positiva": <0-1 ou null>
      },
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
        ents = np.arange(1, n_ent + 1)
        idx = pd.MultiIndex.from_product([ents, datas], names=[col_ent, col_data])
        df = idx.to_frame(index=False)
    else:
        df = pd.DataFrame({col_data: datas})
    return df.sort_values([c for c in (col_ent, col_data) if c]).reset_index(drop=True)


def gerar_arquivo(arq, gerados):
    rng = np.random.default_rng(SEMENTE + abs(hash(arq["nome"])) % 10_000)
    st = arq.get("serie_temporal")
    if st and st.get("coluna_data"):
        df = _grid_temporal(st, arq)
        n = len(df)
        col_ent = st.get("coluna_entidade")
        # atributos da entidade são fixos no tempo: densidade da região não muda
        # a cada dia. Sortear por linha produziria uma região que muda de tamanho
        # diariamente, o que nenhuma análise por entidade sustentaria.
        if col_ent:
            ents = df[col_ent].unique()
            for col in arq["colunas"]:
                if col["nome"] in df.columns or (col.get("papel") or "") == "alvo" or col.get("derivada_de"):
                    continue
                if (col.get("papel") or "") == "auxiliar" or "densid" in (col.get("descricao") or "").lower():
                    valores = _coluna(rng, col, len(ents), df)
                    df[col["nome"]] = pd.Series(df[col_ent]).map(dict(zip(ents, valores)))
    else:
        n = int(arq.get("linhas") or 5000)
        df = pd.DataFrame()

    for col in arq["colunas"]:
        if (col.get("papel") or "") == "alvo" or col.get("derivada_de") or col["nome"] in df.columns:
            continue
        df[col["nome"]] = _coluna(rng, col, n, df)

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
            if media_alvo is not None and desvio_alvo and float(np.std(y)) > 0:
                y = (y - np.mean(y)) / np.std(y) * float(desvio_alvo) + float(media_alvo)
            elif p.get("min") is not None and p.get("max") is not None and float(np.std(y)) > 0:
                lo, hi = float(p["min"]), float(p["max"])
                y = lo + (y - y.min()) / (y.max() - y.min() + 1e-12) * (hi - lo)
            # piso físico apenas (contagem não é negativa); sem teto, para não recortar a cauda
            if p.get("min") is not None:
                y = np.maximum(y, float(p["min"]))
            # contagens são inteiras: um alvo "número de entregas" com casas decimais
            # denuncia dado sintético e confunde quem for analisar
            if especificacao.get("tipo") == "int":
                df[alvo["coluna"]] = np.rint(y).astype(int)
            else:
                df[alvo["coluna"]] = np.round(y, int(especificacao.get("decimais") or 2))

    # Defasagens e janelas móveis: só agora, com o alvo já calculado, e SEMPRE
    # a partir do histórico real — dentro de cada entidade e em ordem de tempo.
    # Sortear uma coluna "valor do período anterior" produz um dado que se
    # contradiz: quem resolver a prova faz shift() e obtém outros números.
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

    protegidas = {alvo.get("coluna"), arq.get("chave_primaria")} - {None}
    # lags não recebem nulos artificiais: os ausentes deles são os do início da série
    protegidas |= {c["nome"] for c in arq["colunas"]
                   if str((c.get("derivada_de") or {}).get("parte") or "").startswith(("lag_", "media_movel_"))}
    df = _aplicar_defeitos(rng, df, arq.get("defeitos") or {}, protegidas)

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
            linhas.append(f"  Entidades distintas em '{st['coluna_entidade']}': {df[st['coluna_entidade']].nunique()}")
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
    inicio_desc = max(0, blocos[0]["inicio"] - 2000)
    corte = txt.rfind(chr(10)*2, inicio_desc, blocos[0]["inicio"])
    inicio_desc = corte + 2 if corte > 0 else inicio_desc
    trecho = txt[inicio_desc:blocos[0]["inicio"]]
    if len(trecho.strip()) < 80:
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

    print("[Fase 4] Checagem semântica...")
    raw2 = _chat(None, MODEL_TURBO, system_prompt_semantica(),
                 user_prompt_semantica(txt_reconciliado, resumir_para_llm(spec, destino)))
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
        if txt_reconciliado != txt:
            (projeto / "prova_pratica.reconciliada.preview.txt").write_text(txt_reconciliado, encoding="utf-8")
            print("  → prévia da descrição reconciliada em prova_pratica.reconciliada.preview.txt")
        print("[4.7] --dry-run: TXT não alterado. Script e CSVs disponíveis para conferência.")
        _print_usage_summary()
        return

    print("[Fase 5] Atualizando a prova...")
    (projeto / "prova_pratica.pre_turbo.txt").write_text(txt, encoding="utf-8")
    if txt_reconciliado != txt:
        txt = txt_reconciliado
        blocos = extrair_datasets(txt)  # os offsets mudaram com o texto novo
    novo = substituir_no_txt(txt, blocos, spec, destino)
    txt_path.write_text(novo, encoding="utf-8")
    print(f"  → backup em prova_pratica.pre_turbo.txt")
    print(f"  → {len(blocos)} bloco(s) substituído(s) por marcador de link + dicionário + amostra")
    print("\n[4.7] Concluído. O coordenador deve subir os CSVs e preencher os marcadores "
          f"'{MARCADOR_LINK}' na prova.")
    _print_usage_summary()


if __name__ == "__main__":
    main()
