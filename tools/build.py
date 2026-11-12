# -*- coding: utf-8 -*-
"""Gera o executavel (PyInstaller) e o instalador (Inno Setup) do HGhub.

    python tools/build.py                 # build + instalador
    python tools/build.py --so-exe        # so o PyInstaller

Saida: installer_output/HGhub-Setup-<versao>.exe   (a versao vem de backend/versao.py)
Garante que NENHUM segredo ou dado entra no pacote."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

PROIBIDOS = {"credenciais.json", "config.json", "usuarios.json", "dados_sistema_hg.json", "permissoes_contas.json",
             "veiculos_ocultos.json", "sessao_hg.json", "SENHA_INICIAL_ADMIN.txt"}


def achar_iscc():
    candidatos = [shutil.which("iscc"), shutil.which("ISCC"),
                  os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
                  r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe"]
    return next((c for c in candidatos if c and os.path.exists(c)), None)


def conferir_pacote(pasta):
    """O pacote nao pode conter credenciais nem dados de ninguem."""
    achados = [str(p.relative_to(pasta)) for p in Path(pasta).rglob("*") if p.is_file() and p.name in PROIBIDOS]
    if achados:
        raise SystemExit("ABORTADO: arquivos que nao podem ir no pacote: " + ", ".join(achados))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--so-exe", action="store_true")
    args = ap.parse_args()

    from backend import versao
    v = versao.__version__
    print(f"== HGhub {v} ==")
    subprocess.run([sys.executable, "-m", "PyInstaller", "HGhub.spec", "--noconfirm", "--clean"], cwd=RAIZ, check=True)
    pasta = RAIZ / "dist" / "HGhub"
    if not (pasta / "HGhub.exe").exists():
        raise SystemExit("PyInstaller nao gerou dist/HGhub/HGhub.exe")
    conferir_pacote(pasta)
    print("Pacote conferido: sem credenciais nem dados.")
    if args.so_exe:
        return

    iscc = achar_iscc()
    if not iscc:
        raise SystemExit("Inno Setup 6 (ISCC.exe) nao encontrado. Instale: winget install JRSoftware.InnoSetup")
    subprocess.run([iscc, f"/DMyAppVersion={v}", f"/DSourceDir={pasta}", str(RAIZ / "installer" / "hghub.iss")], cwd=RAIZ, check=True)
    saida = RAIZ / "installer_output" / f"HGhub-Setup-{v}.exe"
    print(f"Instalador: {saida} ({saida.stat().st_size / 1_048_576:.1f} MB)")


if __name__ == "__main__":
    main()
