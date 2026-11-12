# -*- coding: utf-8 -*-
"""Ferramentas do canal de atualizacao do HGhub (uso do desenvolvedor e do CI).

O canal funciona assim: cada release do GitHub publica o instalador, um MANIFESTO
(`update-manifest.json`, com versao, tamanho e SHA-256 do instalador) e a ASSINATURA
Ed25519 desse manifesto (`update-manifest.json.sig`). O aplicativo so confia em um
manifesto cuja assinatura confira com a chave publica embutida nele (backend/versao.py).
A chave PRIVADA nunca entra no repositorio: fica no cofre de segredos do GitHub
(UPDATE_SIGNING_KEY) e numa copia de seguranca offline do desenvolvedor.

Uso:
    python tools/atualizacao.py gerar-chaves --saida PASTA
    python tools/atualizacao.py manifesto --versao 1.0.1 --instalador ARQ --repo dono/repo --notas notas.md
    python tools/atualizacao.py assinar --manifesto update-manifest.json [--chave ARQ.pem]
    python tools/atualizacao.py verificar --manifesto M --assinatura S --chave-publica B64
    python tools/atualizacao.py checksums ARQ1 ARQ2 ... --saida SHA256SUMS.txt
"""

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ESQUEMA = 1
PRODUTO = "hghub"
PADRAO_VERSAO = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


def sha256_arquivo(caminho):
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def serializar_manifesto(obj):
    """Bytes canonicos do manifesto: o que e assinado e o que o app le sao SEMPRE estes bytes."""
    return (json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def montar_manifesto(versao, instalador, repo, notas, obrigatoria=False, versao_minima=None, publicado_em=None):
    if not PADRAO_VERSAO.match(versao):
        raise SystemExit(f"Versao invalida: {versao!r} (use X.Y.Z)")
    instalador = Path(instalador)
    nome_esperado = f"HGhub-Setup-{versao}.exe"
    if instalador.name != nome_esperado:
        raise SystemExit(f"O instalador deve se chamar {nome_esperado} (chamou-se {instalador.name}).")
    return {
        "esquema": ESQUEMA,
        "produto": PRODUTO,
        "versao": versao,
        "publicado_em": publicado_em or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "obrigatoria": bool(obrigatoria),
        "versao_minima_suportada": versao_minima or "0.0.0",
        "instalador": {
            "nome": instalador.name,
            "url": f"https://github.com/{repo}/releases/download/v{versao}/{instalador.name}",
            "tamanho": instalador.stat().st_size,
            "sha256": sha256_arquivo(instalador),
        },
        "notas": [linha for linha in notas if linha.strip()],
        "url_novidades": f"https://github.com/{repo}/releases/tag/v{versao}",
    }


def carregar_chave_privada(caminho=None, pem_texto=None):
    if pem_texto is None:
        pem_texto = os.environ.get("UPDATE_SIGNING_KEY")
    if caminho:
        dados = Path(caminho).read_bytes()
    elif pem_texto:
        dados = pem_texto.encode("utf-8")
    else:
        raise SystemExit("Informe --chave ARQUIVO.pem ou defina a variavel de ambiente UPDATE_SIGNING_KEY.")
    chave = serialization.load_pem_private_key(dados, password=None)
    if not isinstance(chave, Ed25519PrivateKey):
        raise SystemExit("A chave precisa ser Ed25519.")
    return chave


def chave_publica_b64(chave_privada):
    bruto = chave_privada.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(bruto).decode("ascii")


def assinar_bytes(chave_privada, dados):
    return base64.b64encode(chave_privada.sign(dados)).decode("ascii")


def verificar_bytes(dados, assinatura_b64, chaves_publicas_b64):
    try:
        assinatura = base64.b64decode(assinatura_b64.strip(), validate=True)
    except Exception:
        return False
    for chave_b64 in chaves_publicas_b64:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(chave_b64)).verify(assinatura, dados)
            return True
        except (InvalidSignature, ValueError):
            continue
    return False


# ----------------------------------------------------------------------------- comandos
def cmd_gerar_chaves(args):
    pasta = Path(args.saida)
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / "update_ed25519_private.pem"
    if destino.exists() and not args.forcar:
        raise SystemExit(f"Ja existe uma chave em {destino}. Use --forcar SO se tiver certeza (a antiga deixa de valer).")
    chave = Ed25519PrivateKey.generate()
    destino.write_bytes(chave.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    print(f"Chave privada gravada em: {destino}  (GUARDE uma copia offline; nunca a envie ao GitHub)")
    print(f"CHAVE_PUBLICA_B64={chave_publica_b64(chave)}")


def cmd_manifesto(args):
    notas = Path(args.notas).read_text(encoding="utf-8").splitlines() if args.notas and Path(args.notas).exists() else []
    notas = [re.sub(r"^\s*[-*]\s*", "", n) for n in notas]
    manifesto = montar_manifesto(args.versao, args.instalador, args.repo, notas, args.obrigatoria, args.versao_minima)
    Path(args.saida).write_bytes(serializar_manifesto(manifesto))
    print(f"Manifesto gravado em {args.saida} (sha256 do instalador: {manifesto['instalador']['sha256']})")


def cmd_assinar(args):
    chave = carregar_chave_privada(args.chave)
    dados = Path(args.manifesto).read_bytes()
    assinatura = assinar_bytes(chave, dados)
    destino = Path(args.saida or (args.manifesto + ".sig"))
    destino.write_text(assinatura + "\n", encoding="ascii")
    print(f"Assinatura gravada em {destino}")


def cmd_verificar(args):
    dados = Path(args.manifesto).read_bytes()
    assinatura = Path(args.assinatura).read_text(encoding="ascii")
    if not verificar_bytes(dados, assinatura, [args.chave_publica]):
        raise SystemExit("ASSINATURA INVALIDA: o manifesto nao confere com a chave publica.")
    print("Assinatura valida.")


def cmd_checksums(args):
    linhas = [f"{sha256_arquivo(a)}  {Path(a).name}" for a in args.arquivos]
    Path(args.saida).write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print("\n".join(linhas))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("gerar-chaves")
    a.add_argument("--saida", required=True)
    a.add_argument("--forcar", action="store_true")
    a.set_defaults(fn=cmd_gerar_chaves)

    a = sub.add_parser("manifesto")
    a.add_argument("--versao", required=True)
    a.add_argument("--instalador", required=True)
    a.add_argument("--repo", required=True, help="dono/repositorio onde o release e publicado")
    a.add_argument("--notas", help="arquivo de texto/markdown com as novidades (uma por linha)")
    a.add_argument("--obrigatoria", action="store_true")
    a.add_argument("--versao-minima")
    a.add_argument("--saida", default="update-manifest.json")
    a.set_defaults(fn=cmd_manifesto)

    a = sub.add_parser("assinar")
    a.add_argument("--manifesto", required=True)
    a.add_argument("--chave")
    a.add_argument("--saida")
    a.set_defaults(fn=cmd_assinar)

    a = sub.add_parser("verificar")
    a.add_argument("--manifesto", required=True)
    a.add_argument("--assinatura", required=True)
    a.add_argument("--chave-publica", required=True)
    a.set_defaults(fn=cmd_verificar)

    a = sub.add_parser("checksums")
    a.add_argument("arquivos", nargs="+")
    a.add_argument("--saida", default="SHA256SUMS.txt")
    a.set_defaults(fn=cmd_checksums)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
