# -*- coding: utf-8 -*-
"""Testes do Painel de TV. Rodar de dentro de painel-tv/:  python -m unittest discover -s testes -v

Nenhum teste fala com o Google: o Drive é substituído por funções falsas."""

import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Isola os testes do config.json real (que tem o ID da planilha de produção): sem isto, os testes
# de "não configurado" acabariam lendo o Drive de verdade. Tem de vir ANTES de importar `servidor`.
os.environ["PAINEL_CONFIG"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nao_existe.json")
os.environ.pop("PAINEL_PLANILHA_ID", None)
os.environ.pop("HGHUB_SHEETS_ID", None)

from servidor import dados  # noqa: E402
from servidor.app import AVISO_APOS_FALHAS, criar_app  # noqa: E402
from servidor.configuracao import PADROES  # noqa: E402


def veiculo(placa, tipo, status_geral, categorias):
    return {"placa": placa, "tipo": tipo, "marca": "X", "modelo": "Y", "status_geral": status_geral, "categorias": categorias}


FROTA = [
    veiculo("AAA1A11", "Cavalo", "EM DIA", {"revisao_oleo": "EM DIA", "alinhamento": "EM DIA"}),
    veiculo("BBB2B22", "Cavalo", "PARAR URGENTE", {"revisao_oleo": "PARAR URGENTE", "alinhamento": "PROGRAMAR PARADA"}),
    veiculo("CCC3C33", "Carreta", "PROGRAMAR PARADA", {"alinhamento": "PROGRAMAR PARADA", "sr_tanques": "EM DIA"}),
    veiculo("DDD4D44", "Carreta", "EM DIA", {"alinhamento": "EM DIA"}),
    veiculo("EEE5E55", "Carreta", None, {}),  # recém-cadastrada, sem status: fora da conta
    veiculo("FFF6F66", "Carreta", "PARAR URGENTE", {"alinhamento": "PARAR URGENTE", "quinta_roda": "PARAR URGENTE"}),
]


class SaudeDaFrota(unittest.TestCase):
    def setUp(self):
        self.p = dados.montar_painel(FROTA)

    def test_saude_geral_ignora_quem_nao_tem_status(self):
        s = self.p["saude"]
        self.assertEqual((s["em_dia"], s["programar_parada"], s["parar_urgente"], s["sem_dado"]), (2, 1, 2, 1))
        self.assertEqual(s["elegiveis"], 5)
        self.assertEqual(s["pct"], 40)          # 2 em dia de 5 com status; o "sem dado" não entra no denominador
        self.assertEqual(s["total"], 6)

    def test_saude_de_uma_frente_usa_so_quem_tem_a_frente(self):
        frentes = {f["chave"]: f for f in self.p["frentes"]}
        self.assertEqual(frentes["alinhamento"]["total"], 5)      # 5 veículos têm alinhamento
        self.assertEqual(frentes["alinhamento"]["pct"], 40)
        self.assertEqual(frentes["alinhamento"]["em_dia"], 2)          # contagem em dia, para o cartão da frente
        self.assertEqual(frentes["sr_tanques"]["pct"], 100)       # cavalos não entram em tanques
        self.assertEqual(frentes["sr_tanques"]["total"], 1)

    def test_frentes_ordenadas_pelas_mais_urgentes(self):
        # quinta_roda e alinhamento têm 1 urgente cada; desempata a pior saúde (0% < 40%), como no app
        self.assertEqual([f["chave"] for f in self.p["frentes"][:2]], ["quinta_roda", "alinhamento"])

    def test_a_lista_tem_todos_os_veiculos_com_status_do_pior_para_o_melhor(self):
        placas = [v["placa"] for v in self.p["lista"]]
        # vermelhos (mais frentes pendentes primeiro), laranja, depois os verdes (os que estão em dia)
        self.assertEqual(placas, ["FFF6F66", "BBB2B22", "CCC3C33", "AAA1A11", "DDD4D44"])
        self.assertNotIn("EEE5E55", placas)            # sem nenhuma frente com status: não há o que mostrar
        self.assertEqual([v["cor"] for v in self.p["lista"]], ["vermelho", "vermelho", "laranja", "verde", "verde"])

    def test_os_veiculos_em_dia_tambem_aparecem(self):
        aaa = next(v for v in self.p["lista"] if v["placa"] == "AAA1A11")
        self.assertEqual((aaa["cor"], aaa["pendentes"], aaa["total_frentes"]), ("verde", 0, 2))
        self.assertEqual([(f["titulo"], f["status"]) for f in aaa["frentes"]],
                         [("Alinhamento", "EM DIA"), ("Revisão e óleo", "EM DIA")])

    def test_cada_linha_traz_todas_as_frentes_pior_primeiro(self):
        bbb = next(v for v in self.p["lista"] if v["placa"] == "BBB2B22")
        self.assertEqual([(f["titulo"], f["status"]) for f in bbb["frentes"]],
                         [("Revisão e óleo", "PARAR URGENTE"), ("Alinhamento", "PROGRAMAR PARADA")])
        self.assertEqual((bbb["tipo"], bbb["pendentes"], bbb["total_frentes"]), ("cavalo", 2, 2))
        ccc = next(v for v in self.p["lista"] if v["placa"] == "CCC3C33")
        self.assertEqual([f["status"] for f in ccc["frentes"]], ["PROGRAMAR PARADA", "EM DIA"])   # as em dia vêm depois

    def test_resumo_da_lista_conta_cada_cor(self):
        self.assertEqual(self.p["resumo_lista"], {"vermelho": 2, "laranja": 1, "verde": 2})

    def test_frota_vazia_nao_quebra(self):
        p = dados.montar_painel([])
        self.assertIsNone(p["saude"]["pct"])
        self.assertEqual(p["lista"], [])
        self.assertEqual(p["resumo_lista"], {"vermelho": 0, "laranja": 0, "verde": 0})
        self.assertEqual(p["frentes"], [])

    def test_nao_altera_a_entrada(self):
        antes = repr(FROTA)
        dados.montar_painel(FROTA)
        self.assertEqual(repr(FROTA), antes)

    def test_demo_e_estavel_e_valido(self):
        a, b = dados.veiculos_demo(), dados.veiculos_demo()
        self.assertEqual(a, b)
        self.assertEqual(len({v["placa"] for v in a}), len(a))        # sem placa repetida
        p = dados.montar_painel(a)
        self.assertTrue(p["lista"])
        self.assertEqual(sum(p["resumo_lista"].values()), len(p["lista"]))


class RegraDeCor(unittest.TestCase):
    """A regra do setor: mais pendentes que em dia = vermelho; meio a meio = laranja; mais em dia = verde."""

    def test_comparacao_entre_pendentes_e_em_dia(self):
        casos = [
            # (pendentes, em_dia, cor)
            (0, 1, "verde"), (0, 4, "verde"),        # tudo em dia
            (1, 3, "verde"), (1, 2, "verde"),        # "só 1 pendente" entre vários = verde
            (1, 1, "laranja"), (2, 2, "laranja"),    # meio a meio
            (2, 1, "vermelho"), (3, 1, "vermelho"),  # mais pendentes
            (1, 0, "vermelho"), (4, 0, "vermelho"),  # tudo pendente
        ]
        for pendentes, em_dia, esperada in casos:
            self.assertEqual(dados.cor_do_veiculo(pendentes, em_dia), esperada, (pendentes, em_dia))

    def cor_de(self, categorias):
        v = [veiculo("ZZZ9Z99", "Carreta", None, categorias)]
        return dados.montar_painel(v)["lista"][0]["cor"]

    def test_a_vencer_tambem_e_pendente(self):
        self.assertEqual(self.cor_de({"a": "PROGRAMAR PARADA", "b": "EM DIA", "c": "EM DIA", "d": "EM DIA"}), "verde")
        self.assertEqual(self.cor_de({"a": "PROGRAMAR PARADA", "b": "PROGRAMAR PARADA", "c": "EM DIA", "d": "EM DIA"}), "laranja")
        self.assertEqual(self.cor_de({"a": "PROGRAMAR PARADA", "b": "PARAR URGENTE", "c": "PARAR URGENTE", "d": "EM DIA"}), "vermelho")

    def test_vencida_e_a_vencer_valem_o_mesmo_na_conta(self):
        self.assertEqual(self.cor_de({"a": "PARAR URGENTE", "b": "EM DIA", "c": "EM DIA", "d": "EM DIA"}), "verde")
        self.assertEqual(self.cor_de({"a": "PARAR URGENTE", "b": "EM DIA"}), "laranja")

    def test_frente_sem_dado_nao_conta(self):
        self.assertEqual(self.cor_de({"a": "PARAR URGENTE", "b": None, "c": "EM DIA"}), "laranja")
        self.assertEqual(self.cor_de({"a": "PARAR URGENTE", "b": None}), "vermelho")


class FrentesOcultas(unittest.TestCase):
    """O painel pode esconder frentes (hoje o Alinhamento, cuja aba da planilha está desatualizada)."""

    def test_a_frente_some_dos_cartoes_e_dos_chips(self):
        p = dados.montar_painel(FROTA, ["alinhamento"])
        self.assertNotIn("alinhamento", [f["chave"] for f in p["frentes"]])
        for linha in p["lista"]:
            self.assertNotIn("alinhamento", [f["chave"] for f in linha["frentes"]])

    def test_a_cor_e_a_lista_sao_refeitas_sem_a_frente(self):
        p = dados.montar_painel(FROTA, ["alinhamento"])
        por_placa = {v["placa"]: v for v in p["lista"]}
        self.assertEqual([v["placa"] for v in p["lista"]], ["BBB2B22", "FFF6F66", "AAA1A11", "CCC3C33"])
        self.assertNotIn("DDD4D44", por_placa)         # só tinha Alinhamento: ficou sem nenhuma frente
        self.assertEqual(por_placa["CCC3C33"]["cor"], "verde")      # sobrou só tanques, em dia
        self.assertEqual(por_placa["BBB2B22"]["cor"], "vermelho")   # sobrou revisão e óleo, vencida
        self.assertEqual([f["chave"] for f in por_placa["BBB2B22"]["frentes"]], ["revisao_oleo"])

    def test_status_geral_e_refeito_sem_a_frente(self):
        p = dados.montar_painel(FROTA, ["alinhamento"])
        s = p["saude"]
        # AAA em dia, BBB urgente (óleo), CCC em dia (só tanques sobrou), DDD sem frentes -> sem dado,
        # EEE sem dado, FFF urgente (5ª roda)
        self.assertEqual((s["em_dia"], s["programar_parada"], s["parar_urgente"], s["sem_dado"]), (2, 0, 2, 2))
        self.assertEqual(s["total"], 6)

    def test_nao_altera_a_entrada_e_sem_ocultas_nada_muda(self):
        antes = repr(FROTA)
        dados.montar_painel(FROTA, ["alinhamento"])
        self.assertEqual(repr(FROTA), antes)
        self.assertEqual(dados.montar_painel(FROTA, []), dados.montar_painel(FROTA))
        self.assertIn("alinhamento", [f["chave"] for f in dados.montar_painel(FROTA)["frentes"]])

    def test_o_padrao_do_painel_esconde_o_alinhamento(self):
        self.assertEqual(PADROES["frentes_ocultas"], ["alinhamento"])

    def test_servidor_aplica_a_configuracao(self):
        app = criar_app(buscar_veiculos=lambda: (FROTA, None), config={**PADROES, "frentes_ocultas": ["alinhamento"]})
        app.atualizador.atualizar_uma_vez()
        r = app.test_client().get("/api/painel").get_json()
        self.assertNotIn("alinhamento", [f["chave"] for f in r["frentes"]])
        self.assertEqual([v["placa"] for v in r["lista"]], ["BBB2B22", "FFF6F66", "AAA1A11", "CCC3C33"])
        self.assertEqual(r["resumo_lista"], {"vermelho": 2, "laranja": 0, "verde": 2})

    def test_config_json_com_bom_e_lido(self):
        """Editado à mão (PowerShell 5.1, Bloco de Notas antigo) o config.json vem com BOM; sem tolerância o
        painel caía em silêncio nos padrões e mostrava 'não configurado'."""
        import json
        import tempfile
        from servidor import configuracao
        arquivo = os.path.join(tempfile.mkdtemp(prefix="painel_bom_"), "config.json")
        with open(arquivo, "w", encoding="utf-8-sig") as f:
            json.dump({"planilha_id": "ID-DE-TESTE", "frentes_ocultas": ["chassi"]}, f)
        anterior = configuracao.ARQUIVO_CONFIG
        configuracao.ARQUIVO_CONFIG = arquivo
        try:
            lida = configuracao.carregar_config()
        finally:
            configuracao.ARQUIVO_CONFIG = anterior
        self.assertEqual(lida["planilha_id"], "ID-DE-TESTE")
        self.assertEqual(lida["frentes_ocultas"], ["chassi"])

    def test_config_invalida_cai_no_padrao(self):
        import json
        import tempfile
        from servidor import configuracao
        arquivo = os.path.join(tempfile.mkdtemp(prefix="painel_cfg_"), "config.json")
        for valor, esperado in (("alinhamento", ["alinhamento"]), (["quinta_roda", " "], ["quinta_roda"]), ([], [])):
            json.dump({"frentes_ocultas": valor}, open(arquivo, "w", encoding="utf-8"))
            anterior = configuracao.ARQUIVO_CONFIG
            configuracao.ARQUIVO_CONFIG = arquivo
            try:
                self.assertEqual(configuracao.carregar_config()["frentes_ocultas"], esperado)
            finally:
                configuracao.ARQUIVO_CONFIG = anterior


class Servidor(unittest.TestCase):
    def config(self, **extra):
        return {**PADROES, "atualizar_a_cada_segundos": 300, "frentes_ocultas": [], **extra}

    def cliente(self, buscar, **extra):
        app = criar_app(buscar_veiculos=buscar, config=self.config(**extra))
        return app, app.test_client()

    def test_antes_da_primeira_leitura_diz_que_esta_carregando(self):
        _, c = self.cliente(lambda: (FROTA, None))
        r = c.get("/api/painel").get_json()
        self.assertTrue(r["carregando"])
        self.assertNotIn("saude", r)

    def test_depois_da_leitura_entrega_o_painel(self):
        app, c = self.cliente(lambda: (FROTA, "2026-10-20T10:00:00Z"))
        app.atualizador.atualizar_uma_vez()
        r = c.get("/api/painel").get_json()
        self.assertEqual(r["saude"]["pct"], 40)
        self.assertEqual(r["modo"], "real")
        self.assertEqual(r["planilha_modificada_em"], "2026-10-20T10:00:00Z")
        self.assertFalse(r["desatualizado"])
        self.assertEqual(r["intervalo_segundos"], 300)

    def test_modo_demo_e_sinalizado(self):
        app, c = self.cliente(lambda: (dados.veiculos_demo(), None), modo_demo=True)
        app.atualizador.atualizar_uma_vez()
        self.assertEqual(c.get("/api/painel").get_json()["modo"], "demo")

    def test_falha_depois_de_sucesso_mantem_o_ultimo_dado_e_avisa(self):
        estado = {"falhar": False}

        def buscar():
            if estado["falhar"]:
                raise RuntimeError("sem internet")
            return FROTA, None

        app, c = self.cliente(buscar)
        app.atualizador.atualizar_uma_vez()
        estado["falhar"] = True
        for i in range(AVISO_APOS_FALHAS):
            app.atualizador.atualizar_uma_vez()
            r = c.get("/api/painel").get_json()
            self.assertEqual(r["saude"]["pct"], 40)             # nunca perde o que já tinha
            self.assertEqual(r["desatualizado"], i + 1 >= AVISO_APOS_FALHAS)
        estado["falhar"] = False
        app.atualizador.atualizar_uma_vez()
        self.assertFalse(c.get("/api/painel").get_json()["desatualizado"])   # voltou: aviso some

    def test_sem_planilha_configurada_mostra_o_que_falta_em_vez_de_quebrar(self):
        app, c = self.cliente(None)   # usa o leitor real, sem planilha_id
        os.environ.pop("PAINEL_PLANILHA_ID", None)
        app.atualizador.config["planilha_id"] = ""
        app.atualizador.atualizar_uma_vez()
        r = c.get("/api/painel").get_json()
        self.assertTrue(r["nao_configurado"])
        self.assertIn("planilha", r["mensagem"].lower())

    def test_mensagem_de_nao_configurado_aponta_o_arquivo_certo(self):
        os.environ.pop("PAINEL_PLANILHA_ID", None)
        os.environ.pop("HGHUB_SHEETS_ID", None)
        app, c = self.cliente(None)
        app.atualizador.atualizar_uma_vez()
        mensagem = c.get("/api/painel").get_json()["mensagem"]
        self.assertIn("planilha_id", mensagem)
        self.assertIn("config.json", mensagem)
        self.assertNotIn("HGHUB_SHEETS_ID", mensagem)       # nome do app principal, não vale aqui

    def test_planilha_id_do_config_chega_ao_leitor(self):
        from servidor import configuracao, leitor
        os.environ["PAINEL_PLANILHA_ID"] = "  id-de-teste  "
        os.environ.pop("HGHUB_SHEETS_ID", None)
        try:
            self.assertEqual(configuracao.carregar_config()["google_sheets"]["planilha_id"], "id-de-teste")
            self.assertEqual(leitor._config_sheets()[0], "id-de-teste")
        finally:
            os.environ.pop("PAINEL_PLANILHA_ID", None)

    def test_paginas_estaticas(self):
        _, c = self.cliente(lambda: (FROTA, None))
        self.assertEqual(c.get("/").status_code, 200)
        for arquivo in ("painel.css", "painel.js", "logo.png"):
            self.assertEqual(c.get(f"/web/{arquivo}").status_code, 200, arquivo)
        self.assertEqual(c.get("/web/../config.json").status_code, 404)   # não vaza arquivos fora de web/


class LeitorSomenteLeitura(unittest.TestCase):
    """O painel nunca pode gravar na planilha: garante que nada de escrita veio na cópia."""

    @classmethod
    def setUpClass(cls):
        caminho = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "servidor", "leitor.py")
        cls.fonte = open(caminho, encoding="utf-8").read()
        cls.funcoes = {n.name for n in ast.walk(ast.parse(cls.fonte)) if isinstance(n, ast.FunctionDef)}

    def test_escopo_do_google_e_somente_leitura(self):
        self.assertIn("drive.readonly", self.fonte)
        self.assertEqual(self.fonte.count("googleapis.com/auth/"), 1)        # um único escopo declarado
        self.assertNotIn('auth/drive"', self.fonte)

    def test_nenhuma_funcao_de_escrita(self):
        for nome in ("atualizar_campo_veiculo", "substituir_planilha_no_drive", "cadastrar_veiculo", "definir_visibilidade"):
            self.assertNotIn(nome, self.funcoes)

    def test_nenhuma_chamada_de_escrita_na_api_do_drive(self):
        for chamada in ("files().update(", "files().create(", "files().delete(", "files().copy(", "permissions()",
                        "MediaIoBaseUpload", "MediaFileUpload"):
            self.assertNotIn(chamada, self.fonte, chamada)

    def test_tem_o_que_o_painel_precisa(self):
        self.assertTrue({"montar_prontuario_frota", "calcular_saude_frota", "forcar_atualizacao"} <= self.funcoes)


if __name__ == "__main__":
    unittest.main()
