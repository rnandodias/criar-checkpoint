"""
=============================
Geradores do Checkpoint — Aulas 2 (Teórica)
=========================================================
openai==1.102.0 | python-dotenv
=============================
scripts/gerar_prova_teorica_do_zero.py
=============================

Como rodar:

Teórica (nível 1, apenas TXT, ordenado por dificuldade asc):
python scripts/gerar_prova_teorica_do_zero.py \
  --nivel 1 \
  --carreira "Analista de Dados" \
  --resumos_arquivo output/checkpoints/resumos_analista_de_dados_nivel_1.json \
  --max_questoes 10 --min_por_curso 1 --max_por_curso 2 --domains_window 3

Opcional (deixar as alternativas com tamanhos mais parecidos — custa mais):
  --ajustar_alternativas

Exemplo de uso:
python scripts/gerar_prova_teorica_do_zero.py --nivel 1 --carreira "governanca_de_dados" --resumos_arquivo output/checkpoints/resumos_governanca_de_dados_nivel_1.json --max_questoes 20 --min_por_curso 2 --max_por_curso 3 --domains_window 5 --ajustar_alternativas

Saída:
output/cursos_checkpoint/prova_teorica_<slug_carreira>_nivel_<n>.txt
"""

from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from openai import OpenAI
from anthropic import Anthropic

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# =========================
# Configuração de modelos
# =========================
# Geração crítica (ideias + transformação em múltipla escolha): "cavalo de carga"
MODEL_IDEAS = "claude-sonnet-4-6"
MODEL_FORMAT = "claude-sonnet-4-6"
TEMPERATURE_IDEAS = 0.0
TEMPERATURE_FORMAT = 0.0

# Ranqueamento (apenas dificuldade — não vale gastar): modelo barato
MODEL_RANK = "claude-haiku-4-5-20251001"
TEMPERATURE_RANK = 0.0

# Alternativas:
# MODEL_IDEAS = MODEL_FORMAT = "gpt-5" / "gpt-4o" / "claude-opus-4-7"
# MODEL_RANK = "gpt-4o-mini"

SINGLE_PASS_CHAR_LIMIT = 300_000

INPUT_DIR = Path(__file__).resolve().parent.parent / "output" / "checkpoints"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output" / "cursos_checkpoint"

# Schema novo (etapa 2): resumo por curso é {tema_central, conteudos_testaveis[], ferramentas_usadas[]}.
# Cada conteudo_testavel: {topico, nivel, tipo, habilidade, evidencia_de_ensino, armadilhas_comuns[]}.

# =========================
# Prompts VERBATIM (mantidos)
# =========================

def system_prompt_to_ask_for_exercise_ideas() -> str:
    return """Você é um especialista em criar exercícios educacionais baseados em transcrições de aulas em vídeo.
Sua tarefa é criar questões teóricas que testem compreensão e análise, sem exigir criação de código, focando na aplicação dos conceitos no dia a dia. Siga sempre as diretrizes abaixo.
Você receberá a transcrição de uma aula e um domínio (empresa fictícia + área de atuação). Escolha os conceitos teóricos principais da aula e crie uma analogia para o domínio.

Regras gerais:
- Estimule pensamento crítico e aplicação dos conceitos no dia a dia;
- Explique brevemente o contexto da empresa fictícia em todas as questões (uma frase basta);
- Não introduza ferramentas, conceitos ou técnicas que não estejam na transcrição;
- Teste aplicação dos conceitos, nunca memorização literal de fórmulas;
- Crie 8 questões distintas com respostas baseadas nas analogias;
- Cada enunciado termina com uma pergunta norteadora única, variando entre questões;
- Título: português corrido, curto (ex.: "Garantindo privacidade na Voll");
- Linguagem neutra: "pessoa desenvolvedora", "A empresa te contratou". Nunca "Você foi contratado" nem masculino genérico.

Limites de tamanho (CUMPRIR ESTRITAMENTE):
- Enunciado (contexto + pergunta): 120-180 palavras no total.
- Pergunta norteadora (a frase final): 1 frase única, ≤30 palavras.
- Resposta: concisa, justificando a escolha em 1-2 frases (≤60 palavras), sem listar todos os passos.
"""

def user_prompt_to_ask_for_exercise_ideas_static() -> str:
    """Parte estática do user prompt — pronta para cache (Anthropic)."""
    return """A narrativa deve incluir o estudante no centro da situação-problema, com pronomes como "A empresa contratou você" ou "A equipe que você integra". Aplicação real ao domínio e conteúdo. Nunca cite a aula (ex.: "discutido na aula").

EXEMPLOS DE ENUNCIADO (mantenha esse tamanho — entre 120 e 180 palavras):

- A Silver Screen Productions é uma produtora de filmes que está testando modelos multimodais para acelerar etapas criativas. A empresa te contratou para mapear quais tarefas o modelo consegue executar bem e quais ainda exigem supervisão humana, antes de definir o escopo do piloto. Considerando o estado atual da tecnologia multimodal, qual tarefa ultrapassa as capacidades de um modelo multimodal típico?

- A Hermex Log, empresa de logística, te contratou para apoiar a equipe de QA com containers Docker efêmeros. Ao rodar `docker run ubuntu`, o container inicia e logo fica em estado "exited", impedindo análise. A equipe precisa entender o motivo desse comportamento. Por que o container saiu logo após a inicialização?

Formato de saída:
Exercício 1 - Título curto em português corrido

Texto da questão:

Conceito abordado:

Resposta:
"""


def user_prompt_to_ask_for_exercise_ideas_dynamic(domains: str, transcription: str) -> str:
    """Parte dinâmica — domínios + transcrição. NÃO entra em cache."""
    return f"""A transcrição da aula está a seguir entre duas hashtags(##):
##
{transcription}
##

Domínios para usar nas questões: {domains}
"""


def user_prompt_to_ask_for_exercise_ideas(domains: str, transcription: str) -> str:
    """Wrapper de compatibilidade — concatena static + dynamic para uso síncrono OpenAI."""
    return user_prompt_to_ask_for_exercise_ideas_static() + "\n\n" + user_prompt_to_ask_for_exercise_ideas_dynamic(domains, transcription)

def system_prompt_to_transform_into_multiple_choice() -> str:
    return """Você é um especialista em criar questões de múltipla escolha a partir de perguntas teóricas baseadas em transcrições de aulas. Transforme a questão teórica em uma questão de múltipla escolha com 4 alternativas (apenas 1 correta), usando o cenário da empresa fictícia.

**Instruções:**

1. **Contexto:** use o cenário da questão original para contextualizar as alternativas.
2. **Adapte a resposta correta:** ajuste-a para o formato de alternativa, com clareza e objetividade — sem listar todos os passos.
3. **3 alternativas incorretas:** cada uma é um cenário plausível com aplicação errada do conceito ou má prática. Nunca apenas omitir um componente (ex.: "sem TLS"); use cenários completos com erros comuns. Evite padrões repetitivos.
4. **Justificativas:** uma frase neutra explicando por que é correta/incorreta, sem revelar a resposta correta nas incorretas.
5. **Consistência:** alternativas com tamanhos parecidos, cobrindo aspectos distintos do tema.

Limites de tamanho (CUMPRIR ESTRITAMENTE):
- Pergunta norteadora (a última frase do enunciado): 1 frase, ≤30 palavras.
- Cada alternativa: 1-2 frases, ≤45 palavras.
- Cada justificativa: 1 frase, ≤30 palavras.

**Objetivo:** questão desafiadora, concisa, que avalie compreensão.

**Exemplo curto (siga este tamanho):**

A Silver Screen Productions, produtora de filmes em busca de inovação, está testando modelos multimodais. A empresa te contratou para mapear o que esses modelos conseguem fazer hoje. Qual tarefa ultrapassa as capacidades atuais de um modelo multimodal típico?
A) Gerar uma sinopse a partir do roteiro fornecido, apoiando o marketing.
Justificativa: Incorreta, pois modelos multimodais geram resumos textuais com base em roteiros.
B) Produzir efeitos visuais completos integrando personagens digitais e cenários reais sem supervisão humana.
Justificativa: Correta, pois efeitos visuais cinematográficos autônomos ainda exigem supervisão humana.
C) Sugerir trilha sonora a partir da análise de sentimentos do roteiro.
Justificativa: Incorreta, pois modelos multimodais combinam análise textual e sugestões de áudio.
D) Propor conceitos de figurino com base em descrições dos personagens.
Justificativa: Incorreta, pois a geração de imagens a partir de texto está dentro das capacidades multimodais.
"""

def user_prompt_to_transform_into_multiple_choice_static() -> str:
    """Parte estática — instruções, formato, regras (cacheável)."""
    return """Transforme a questão abaixo em uma questão de múltipla escolha com uma pergunta final (norteadora) única, mas *NUNCA* alterando o seu contexto. A questão final não pode ter duas ou mais perguntas. Sempre utilize linguagem neutra para se referir aos cargos citados, por exemplo nunca use "um programador", "o usuário", sempre "pessoa desenvolvedora", "pessoa responsável pela gerência", "pessoa usuária", etc.

A sua saída deve ser no seguinte formato e a explicação do domínio (que será fornecido na parte dinâmica) deve estar presente na questão após o nome da empresa fictícia. Nunca faça alteração no título da questão.

Título:  Texto do título da questão aqui
Pergunta: Texto da pergunta aqui
A)
Justificativa: Correta, pois...
B)
Justificativa: Incorreta, pois...
C)
Justificativa: Incorreta, pois...
D)
Justificativa: Incorreta, pois...
"""


def user_prompt_to_transform_into_multiple_choice_dynamic(exercise, domains: str) -> str:
    """Parte dinâmica — exercício específico + domínios. NÃO entra em cache."""
    return f"""titulo: {exercise['titulo']}

pergunta: {exercise['pergunta']}

resposta: {exercise['resposta']}

domínios: {domains}
"""


def user_prompt_to_transform_into_multiple_choice(exercise, domains: str) -> str:
    """Wrapper de compatibilidade — concatena static + dynamic."""
    return user_prompt_to_transform_into_multiple_choice_static() + "\n" + user_prompt_to_transform_into_multiple_choice_dynamic(exercise, domains)

def system_prompt_to_adjust_alternative_sizes() -> str:
    return """You are an expert in making previously created exercises more challenging. Your task is to ensure that the length of the incorrect alternatives matches that of the correct alternative. This makes choosing the correct option more difficult, as it doesn't make it obvious which one is correct. We have many issues with the correct alternative being the longest, which makes it clear that it is the right one. Therefore, I need you to insert more information into the incorrect alternatives so that they become the same length as the correct alternative.

You must follow ALL the rules below to increase the length of the incorrect alternatives:

Focus on Content: The incorrect alternatives should be based on aspects of the central content of the question. Aim to create options that explore different facets or perspectives of this content, maintaining proximity to the correct alternative.

Structure and Clarity: Use a structure similar to that of the correct alternative, with clear and precise language, ensuring that all options are presented consistently.

Relevance and Coherence: The alternatives must be relevant to the topic of the question and coherent with the knowledge related to the subject. Avoid deviating from the central theme or introducing disconnected information.

Convincing Additions: Any content added to increase the length of the incorrect alternatives should be designed to convince the user that the alternative could be correct. Use arguments that utilize the concepts within the alternative, providing plausible reasoning or supporting evidence that makes the option seem valid—without explicitly stating "this is the correct alternative."

Maintain Plausibility: When expanding the incorrect alternatives, add neutral or plausible information that maintains the original meaning without emphasizing their incorrectness. Avoid adding content that makes the alternative more obviously wrong.

Consistency: Ensure that the tone, style, and complexity of all alternatives are consistent, so no option stands out due to its wording or length.

Avoid Depreciation: Do not include hints or language that depreciates the incorrect alternatives or makes it easier to identify them as incorrect. The goal is to keep all options equally convincing.

When expanding the length of the alternatives, especially the incorrect ones, add neutral or plausible information that maintains the original meaning without emphasizing their incorrectness. Avoid adding content that makes the alternative more obviously wrong. The goal is to increase the length to match the correct option, keeping all alternatives equally convincing.

Connector Usage: When generating content, vary the use of connectors to ensure diversity in sentence structure. Avoid frequent repetition of any single connector, such as "Além disso." Here is a list of Portuguese connectors that can be used instead: [E; nem; também; não só…mas também; não apenas; não somente; além disso; ademais; como; bem como; ainda; do mesmo modo; depois; finalmente; em seguida; adicionalmente]. Experiment with different combinations to enhance the flow and coherence of the text.

Here it is a GOOD and EXPECTED kind of expanding:

OLD ONE:

Título: Sincronizando Pedidos de Café 
Pergunta: Você é o gerente do Serenatto - Café & Bistrô, um estabelecimento que oferece uma variedade de refeições e bebidas (Culinária), e está buscando otimizar o tempo de preparo dos pedidos de café. Considerando o conceito de concorrência discutido na aula, qual seria a melhor abordagem para melhorar a eficiência do preparo dos pedidos e reduzir o tempo de espera dos clientes?
A) Implementar um sistema onde cada tipo de café é preparado simultaneamente, criando "goroutines" para cada pedido, permitindo que o expresso, o cappuccino e o latte sejam preparados ao mesmo tempo. Justificativa: Correta, pois ao utilizar "goroutines" para preparar diferentes tipos de café simultaneamente, o tempo de espera dos clientes é reduzido, já que os pedidos são processados em paralelo, otimizando o uso dos recursos disponíveis. 
B) Continuar processando os pedidos de café sequencialmente, mas aumentar o número de baristas para acelerar o preparo de cada pedido individualmente. 
Justificativa: Incorreta, pois embora aumentar o número de baristas possa acelerar o preparo individual, não utiliza o conceito de concorrência para processar pedidos simultaneamente, o que poderia reduzir ainda mais o tempo de espera. 
C) Implementar um sistema de fila prioritária onde os pedidos de café mais complexos, como o latte, são preparados primeiro, seguidos pelos mais simples, como o expresso. 
Justificativa: Incorreta, pois priorizar pedidos mais complexos não aproveita a concorrência para preparar diferentes tipos de café ao mesmo tempo, o que poderia otimizar o tempo de preparo. 
D) Utilizar um cronograma fixo para preparar cada tipo de café em horários diferentes do dia, garantindo que cada tipo de café tenha um tempo dedicado exclusivo. 
Justificativa: Utilizar um cronograma fixo para preparar cada tipo de café em horários diferentes do dia, garantindo que cada tipo de café tenha um tempo dedicado exclusivo.

CHANGED ONE:

Título: Sincronizando Pedidos de Café 
Pergunta: Você é o gerente do Serenatto - Café & Bistrô, um estabelecimento que oferece uma variedade de refeições e bebidas (Culinária), e está buscando otimizar o tempo de preparo dos pedidos de café. Considerando o conceito de concorrência discutido na aula, qual seria a melhor abordagem para melhorar a eficiência do preparo dos pedidos e reduzir o tempo de espera dos clientes?
A) Implementar um sistema onde cada tipo de café é preparado simultaneamente, criando "goroutines" para cada pedido, permitindo que o expresso, o cappuccino e o latte sejam preparados ao mesmo tempo. Justificativa: Correta, pois ao utilizar "goroutines" para preparar diferentes tipos de café simultaneamente, o tempo de espera dos clientes é reduzido, já que os pedidos são processados em paralelo, otimizando o uso dos recursos disponíveis. 
B) Implementar um sistema de navegação avançado que personalize a experiência do usuário, direcionando automaticamente os clientes para as seções com base em seu histórico de navegação e preferências anteriores. Inclua menus dinâmicos que se ajustem em tempo real, destacando as seções mais relevantes, como café da manhã, almoço ou sobremesas, e utilize algoritmos preditivos para antecipar as necessidades do usuário e agilizar sua navegação.
Justificativa: Incorreta, pois, embora a personalização possa melhorar a experiência do usuário, direcionar automaticamente os clientes sem sua escolha explícita pode causar confusão e reduzir seu controle sobre a navegação, potencialmente levando à frustração se o sistema interpretar erroneamente suas preferências.
C) Redesenhar o site utilizando uma abordagem minimalista, removendo os menus e links tradicionais e apresentando todo o conteúdo em uma única página de rolagem contínua. Organize as seções de café da manhã, almoço e sobremesas sequencialmente, utilizando imagens de alta qualidade e descrições breves para envolver os usuários enquanto exploram o site, incentivando a descoberta de toda a variedade de opções disponíveis.
Justificativa: Incorreta, pois, embora um design de página única com rolagem contínua possa ser visualmente atraente, isso pode dificultar que os clientes encontrem rapidamente seções específicas, levando a uma experiência de navegação menos eficiente.
D) Incorporar uma introdução interativa adicionando um pop-up de boas-vindas que ofereça um tour virtual pelo site, destacando as principais funcionalidades e seções, como os menus de café da manhã, almoço e sobremesas. Inclua animações e instruções que guiem os clientes através do processo de navegação, apresentando promoções e ofertas especiais para aumentar o engajamento e incentivar a exploração.
Justificativa: Incorreta, pois, embora tours interativos possam ser informativos, pop-ups e orientações forçadas podem interromper o fluxo natural de navegação do usuário e serem considerados intrusivos, potencialmente prejudicando a experiência de navegação intuitiva.
"""

def user_prompt_to_adjust_alternative_sizes(exercise: str) -> str:
    return f"""Below is the exercise with the alternatives; perform your task and return the exercise in the same formatting as it is.

{exercise}"""

# =========================
# Ranqueamento de dificuldade (barato)
# =========================

def system_prompt_rank_difficulty_plain() -> str:
    return """Você é uma pessoa especialista em avaliação educacional.
Receberá:
- RESUMOS compactos dos cursos do nível (fonte da verdade).
- Uma lista enumerada de questões de múltipla escolha em TEXTO PURO.

Tarefa:
1) Para CADA questão, atribua apenas:
   - dificuldade (1–5): 1=básica, 2=introdutória, 3=intermediária, 4=desafiadora, 5=avançada.
2) Não reescreva as questões e não gere comentários.
3) Saída: JSON estrito, lista de objetos:
[
  {"idx": <índice_da_questao_listada_a_partir_de_0>, "dificuldade": 1-5}
]
"""

def user_prompt_rank_difficulty_plain(questions_text_block: str, resumos_compactos_json: str, carreira: str) -> str:
    return (
        "Avalie a **dificuldade** das questões (1–5) com base nos RESUMOS do nível e no objetivo de checkpoint da carreira.\n\n"
        f"Carreira: {carreira or 'geral'}\n\n"
        "RESUMOS (JSON compacto):\n```json\n" + resumos_compactos_json + "\n```\n\n"
        "QUESTÕES (enumeradas a partir de 0):\n"
        + questions_text_block
        + "\n\nRetorne APENAS o JSON pedido."
    )

# =========================
# OpenAI helpers
# =========================

@dataclass
class ExerciseItem:
    titulo: str
    pergunta: str
    resposta: str

def _safe_json_loads(s: str) -> Optional[Any]:
    if not s:
        return None
    s = s.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:].strip()
    try:
        return json.loads(s)
    except Exception:
        try:
            start, end = s.index("{"), s.rindex("}") + 1
            return json.loads(s[start:end])
        except Exception:
            return None

def _model_supports_temperature(model: str) -> bool:
    """Modelos que NÃO aceitam temperature customizada:
    OpenAI: gpt-5*, reasoning (o1, o3, o4).
    Anthropic: claude-opus-4-7+ (Sonnet 4.6 e Haiku 4.5 ainda aceitam)."""
    m = (model or "").lower()
    return not (
        m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4")
        or m.startswith("claude-opus-4-7") or m.startswith("claude-opus-5")
    )


def _provider_for(model: str) -> str:
    m = (model or "").lower()
    if m.startswith("claude") or m.startswith("anthropic"):
        return "anthropic"
    return "openai"


# Clients lazy (instanciados quando usados)
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


# Acumulador global de uso (Anthropic) para reportar economia de cache no fim do main
USAGE_TOTALS: Dict[str, int] = {
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "input_tokens": 0,
    "output_tokens": 0,
}


def _accumulate_usage(usage: Dict[str, int]) -> None:
    for k in USAGE_TOTALS:
        USAGE_TOTALS[k] += int(usage.get(k, 0) or 0)


def _build_anthropic_request_params(
    *, model: str, system_static: str, user_static: str, user_dynamic: str,
    temperature: float, max_tokens: int,
) -> Dict[str, Any]:
    system_blocks: List[Dict[str, Any]] = []
    if system_static:
        system_blocks.append({"type": "text", "text": system_static, "cache_control": {"type": "ephemeral"}})
    user_content: List[Dict[str, Any]] = []
    if user_static:
        user_content.append({"type": "text", "text": user_static, "cache_control": {"type": "ephemeral"}})
    if user_dynamic:
        user_content.append({"type": "text", "text": user_dynamic})
    params: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user_content}],
    }
    if _model_supports_temperature(model):
        params["temperature"] = temperature
    if system_blocks:
        params["system"] = system_blocks
    return params


def _anthropic_messages_with_cache(
    *, model: str, system_static: str, user_static: str, user_dynamic: str,
    temperature: float = 0.0, max_tokens: int = 8192,
    retries: int = 3, backoff: float = 2.0,
) -> str:
    client = _get_anthropic_client()
    params = _build_anthropic_request_params(
        model=model, system_static=system_static, user_static=user_static,
        user_dynamic=user_dynamic, temperature=temperature, max_tokens=max_tokens,
    )
    for attempt in range(retries):
        try:
            resp = client.messages.create(**params)
            text = "".join(b.text for b in resp.content if hasattr(b, "text"))
            _accumulate_usage({
                "cache_creation_input_tokens": getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
                "cache_read_input_tokens": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            })
            return text
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = backoff * (2 ** attempt)
            print(f"[RETRY {attempt + 1}/{retries}] {type(e).__name__}: {e} — {wait:.1f}s")
            time.sleep(wait)
    return ""


def _anthropic_messages_batch(
    *, model: str,
    items: List[Tuple[str, str, str, str]],
    temperature: float = 0.0, max_tokens: int = 8192,
    poll_interval: float = 15.0,
) -> Dict[str, str]:
    """Submete batch e bloqueia até concluir. items=[(custom_id, system_static, user_static, user_dynamic)]."""
    client = _get_anthropic_client()
    requests = []
    for custom_id, sys_s, user_s, user_d in items:
        params = _build_anthropic_request_params(
            model=model, system_static=sys_s, user_static=user_s, user_dynamic=user_d,
            temperature=temperature, max_tokens=max_tokens,
        )
        requests.append({"custom_id": custom_id, "params": params})

    print(f"[Batch] Submetendo {len(requests)} requests para {model}...")
    batch = client.messages.batches.create(requests=requests)
    print(f"[Batch] ID: {batch.id} | aguardando (poll a cada {poll_interval:.0f}s)...")

    while batch.processing_status != "ended":
        time.sleep(poll_interval)
        batch = client.messages.batches.retrieve(batch.id)
        rc = batch.request_counts
        print(f"[Batch {batch.id[:16]}] proc={rc.processing} ok={rc.succeeded} err={rc.errored} cancel={rc.canceled} exp={rc.expired}")

    print("[Batch] Concluído. Lendo resultados...")
    results: Dict[str, str] = {}
    falhos: List[str] = []
    for entry in client.messages.batches.results(batch.id):
        custom_id = entry.custom_id
        if entry.result.type == "succeeded":
            msg = entry.result.message
            text = "".join(b.text for b in msg.content if hasattr(b, "text"))
            _accumulate_usage({
                "cache_creation_input_tokens": getattr(msg.usage, "cache_creation_input_tokens", 0) or 0,
                "cache_read_input_tokens": getattr(msg.usage, "cache_read_input_tokens", 0) or 0,
                "input_tokens": msg.usage.input_tokens,
                "output_tokens": msg.usage.output_tokens,
            })
            results[custom_id] = text
        else:
            falhos.append(custom_id)

    if falhos:
        print(f"[Batch] {len(falhos)} requests falharam. Retry síncrono...")
        item_by_id = {it[0]: it for it in items}
        for cid in falhos:
            try:
                _, sys_s, user_s, user_d = item_by_id[cid]
                results[cid] = _anthropic_messages_with_cache(
                    model=model, system_static=sys_s, user_static=user_s, user_dynamic=user_d,
                    temperature=temperature, max_tokens=max_tokens,
                )
            except Exception as e:
                print(f"[Batch] retry de {cid} falhou: {e}")
                results[cid] = ""

    return results


def _chat(client: Any, model: str, system: str, user_static: str, user_dynamic: str = "",
          temperature: float = 0.0) -> str:
    """Sync. Para Anthropic separa user_static (cacheable) de user_dynamic (não cache)."""
    if _provider_for(model) == "anthropic":
        return _anthropic_messages_with_cache(
            model=model, system_static=system,
            user_static=user_static, user_dynamic=user_dynamic,
            temperature=temperature, max_tokens=8192,
        )
    # OpenAI: concatena user_static + user_dynamic (cache automático na maior parte dos modelos)
    user = (user_static + ("\n\n" + user_dynamic if user_dynamic else "")).strip()
    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if _model_supports_temperature(model):
        kwargs["temperature"] = temperature
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
    print(f"  Cache create (escrita):     {cache_create:>10,} tokens (custa 1.25x input)")
    print(f"  Cache read (hit):           {cache_read:>10,} tokens (custa 0.10x input — economia ~90%)")
    print(f"  Output:                     {out:>10,} tokens")
    print("=" * 60)

def _slugify(s: str) -> str:
    s = (s or "").strip().lower()
    import re as _re
    s = _re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "geral"

# =========================
# Conversão de RESUMO -> "transcrição" sintética
# =========================

def _render_conteudo_testavel(c: Dict[str, Any]) -> List[str]:
    """Renderiza 1 conteudo_testavel como bloco de linhas para o prompt."""
    topico = str(c.get("topico", "") or "").strip()
    if not topico:
        return []
    nivel = str(c.get("nivel", "") or "").strip()
    tipo = str(c.get("tipo", "") or "").strip()
    hab = str(c.get("habilidade", "") or "").strip()
    ev = str(c.get("evidencia_de_ensino", "") or "").strip()
    armadilhas = c.get("armadilhas_comuns", []) or []

    cabecalho_tags = "/".join(t for t in (nivel, tipo) if t)
    head = f"- {topico}" + (f" [{cabecalho_tags}]" if cabecalho_tags else "")
    out = [head]
    if hab:
        out.append(f"  Habilidade: {hab}")
    if ev:
        out.append(f"  Como foi ensinado: {ev}")
    if armadilhas:
        out.append("  Armadilhas comuns: " + " | ".join(str(a) for a in armadilhas))
    return out


def resumo_to_transcription_text(course: Dict[str, Any]) -> str:
    nome = str(course.get("nome") or f"Curso {course.get('id')}")
    link = str(course.get("link") or "")
    resumo = (course.get("resumo") or {})
    lines: List[str] = [f"Curso: {nome}"]
    if link:
        lines[0] += f" | Fonte: {link}"

    tema = str(resumo.get("tema_central", "") or "").strip()
    if tema:
        lines.append(f"Tema central: {tema}")

    conteudos = resumo.get("conteudos_testaveis", []) or []
    centrais = [c for c in conteudos if c.get("nivel") == "central"]
    complementares = [c for c in conteudos if c.get("nivel") != "central"]

    if centrais:
        lines.append("Conteúdos centrais (prioridade na prova):")
        for c in centrais:
            lines.extend(_render_conteudo_testavel(c))
    if complementares:
        lines.append("Conteúdos complementares:")
        for c in complementares:
            lines.extend(_render_conteudo_testavel(c))

    ferramentas = resumo.get("ferramentas_usadas", []) or []
    if ferramentas:
        lines.append(f"Ferramentas usadas no curso: {', '.join(ferramentas)}")

    text = "\n".join(lines)
    if len(text) > SINGLE_PASS_CHAR_LIMIT:
        text = text[:SINGLE_PASS_CHAR_LIMIT]
    return text

# =========================
# Pós-processamento de alternativas
# =========================

def _extract_alternatives_blocks(mc_text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    pattern = r"([A-D]\)\s[\s\S]*?)(?=\n[A-D]\)|\nJustificativa:|$)"
    matches = re.findall(pattern, mc_text)
    labels = ["A)", "B)", "C)", "D)"]
    for lbl in labels:
        for m in matches:
            if m.strip().startswith(lbl):
                out[lbl[0]] = m.strip()
                break
    return out

def _find_correct_label(mc_text: str) -> Optional[str]:
    regex = r"\n([A-D])\)[\s\S]*?\nJustificativa:\s*Corre(t|c)a"
    m = re.search(regex, mc_text, re.IGNORECASE)
    if m:
        return m.group(1)
    m2 = re.search(r"Resposta\s*correta\s*:\s*([A-D])", mc_text, re.IGNORECASE)
    return m2.group(1) if m2 else None

def is_any_alternative_longer_than_20_percent_of_the_correct_alternative(mc_text: str) -> bool:
    alts = _extract_alternatives_blocks(mc_text)
    if len(alts) != 4:
        return False
    correct = _find_correct_label(mc_text)
    if not correct:
        return False
    base_len = len(alts[correct]) or 1
    for k, v in alts.items():
        if k == correct:
            continue
        diff = abs(len(v) - base_len) / base_len * 100
        if diff >= 20:
            return True
    return False

def add_line_break_before_question(text: str) -> str:
    parts = text.split('Pergunta: ', 1)
    if len(parts) != 2:
        return text
    content = parts[1]
    sentences = content.split('. ')
    if len(sentences) <= 1:
        return text
    question_index = -1
    for i in range(len(sentences)-1, -1, -1):
        if '?' in sentences[i]:
            question_index = i
            break
    if question_index <= 0:
        return text
    result = parts[0] + 'Pergunta: '
    result += '. '.join(sentences[:question_index]) + '.\n\n'
    result += sentences[question_index]
    remaining_text = text.split(sentences[question_index], 1)[1]
    result += remaining_text
    return result

# =========================
# Domínios padrão + janela rotativa
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
    "Luz & Cena - Cinema que oferece exibição de filmes em cartaz, com horários atualizados, sinopses detalhadas e acesso a trailers diretamente na plataforma",
    "UseDev - E-commerce especializado na venda de produtos geeks, oferecendo itens como roupas temáticas, action figures e acessórios tecnológicos",
    "Petpark - Plataforma de e-commerce de produtos e serviços personalizados para animais de estimação, com recursos de agendamento online para banhos, tosas e consultas veterinárias",
    "CodeConnect - Rede social para programadores, que permite curtidas, compartilhamento e comentários em projetos e códigos.",
    "Zoop - Plataforma de e-commerce que oferece soluções tecnológicas completas para vendedores online, incluindo gestão de estoque, pagamentos integrados e marketing digital",
    "Runner Circle - Plataforma social dedicada a corredores, onde os usuários podem compartilhar treinos, metas e desafios.",
    "HomeHub - Plataforma de monitoramento e controle de dispositivos para casas inteligentes, oferecendo dashboards personalizáveis que integram iluminação, segurança e climatização em uma única interface",
    "Listin - Aplicativo para gerenciamento inteligente de listas de supermercado, com funcionalidades de compartilhamento e controle em tempo real",
    "SwiftBank - Aplicativo de banco digital que oferece serviços financeiros completos, como abertura de conta, pagamentos e transferências",
    "Indexa - Plataforma que organiza e gerencia contatos pessoais e profissionais de forma inteligente, utilizando recursos de busca avançada e sincronização automática",
    "Cinetopia - Aplicativo que permite aos usuários buscar, favoritar e organizar filmes favoritos, oferecendo sinopses detalhadas, avaliações e recomendações personalizadas",
    "Clickbonus - Plataforma digital que oferece um clube de vantagens e recompensas personalizadas por meio de parcerias com diversas empresas",
    "Calmaria Spas - Plataforma que conecta usuários a experiências de bem-estar e serviços de spas, oferecendo agendamentos online e personalização de tratamentos de relaxamento",
    "Jornada Viagens - Plataforma tecnológica especializada na comparação e reserva de pacotes de viagens, hotéis e passagens aéreas, oferecendo recursos como monitoramento de preços em tempo real e opções personalizadas para o usuário",
    "VideoFlowNow - Plataforma de streaming especializada em vídeos curtos e transmissões ao vivo, oferecendo soluções avançadas para criadores e empresas aumentarem seu engajamento por meio de inteligência artificial e personalização de conteúdo",
    "WaveCast - Plataforma de streaming e distribuição de podcasts, especializada em facilitar a publicação e monetização de conteúdos em áudio para criadores e empresas",
    "Freelando - Plataforma digital que conecta freelancers a contratantes, oferecendo um ambiente seguro para a publicação e contratação de projetos de diversas áreas",
    "TRATOTECH - Plataforma de classificados focada em produtos tecnológicos, conectando compradores e vendedores de eletrônico",
    "Dev.Spot - Plataforma para desenvolvedores criarem portfólios digitais e link trees personalizados, facilitando a apresentação de projetos, habilidades e links relevantes em um só lugar",
]

def _domains_window(domains: List[str], start_idx: int, size: int) -> List[str]:
    if not domains:
        return []
    n = len(domains)
    return [domains[(start_idx + k) % n] for k in range(size)]

# =========================
# Utilidades: resumos, ranking e parsing local + PROGRESSO
# =========================

def _load_resumos_via_cli(path: str) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Arquivo de resumos não encontrado: {p}")
    return json.loads(p.read_text(encoding="utf-8"))

def _compact_for_ranking(courses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Para o ranqueamento de dificuldade só interessa o tema + tópicos centrais."""
    compact = []
    for c in courses:
        resumo = c.get("resumo", {}) or {}
        topicos_centrais: List[str] = []
        topicos_complementares: List[str] = []
        for ct in resumo.get("conteudos_testaveis", []) or []:
            t = str(ct.get("topico", "") or "").strip()
            if not t:
                continue
            if ct.get("nivel") == "central":
                topicos_centrais.append(t)
            else:
                topicos_complementares.append(t)
        compact.append({
            "id": c.get("id"),
            "nome": c.get("nome"),
            "tema_central": resumo.get("tema_central", ""),
            "topicos_centrais": topicos_centrais,
            "topicos_complementares": topicos_complementares,
        })
    return compact

def _renumber_text_headers(text_list: List[str], new_order_indices: List[int]) -> List[str]:
    out = []
    import re as _re
    for i, old_idx in enumerate(new_order_indices, start=1):
        t = text_list[old_idx]
        t = _re.sub(r"^EXERCÍCIO\s+\d+", f"EXERCÍCIO {i}", t, count=1)
        out.append(t)
    return out

def _parse_exercise_ideas_verbatim(raw_text: str) -> List[ExerciseItem]:
    """
    Parser local do formato:
    Exercício N - <titulo>

    Texto da questão:
    <pergunta...>

    Conceito abordado:
    <...>

    Resposta:
    <resposta...>

    Tolera markdown bold (**...**) que alguns modelos (ex.: Sonnet 4.6) usam.
    """
    txt = raw_text.replace("\r\n", "\n")
    # Remove markdown bold (**xxx**) e itálico (*xxx*) que cercam marcadores
    txt = re.sub(r"\*{1,3}([^*\n]+?)\*{1,3}", r"\1", txt)
    blocks = re.split(r"\n?Exerc[íi]cio\s*\d+\s*[-–—:]\s*", txt, flags=re.IGNORECASE)
    items: List[ExerciseItem] = []
    for blk in blocks:
        blk = blk.strip()
        if not blk:
            continue
        title_end = blk.find("\n")
        if title_end == -1:
            continue
        titulo = blk[:title_end].strip()
        rest = blk[title_end+1:].strip()

        m = re.search(
            r"Texto da questão:\s*(?P<pergunta>.*?)\n+Conceito abordado:\s*(?P<conceito>.*?)\n+Resposta:\s*(?P<resposta>.*)$",
            rest,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not m:
            m2 = re.search(
                r"Texto da questão:\s*(?P<pergunta>.*?)\n+Resposta:\s*(?P<resposta>.*)$",
                rest,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if not m2:
                continue
            pergunta = m2.group("pergunta").strip()
            resposta = m2.group("resposta").strip()
        else:
            pergunta = m.group("pergunta").strip()
            resposta = m.group("resposta").strip()

        if titulo and pergunta and resposta:
            items.append(ExerciseItem(titulo=titulo, pergunta=pergunta, resposta=resposta))
    return items

# ---- Helpers visuais (barra de progresso simples) ----
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
    print()  # quebra de linha

# =========================
# Núcleo: geração de questões — TXT + ranking por dificuldade (mini)
# =========================

def _ask_exercise_ideas(client: Any, transcription_text: str, domains_csv: str) -> str:
    return _chat(
        client,
        MODEL_IDEAS,
        system_prompt_to_ask_for_exercise_ideas(),
        user_prompt_to_ask_for_exercise_ideas_static(),
        user_prompt_to_ask_for_exercise_ideas_dynamic(domains_csv, transcription_text),
        temperature=TEMPERATURE_IDEAS,
    )

def _to_multiple_choice(client: Any, ex: "ExerciseItem", domains_csv: str) -> str:
    user_dynamic = user_prompt_to_transform_into_multiple_choice_dynamic({
        "titulo": ex.titulo,
        "pergunta": ex.pergunta,
        "resposta": ex.resposta,
    }, domains_csv)
    return _chat(
        client,
        MODEL_FORMAT,
        system_prompt_to_transform_into_multiple_choice(),
        user_prompt_to_transform_into_multiple_choice_static(),
        user_dynamic,
        temperature=0.0,
    )

def _maybe_adjust_alternative_sizes(client: Any, mc_text: str, ajustar: bool) -> str:
    if not ajustar:
        return mc_text
    if not is_any_alternative_longer_than_20_percent_of_the_correct_alternative(mc_text):
        return mc_text
    # mc_text é totalmente dinâmico (não cacheia)
    return _chat(
        client,
        MODEL_IDEAS,
        system_prompt_to_adjust_alternative_sizes(),
        "",  # user_static vazio (esta etapa não tem parte estática significativa)
        user_prompt_to_adjust_alternative_sizes(mc_text),
        temperature=0.0,
    )

def gerar_prova_teorica(
    nivel: int,
    carreira: str,
    resumos_arquivo: str,
    domains: List[str],
    max_questoes: int = 10,
    min_por_curso: int = 1,
    max_por_curso: int = 2,
    permitir_exceder_max: bool = False,
    domains_window: int = 3,
    ajustar_alternativas: bool = False,
    batch_mode: bool = True,
) -> str:
    load_dotenv()
    # Clients OpenAI/Anthropic são lazy — instanciados em _chat() conforme o MODEL.
    # Mantemos `client = None` apenas por compatibilidade com a assinatura atual de _chat().
    client = None

    # Carrega resumos via CLI (simples)
    courses = _load_resumos_via_cli(resumos_arquivo)
    if not courses:
        raise RuntimeError("Nenhum curso encontrado no JSON de resumos do nível.")

    total_min_required = min_por_curso * len(courses)
    target_total = max_questoes if max_questoes and max_questoes > 0 else total_min_required
    if permitir_exceder_max and total_min_required > target_total:
        target_total = total_min_required

    ideias_por_curso: List[Tuple[str, List[ExerciseItem], str]] = []

    # ---- Fase 1: Gera ideias por curso (1 chamada por curso) ----
    print("Fase 1/4: Gerando ideias por curso...")
    n_courses = len(courses)

    # Prepara lista de (nome, transcription, domains_csv) para cada curso
    cursos_prep: List[Tuple[str, str, str]] = []
    for idx, course in enumerate(courses, start=1):
        nome = course.get("nome") or f"Curso {course.get('id')}"
        transcription = resumo_to_transcription_text(course)
        doms = _domains_window(domains, start_idx=(idx-1) * domains_window, size=domains_window)
        if not doms:
            doms = domains
        domains_csv = ", ".join(doms)
        cursos_prep.append((str(nome), transcription, domains_csv))

    use_batch_f1 = batch_mode and _provider_for(MODEL_IDEAS) == "anthropic" and n_courses >= 2
    if use_batch_f1:
        print(f"  → Fase 1 via Anthropic Batch ({n_courses} requests)")
        items: List[Tuple[str, str, str, str]] = []
        for i, (nome, transcription, domains_csv) in enumerate(cursos_prep):
            items.append((
                f"f1_{i}",
                system_prompt_to_ask_for_exercise_ideas(),
                user_prompt_to_ask_for_exercise_ideas_static(),
                user_prompt_to_ask_for_exercise_ideas_dynamic(domains_csv, transcription),
            ))
        try:
            responses = _anthropic_messages_batch(model=MODEL_IDEAS, items=items, temperature=TEMPERATURE_IDEAS)
            for i, (nome, _, domains_csv) in enumerate(cursos_prep):
                raw_ideas = responses.get(f"f1_{i}", "")
                exercises = _parse_exercise_ideas_verbatim(raw_ideas)
                ideias_por_curso.append((nome, exercises, domains_csv))
        except Exception as e:
            print(f"[Fase 1] Batch falhou ({type(e).__name__}: {e}). Fallback sync.")
            use_batch_f1 = False

    if not use_batch_f1:
        # Sync: 1 chamada por curso
        for idx, (nome, transcription, domains_csv) in enumerate(cursos_prep, start=1):
            raw_ideas = _ask_exercise_ideas(client, transcription, domains_csv)
            exercises = _parse_exercise_ideas_verbatim(raw_ideas)
            ideias_por_curso.append((nome, exercises, domains_csv))
            _progress("  → Ideias geradas", idx, n_courses)
        _progress_done()

    todas_texto: List[str] = []
    por_curso_count: Dict[int, int] = {}

    # ---- Fase 2: mínimo por curso ----
    print("Fase 2/4: Montando questões (mínimo por curso)...")

    # Coleta todos os pares (curso_idx, ex) que viram chamada nesta fase
    f2_calls: List[Tuple[int, "ExerciseItem", str]] = []  # (curso_idx, exercicio, domains_csv)
    for c_idx, (_, exercises, domains_csv) in enumerate(ideias_por_curso):
        for j, ex in enumerate(exercises[:min_por_curso]):
            f2_calls.append((c_idx, ex, domains_csv))

    use_batch_f2 = batch_mode and _provider_for(MODEL_FORMAT) == "anthropic" and len(f2_calls) >= 2
    f2_results: Dict[int, str] = {}  # call_idx -> mc_text
    if use_batch_f2:
        print(f"  → Fase 2 via Anthropic Batch ({len(f2_calls)} requests)")
        items_f2: List[Tuple[str, str, str, str]] = []
        for k, (_c_idx, ex, domains_csv) in enumerate(f2_calls):
            items_f2.append((
                f"f2_{k}",
                system_prompt_to_transform_into_multiple_choice(),
                user_prompt_to_transform_into_multiple_choice_static(),
                user_prompt_to_transform_into_multiple_choice_dynamic(
                    {"titulo": ex.titulo, "pergunta": ex.pergunta, "resposta": ex.resposta},
                    domains_csv,
                ),
            ))
        try:
            resp = _anthropic_messages_batch(model=MODEL_FORMAT, items=items_f2, temperature=TEMPERATURE_FORMAT)
            for k in range(len(f2_calls)):
                f2_results[k] = resp.get(f"f2_{k}", "")
        except Exception as e:
            print(f"[Fase 2] Batch falhou ({type(e).__name__}: {e}). Fallback sync.")
            use_batch_f2 = False

    # Aplica resultados (batch ou sync) e maybe_adjust (sync sempre — depende de avaliação local)
    total_phase2 = len(courses) * max(0, min_por_curso)
    done_phase2 = 0
    call_k = 0
    for idx, (nome, exercises, domains_csv) in enumerate(ideias_por_curso):
        want = min_por_curso
        got = 0
        for ex in exercises:
            if got >= want:
                break
            if use_batch_f2:
                mc = f2_results.get(call_k, "")
                call_k += 1
            else:
                mc = _to_multiple_choice(client, ex, domains_csv)
            mc = _maybe_adjust_alternative_sizes(client, mc, ajustar_alternativas)
            mc = add_line_break_before_question(mc)
            header = f"EXERCÍCIO {len(todas_texto)+1} (curso: {nome})\n"
            todas_texto.append(header + mc)
            got += 1
            done_phase2 += 1
            if not use_batch_f2:
                _progress("  → Questões mínimas", done_phase2, total_phase2 if total_phase2 else 1)
        por_curso_count[idx] = got
    if not use_batch_f2:
        _progress_done()

    # ---- Fase 3: completar até o alvo ----
    print("Fase 3/4: Completando até atingir o alvo total...")
    def can_add_more() -> bool:
        return (len(todas_texto) < target_total) or (permitir_exceder_max and len(todas_texto) < (min_por_curso * len(courses)))

    remaining = max(0, target_total - len(todas_texto))
    done_phase3 = 0
    while can_add_more():
        progressed = False
        for idx, (nome, exercises, domains_csv) in enumerate(ideias_por_curso):
            if por_curso_count.get(idx, 0) >= max_por_curso:
                continue
            next_i = por_curso_count.get(idx, 0)
            if next_i >= len(exercises):
                # Regerar ideias (1 chamada extra) se acabaram
                course = courses[idx]
                transcription = resumo_to_transcription_text(course)
                raw_ideas = _ask_exercise_ideas(client, transcription, domains_csv)
                exercises.extend(_parse_exercise_ideas_verbatim(raw_ideas))
                if next_i >= len(exercises):
                    continue
            ex = exercises[next_i]
            mc = _to_multiple_choice(client, ex, domains_csv)
            mc = _maybe_adjust_alternative_sizes(client, mc, ajustar_alternativas)
            mc = add_line_break_before_question(mc)
            header = f"EXERCÍCIO {len(todas_texto)+1} (curso: {nome})\n"
            todas_texto.append(header + mc)
            por_curso_count[idx] = por_curso_count.get(idx, 0) + 1
            progressed = True
            done_phase3 += 1
            # atualiza barra com base no alvo inicial planejado
            _progress("  → Complemento até alvo", min(done_phase3, remaining if remaining else done_phase3), remaining if remaining else done_phase3)
            if not can_add_more():
                break
        if not progressed:
            break
    _progress_done()

    # ---- Fase 4: ranqueamento (dificuldade) ----
    print("Fase 4/4: Ranqueando dificuldade (modelo mini)...")
    txt_enumerado = []
    for i, t in enumerate(todas_texto):
        txt_enumerado.append(f"[{i}] ---\n{t}\n")
    block = "\n".join(txt_enumerado)

    resumos_compact = _compact_for_ranking(courses)
    resumos_compact_json = json.dumps(resumos_compact, ensure_ascii=False)

    try:
        _progress("  → Avaliando dificuldade", 0, 1)
        rank_resp = _chat(
            client,
            MODEL_RANK,
            system_prompt_rank_difficulty_plain(),
            "",  # rank é 1 chamada com input dinâmico — sem parte estática separada
            user_prompt_rank_difficulty_plain(
                questions_text_block=block,
                resumos_compactos_json=resumos_compact_json,
                carreira=carreira,
            ),
            temperature=TEMPERATURE_RANK,
        )
        ranking = _safe_json_loads(rank_resp) or []
        diff_map: Dict[int, float] = {}
        for obj in ranking:
            if isinstance(obj, dict) and "idx" in obj and "dificuldade" in obj:
                try:
                    diff_map[int(obj["idx"])] = float(obj["dificuldade"])
                except Exception:
                    pass
        if not diff_map:
            diff_map = {i: 3.0 for i in range(len(todas_texto))}
        _progress("  → Avaliando dificuldade", 1, 1)
    except Exception:
        diff_map = {i: 3.0 for i in range(len(todas_texto))}
        _progress("  → Avaliando dificuldade (fallback)", 1, 1)
    _progress_done()

    # Ordena por dificuldade asc (desempate: idx)
    order = sorted(range(len(todas_texto)), key=lambda i: (diff_map.get(i, 3.0), i))
    todas_texto_ord = _renumber_text_headers(todas_texto, order)

    # Anexa etiqueta de dificuldade no cabeçalho de cada exercício
    etiquetados: List[str] = []
    for new_i, old_idx in enumerate(order, start=1):
        dif = int(round(diff_map.get(old_idx, 3.0)))
        bloco = todas_texto[old_idx]
        parts = bloco.split("\n", 1)
        if len(parts) == 2:
            head, rest = parts
            head = re.sub(r"EXERCÍCIO\s+\d+", f"EXERCÍCIO {new_i}", head, count=1)
            head = head + f" [dificuldade: {dif}/5]"
            etiquetados.append(head + "\n" + rest)
        else:
            etiquetados.append(bloco)

    sep = "\n-------------------------------------------------------------------\n\n"
    txt_final = sep.join(etiquetados) + ("\n" if etiquetados else "")
    return txt_final

# =========================
# CLI
# =========================

def main():
    parser = argparse.ArgumentParser(description="Gerar Prova Teórica (Aula 2) — apenas TXT, ranking por dificuldade ascendente e menor custo.")
    parser.add_argument("--nivel", type=int, choices=[1,2,3], required=True)
    parser.add_argument("--carreira", type=str, default="", help="Nome da carreira para compor o nome do arquivo de saída")
    parser.add_argument("--resumos_arquivo", type=str, required=True, help="Caminho para o JSON de resumos do nível (da carreira).")
    parser.add_argument("--max_questoes", type=int, default=10)
    parser.add_argument("--min_por_curso", type=int, default=1)
    parser.add_argument("--max_por_curso", type=int, default=2)
    parser.add_argument("--permitir_exceder_max", action="store_true")
    parser.add_argument("--domains_window", type=int, default=3)
    parser.add_argument("--domains_arquivo", type=str, default="", help="JSON com lista de domínios (opcional)")
    parser.add_argument("--ajustar_alternativas", action="store_true", help="(Opcional) Tenta igualar o tamanho das alternativas — aumenta custo.")
    parser.add_argument("--no-batch", action="store_true", help="Desativa modo batch (Anthropic). Usa execução síncrona — apenas para debug.")
    args = parser.parse_args()

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

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    txt = gerar_prova_teorica(
        nivel=args.nivel,
        carreira=args.carreira,
        resumos_arquivo=args.resumos_arquivo,
        domains=domains,
        max_questoes=args.max_questoes,
        min_por_curso=args.min_por_curso,
        max_por_curso=args.max_por_curso,
        permitir_exceder_max=args.permitir_exceder_max,
        domains_window=args.domains_window,
        ajustar_alternativas=args.ajustar_alternativas,
        batch_mode=not args.no_batch,
    )
    elapsed = time.perf_counter() - t0

    carreira_slug = _slugify(args.carreira)
    base = f"prova_teorica_{carreira_slug}_nivel_{args.nivel}"
    out_path = OUTPUT_DIR / f"{base}.txt"
    out_path.write_text(txt, encoding="utf-8")
    print(f"[OK] TXT salvo em: {out_path}")

    mins = int(elapsed // 60)
    secs = int(elapsed % 60)
    print(f"[Tempo total] {mins} min {secs} s")
    _print_usage_summary()

if __name__ == "__main__":
    main()
