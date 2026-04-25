"""
Mapa de carreiras/níveis → lista de IDs de cursos.

Extraído dos comentários em `scraping_app.py` do projeto SCRAPING_FORMAÇÕES.
Mantenha este arquivo como fonte única da verdade para ordens de execução do
scraping — adicione novas carreiras/níveis aqui conforme forem sendo definidos.

Uso via CLI:
    python scripts/obter_transcricoes_cursos.py --carreira analise_de_dados --nivel 1
    python scripts/obter_transcricoes_cursos.py --nome_saida governanca_de_dados_nivel_1 \\
        --ids 3713,4631,4632,3714,4633,3716,4635,3717,5166,4634
"""
from __future__ import annotations
from typing import Dict, List

CARREIRAS_NIVEIS: Dict[str, Dict[int, List[int]]] = {
    "analise_de_dados": {
        1: [4892, 4945, 3395, 3396, 3457, 4893, 4894, 2788, 4895, 3679, 3680,
            3681, 3682, 2925, 2926, 2927, 4896, 2929, 2928, 4217, 4897, 3055],
        2: [4944, 4899, 3053, 3676, 1273, 4900, 3685, 3686, 3687, 3688, 3728,
            3713, 4165, 4166, 5226, 5227, 4901],
        3: [2675, 2710, 2941, 4024, 4014, 4025, 4129, 3971, 3677, 4902, 4903],
    },
    "cientista_de_dados": {
        1: [2925, 2926, 2927, 2928, 2929, 3055, 3057, 3137, 3056, 3676, 3396,
            3675, 4806, 3054, 3067, 3069, 3068, 3070, 3677, 3766, 3767, 3778,
            3764, 3765, 2273, 2277, 3448],
        2: [3071, 2605, 3264, 3072, 3064, 3768, 3769, 3771, 3770, 2662, 4165,
            4167, 4168, 4169, 4945, 3694, 2651, 2652, 2654, 4815],
        3: [2737, 3773, 3981, 3982, 3972, 3974, 3973, 3976, 3977, 4810, 4811,
            4812, 4814],
    },
    "desenvolvimento_back_end_java": {
        1: [2858, 2887, 2914, 2944, 4555, 4556, 4557, 4559, 4580, 4581, 4582,
            4558, 4560, 3257, 3355, 3356, 3441, 3149, 3238, 3349, 3584, 3596],
        2: [2700, 2770, 2771, 2225, 3865, 3866, 3867, 4349, 2306, 2545, 2625,
            2731, 1552, 1555, 1556, 1557, 3086, 3983, 2644],
        3: [3857, 3895, 1846, 1916, 3746, 3820, 3868, 3869, 2697, 2757, 2825,
            2905],
    },
    "desenvolvimento_back_end_php": {
        1: [3730, 3731, 3732, 3733, 2632, 2442, 1255, 1727, 3148, 2793, 2867,
            2870, 2129, 2130, 2131, 2547, 2548, 1262, 1316, 1509, 1831, 3584,
            3596],
        2: [2612, 2613, 2638, 2639, 2640, 2781, 2893, 1315, 1957, 2144, 2571,
            3410, 3536, 3983, 2644],
        3: [1959, 2143, 1956, 1944, 1960, 2226, 1668, 1669, 1670, 1774, 1822,
            3099, 3284, 4706, 2697, 2757, 2825, 2905, 1846, 1916, 3746, 3820,
            3868, 3869],
    },
    "desenvolvimento_back_end_python": {
        3: [4888, 5549, 1846, 3746, 1916, 3820, 3868, 3869, 2905],
    },
    "site_reliability_engineering": {
        1: [3392, 1649, 4072, 2644, 2697, 2825, 2421, 3161, 3092],
        2: [2289, 2290, 2291, 4130, 4062, 3859, 3375, 2380, 4597, 3820, 1916,
            1846, 3868, 3869, 3746],
        3: [3529, 2460, 2403, 2341, 2272, 2312, 2575, 2508, 2522, 2301, 2765],
    },
    "engenharia_ia": {
        2: [4912, 4795, 1414, 1563, 1892, 1734, 3975],
        3: [4913, 4793, 4916, 4917, 4914, 3971, 3977, 3677],
    },
    "especialista_ia": {
        3: [4918, 4793, 4916, 4917, 4914, 3977],
    },
    "governanca_de_dados": {
        1: [3713, 4631, 4632, 3714, 4633, 3716, 4635, 3717, 5166, 4634],
    },
}


def listar_carreiras() -> List[str]:
    return sorted(CARREIRAS_NIVEIS.keys())


def listar_niveis(carreira: str) -> List[int]:
    return sorted(CARREIRAS_NIVEIS.get(carreira, {}).keys())


def obter_ids(carreira: str, nivel: int) -> List[int]:
    try:
        return list(CARREIRAS_NIVEIS[carreira][nivel])
    except KeyError as e:
        raise KeyError(
            f"Combinação carreira/nível não encontrada: {carreira!r}/{nivel}. "
            f"Opções: {listar_carreiras()}"
        ) from e
