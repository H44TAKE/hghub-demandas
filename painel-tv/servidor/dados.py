# -*- coding: utf-8 -*-
"""Do resultado do leitor ao JSON que a TV desenha.

`montar_painel(veiculos)` recebe a lista achatada de `leitor.calcular_saude_frota` (um item
por cavalo/carreta, com o status já resumido por frente) e devolve só o que o painel mostra:
a saúde de cada frente e a lista de TODOS os veículos, cada um com a sua cor.

REGRA DA COR DE CADA VEÍCULO (definida pelo setor, 21/10/2026). "Pendente" = frente que NÃO está
em dia (vencida ou a vencer); "sem dado" não conta. Compara pendentes com em dia:
    mais pendentes do que em dia  -> VERMELHO
    meio a meio                   -> LARANJA
    mais em dia do que pendentes  -> VERDE   (inclui 0 pendentes e "só 1 pendente" entre vários)

A regra de saúde é a MESMA do Dashboard do app principal (`resumirSaude` em
manutencao_comum.js): saúde = % de veículos 100% EM DIA entre os que têm status conhecido;
"sem dado" fica de fora da conta.
"""

import random
from datetime import datetime

PARAR = "PARAR URGENTE"
PROGRAMAR = "PROGRAMAR PARADA"
EM_DIA = "EM DIA"

TITULOS_FRENTE = {
    "revisao_oleo": "Revisão e óleo",
    "alinhamento": "Alinhamento",
    "quinta_roda": "5ª roda",
    "integridade_chassi": "Chassi",
    "sr_tanques": "Tanques",
    "preventiva_carretas": "Preventiva",
}

PESO = {PARAR: 2, PROGRAMAR: 1}

VERMELHO, LARANJA, VERDE = "vermelho", "laranja", "verde"
ORDEM_COR = {VERMELHO: 0, LARANJA: 1, VERDE: 2}   # a lista começa pelos piores


def cor_do_veiculo(pendentes, em_dia):
    """Cor do cartão do veículo pela regra do módulo (ver o topo do arquivo)."""
    if pendentes > em_dia:
        return VERMELHO
    if pendentes == em_dia:
        return LARANJA
    return VERDE


def resumir(lista, status_de):
    """{em_dia, programar_parada, parar_urgente, sem_dado, elegiveis, pct} de um recorte."""
    r = {"em_dia": 0, "programar_parada": 0, "parar_urgente": 0, "sem_dado": 0}
    for v in lista:
        status = status_de(v)
        if status == EM_DIA:
            r["em_dia"] += 1
        elif status == PROGRAMAR:
            r["programar_parada"] += 1
        elif status == PARAR:
            r["parar_urgente"] += 1
        else:
            r["sem_dado"] += 1
    r["elegiveis"] = r["em_dia"] + r["programar_parada"] + r["parar_urgente"]
    r["pct"] = round(r["em_dia"] / r["elegiveis"] * 100) if r["elegiveis"] else None
    return r


def _tipo(v):
    return "carreta" if (v.get("tipo") or "").strip().lower() == "carreta" else "cavalo"


def sem_frentes(veiculos, frentes_ocultas):
    """Tira as frentes ocultas de cada veículo e REFAZ o status geral só com as que sobraram
    (o do leitor considera todas: um veículo vencido só no Alinhamento continuaria "vencido").
    Veículo sem nenhuma frente com status fica "sem dado". Não altera a entrada."""
    ocultas = set(frentes_ocultas or ())
    if not ocultas:
        return veiculos
    refeitos = []
    for v in veiculos:
        categorias = {k: st for k, st in (v.get("categorias") or {}).items() if k not in ocultas}
        pior = max((PESO.get(st, 0) for st in categorias.values() if st in PESO or st == EM_DIA), default=None)
        status = None if pior is None else PARAR if pior == 2 else PROGRAMAR if pior == 1 else EM_DIA
        refeitos.append({**v, "categorias": categorias, "status_geral": status})
    return refeitos


def montar_painel(veiculos, frentes_ocultas=()):
    """Resumo completo para a TV. Não altera `veiculos`. `frentes_ocultas`: chaves de frentes
    que não aparecem (ver `sem_frentes`)."""
    veiculos = sem_frentes(veiculos, frentes_ocultas)
    geral = resumir(veiculos, lambda v: v.get("status_geral"))
    geral["total"] = len(veiculos)

    chaves = []
    for v in veiculos:
        for chave in (v.get("categorias") or {}):
            if chave not in chaves:
                chaves.append(chave)
    frentes = []
    for chave in chaves:
        dela = [v for v in veiculos if chave in (v.get("categorias") or {})]
        resumo = resumir(dela, lambda v: v["categorias"][chave])
        if resumo["elegiveis"]:
            frentes.append({
                "chave": chave, "titulo": TITULOS_FRENTE.get(chave, chave), "pct": resumo["pct"],
                "parar_urgente": resumo["parar_urgente"], "programar_parada": resumo["programar_parada"],
                "em_dia": resumo["em_dia"], "total": resumo["elegiveis"],
            })
    frentes.sort(key=lambda f: (-f["parar_urgente"], f["pct"] if f["pct"] is not None else 101, f["titulo"]))

    lista = []
    for v in veiculos:
        da_frota = [
            {"chave": chave, "titulo": TITULOS_FRENTE.get(chave, chave), "status": st}
            for chave, st in (v.get("categorias") or {}).items() if st in PESO or st == EM_DIA
        ]
        if not da_frota:
            continue  # nenhuma frente com status: não há o que mostrar
        da_frota.sort(key=lambda f: (-PESO.get(f["status"], 0), f["titulo"]))
        pendentes = sum(1 for f in da_frota if f["status"] in PESO)
        lista.append({
            "placa": v.get("placa"), "tipo": _tipo(v), "marca": v.get("marca"), "modelo": v.get("modelo"),
            "cor": cor_do_veiculo(pendentes, len(da_frota) - pendentes),
            "pendentes": pendentes, "total_frentes": len(da_frota), "frentes": da_frota,
        })
    lista.sort(key=lambda a: (
        ORDEM_COR[a["cor"]], -a["pendentes"], -sum(1 for f in a["frentes"] if f["status"] == PARAR), a["placa"] or ""))
    resumo_lista = {cor: sum(1 for a in lista if a["cor"] == cor) for cor in ORDEM_COR}

    return {"saude": geral, "frentes": frentes, "lista": lista, "resumo_lista": resumo_lista}


def envelope(painel, modo, planilha_modificada_em=None, erro=None):
    """Dados + metadados de atualização (hora do servidor, aviso de dado velho)."""
    return {
        "modo": modo, "atualizado_em": datetime.now().isoformat(timespec="seconds"),
        "planilha_modificada_em": planilha_modificada_em, "erro": erro, **painel,
    }


# ---------------------------------------------------------------- modo demonstração
def veiculos_demo(semente=7):
    """Frota fictícia (nada vem do Drive) para testar e pré-visualizar o painel na TV."""
    sorteio = random.Random(semente)
    frentes_cavalo = ["revisao_oleo", "alinhamento", "quinta_roda"]
    frentes_carreta = ["alinhamento", "quinta_roda", "integridade_chassi", "preventiva_carretas"]
    marcas = {"cavalo": [("Scania", "R 450"), ("Volvo", "FH 540"), ("Mercedes", "Actros 2651")],
              "carreta": [("Randon", "SR Tanque"), ("Facchini", "SR Tanque"), ("Guerra", "SR Graneleiro")]}
    letras = "ABCDEFGHJKLMNOPRSTUVWXYZ"
    usadas = set()
    veiculos = []
    for tipo, quantidade, frentes in (("Cavalo", 62, frentes_cavalo), ("Carreta", 88, frentes_carreta)):
        for _ in range(quantidade):
            while True:
                placa = "".join(sorteio.choice(letras) for _ in range(3)) + str(sorteio.randint(1, 9)) \
                    + sorteio.choice(letras) + f"{sorteio.randint(0, 99):02d}"
                if placa not in usadas:
                    usadas.add(placa)
                    break
            categorias = {}
            for chave in frentes:
                dado = sorteio.random()
                categorias[chave] = PARAR if dado < 0.06 else PROGRAMAR if dado < 0.18 else EM_DIA
            if tipo == "Carreta" and sorteio.random() < 0.4:
                categorias["sr_tanques"] = PARAR if sorteio.random() < 0.1 else EM_DIA
            marca, modelo = sorteio.choice(marcas[tipo.lower()])
            piores = max((PESO.get(s, 0) for s in categorias.values()), default=0)
            veiculos.append({
                "placa": placa, "tipo": tipo, "marca": marca, "modelo": modelo, "categorias": categorias,
                "status_geral": PARAR if piores == 2 else PROGRAMAR if piores == 1 else EM_DIA,
            })
    return veiculos
