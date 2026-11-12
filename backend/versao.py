# -*- coding: utf-8 -*-
"""Identidade e versao do aplicativo - FONTE UNICA DA VERDADE.

O instalador, o executavel, o manifesto de atualizacao e a tag do GitHub (vX.Y.Z) leem
daqui. O CI recusa publicar uma tag que nao seja igual a `__version__`.

Versionamento semantico (MAJOR.MINOR.PATCH):
    PATCH - correcoes que nao mudam o comportamento      (1.0.0 -> 1.0.1)
    MINOR - funcionalidades novas compativeis            (1.0.1 -> 1.1.0)
    MAJOR - mudancas incompativeis ou de grande impacto  (1.1.0 -> 2.0.0)
"""

__version__ = "1.0.0"

NOME_APP = "REDE HG - Sistema de Demandas"
NOME_CURTO = "HGhub"
EXECUTAVEL = "HGhub.exe"

# --------------------------------------------------------------------------- atualizacao
# "dono/repositorio" do GitHub onde os RELEASES publicos (instalador + manifesto assinado)
# ficam. Vazio = o atualizador fica desligado e avisa que nao esta configurado. Defina uma
# vez; as proximas versoes nao exigem nenhuma outra configuracao manual.
REPO_RELEASES = "H44TAKE/hghub-demandas"

# Chaves PUBLICAS Ed25519 (base64, 32 bytes) cujas assinaturas o app aceita no manifesto.
# A chave privada correspondente NUNCA esta no repositorio (cofre de segredos do GitHub +
# copia offline). Mais de uma chave permite trocar a chave sem deixar instalacoes de fora:
# publica-se uma versao assinada pela chave antiga que ja traz a nova nesta lista.
CHAVES_PUBLICAS_ATUALIZACAO = (
    "60uKVWEYAbPL+1aRXl7vadz38i/KHfanM+AQo1vR/BU=",
)
