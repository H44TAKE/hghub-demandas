# -*- coding: utf-8 -*-
"""Semeia uma base DESCARTAVEL com dados de exemplo (para ver/testar a interface sem tocar na base real).

Uso (PowerShell):
    $env:HGHUB_ARQUIVO_DADOS = "$env:TEMP\\hghub_demo\\dados_sistema_hg.json"
    $env:HGHUB_ADMIN_SENHA = "demo123456"
    python tools/semear_demo.py
    python app.py --web
"""
import os
import random
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

alvo = os.environ.get("HGHUB_ARQUIVO_DADOS", "")
if not alvo or alvo.upper().startswith(("Z:", "Y:")):
    sys.exit("Defina HGHUB_ARQUIVO_DADOS para uma base descartavel (nunca Z:/Y:).")

from backend.models import Etiqueta, Pasta, Projeto, Tarefa, Usuario, sistema  # noqa: E402
from backend.utils import hash_senha  # noqa: E402

random.seed(7)
with sistema.transacao():
    if any(p.nome == "Frota" for p in sistema.pastas):
        sys.exit("A base demo ja foi semeada.")
    admin = sistema.usuarios[0]
    nomes = ["Ana Souza", "Bruno Lima", "Carla Dias", "Diego Rocha"]
    for n in nomes:
        sistema.usuarios.append(Usuario(sistema.gerar_id(), n, n.split()[0].lower() + "@redehg.com", hash_senha("demo123456"),
                                        setores=["Frota"], conta_mestre=False))
    donos = [admin] + sistema.usuarios[1:]
    hoje = datetime.now()
    estruturas = {
        "Frota": {"Revisões 2026": ["Trocar óleo cavalo ABC1D23", "Alinhamento carreta", "Revisar freios", "Pneus eixo 2", "Lavagem técnica"],
                  "Documentação": ["Renovar CRLV", "Seguro da frota", "Licença ambiental"]},
        "Qualidade": {"Auditoria SGQ": ["Checklist de inspeção", "Plano de ação NC-14", "Treinamento operacional", "Revisão de procedimentos"]},
        "RH": {"Admissões": ["Contratar motorista", "Exame admissional", "Integração"]},
    }
    status = {s.nome: s for s in sistema.status_padrao}
    prios = ["Alta", "Média", "Baixa"]
    for nome_pasta, projetos in estruturas.items():
        pasta = Pasta(sistema.gerar_id(), nome_pasta, "#0D3B8C", nome_pasta)
        for nome_proj, titulos in projetos.items():
            proj = Projeto(sistema.gerar_id(), nome_proj)
            for i, titulo in enumerate(titulos):
                st = list(status.values())[i % 4]
                prazo = hoje + timedelta(days=random.randint(-6, 20))
                tarefa = Tarefa(sistema.gerar_id(), titulo, "Descrição de exemplo da demanda.", st,
                                responsavel=random.choice(donos), data_limite=prazo, prioridade=random.choice(prios),
                                etiqueta=Etiqueta("Rotina", "#2563EB"), cidade=random.choice(["Goiânia", "Anápolis", "Brasília"]))
                proj.tarefas.append(tarefa)
            pasta.projetos.append(proj)
        sistema.pastas.append(pasta)
print("Base demo pronta. Login: admin@redehg.com /", os.environ.get("HGHUB_ADMIN_SENHA"))
