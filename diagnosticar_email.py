# -*- coding: utf-8 -*-
"""Diagnostico do e-mail de recuperacao de senha: conecta no servidor SMTP configurado e FAZ O LOGIN,
SEM ENVIAR NENHUMA MENSAGEM. Mostra se a senha de app da conta remetente esta valendo.

    python diagnosticar_email.py

Le o `smtp` do config.json da pasta de dados (Z:/Y:, a mesma que o sistema usa); so le, nunca grava.
Nunca imprime a senha."""
import json
import smtplib
import sys

from backend import utils

config = json.load(open(utils.ARQUIVO_CONFIG, encoding="utf-8-sig")).get("smtp", {})
remetente = config.get("email")
senha = (config.get("senha_app") or "").replace(" ", "")
servidor, porta = config.get("servidor", "smtp.gmail.com"), config.get("porta", 587)

print(f"config.json: {utils.ARQUIVO_CONFIG}")
if not remetente or not senha:
    print("PROBLEMA: faltam 'email' e/ou 'senha_app' em smtp, no config.json.")
    sys.exit(2)
print(f"Remetente: {remetente} | servidor: {servidor}:{porta} | senha de app: preenchida ({len(senha)} caracteres)")

try:
    with smtplib.SMTP(servidor, porta, timeout=20) as srv:
        srv.starttls()
        srv.ehlo()
        utils._autenticar_smtp(srv, remetente, senha)
    print("OK: o login foi aceito. O e-mail de recuperacao de senha deve funcionar (nenhuma mensagem foi enviada neste teste).")
except smtplib.SMTPAuthenticationError as erro:
    print(f"PROBLEMA: o servidor RECUSOU o login (erro {erro.smtp_code}).")
    print("A senha de app foi revogada ou trocada. Gere uma nova senha de app para essa conta e atualize")
    print("'smtp.senha_app' no config.json acima. Nao precisa recompilar nem reabrir o programa: o sistema le")
    print("esse arquivo a cada envio, e ele e o mesmo para todos os PCs. Depois rode este teste de novo.")
    sys.exit(1)
except Exception as erro:
    print(f"PROBLEMA de conexao: {type(erro).__name__}: {erro}")
    sys.exit(3)
