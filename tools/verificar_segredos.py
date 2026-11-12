# -*- coding: utf-8 -*-
"""Falha se algum arquivo RASTREADO pelo git contiver segredo ou dado de producao.
Roda no CI e antes de publicar (python tools/verificar_segredos.py)."""

import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PADROES = {
    "chave privada": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "senha de app (4x4 letras)": re.compile(r"[\"']\s*[a-z]{4} [a-z]{4} [a-z]{4} [a-z]{4}\s*[\"']"),
    "token do GitHub": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "chave de API do Google": re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    "chave AWS": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "senha fixa 123456 para o admin": re.compile(r"hash_senha\(\s*[\"']123456[\"']\s*\)"),
}
NOMES_PROIBIDOS = {"credenciais.json", "config.json", "usuarios.json", "dados_sistema_hg.json", "permissoes_contas.json",
                   "veiculos_ocultos.json", "sessao_hg.json"}
# arquivos de teste podem conter senhas FALSAS de exemplo
IGNORAR = ("tests/",)


def main():
    arquivos = subprocess.run(["git", "ls-files"], cwd=RAIZ, capture_output=True, text=True, check=True).stdout.splitlines()
    problemas = []
    for rel in arquivos:
        nome = Path(rel).name
        if nome in NOMES_PROIBIDOS or rel.startswith(("fotos_usuarios/", "anexos_tarefas/")) or nome.endswith((".pem", ".key")):
            problemas.append(f"{rel}: arquivo de dados/credencial rastreado")
            continue
        if rel.startswith(IGNORAR) or not Path(RAIZ, rel).is_file():
            continue
        try:
            texto = Path(RAIZ, rel).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for rotulo, rx in PADROES.items():
            if rx.search(texto):
                problemas.append(f"{rel}: {rotulo}")
    if problemas:
        print("SEGREDOS/DADOS ENCONTRADOS NO REPOSITORIO:")
        print("\n".join("  - " + p for p in problemas))
        return 1
    print(f"Nenhum segredo em {len(arquivos)} arquivos rastreados.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
