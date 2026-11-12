# -*- mode: python ; coding: utf-8 -*-
"""HGhub.spec — build de DIRETÓRIO (--onedir, nunca --onefile) do Sistema de
Demandas REDE HG (app.py -> pywebview + Flask).

Arquitetura de caminhos deste app (ver backend/utils.py):
    BASE_DIR = os.path.dirname(sys.executable)   # quando empacotado (sys.frozen)
`create_app()` (backend/__init__.py) monta `template_folder`/`static_folder`
a partir desse BASE_DIR - e `ARQUIVO_CREDENCIAIS_SHEETS`, `config.json`,
`usuarios.json`, `dados_sistema_hg.json`, `hghub.log` seguem exatamente a
mesma regra. Ou seja: TUDO isso já espera viver ao lado do .exe, nunca dentro
de `sys._MEIPASS` - esse é o motivo dos dois ajustes abaixo, que fogem do
"onedir padrão" do PyInstaller 6+:

1. `contents_directory="."` no EXE() - o PyInstaller 6 passou a colocar tudo
   (exceto o .exe) dentro de uma subpasta `_internal/` por padrão. Com
   `BASE_DIR` apontando para a pasta do .exe (não para `_internal/`), isso
   faria `templates/`/`static/` "sumirem" do ponto de vista do app. Setar
   `contents_directory="."` restaura o layout achatado (pré-6.0): tudo direto
   em `dist/HGhub/`, exatamente onde `BASE_DIR` já espera encontrar.
2. `credenciais.json` e `config.json` NÃO entram em `datas=` de propósito -
   são segredos/configuração do DEPLOYMENT (rotacionáveis sem rebuild, cada
   instalação pode ter os seus), não recursos estáticos do app. Ficam de fora
   do pacote e são copiados para `dist/HGhub/` como um passo separado do
   build (nunca embutidos no `.exe` em si) - ver o passo de build no chat.
"""

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    collect_submodules("googleapiclient")
    + collect_submodules("google.oauth2")
    + collect_submodules("google.auth")
    + collect_submodules("google_auth_httplib2")
    + [
        "pandas",
        "openpyxl",
        # pywebview escolhe o backend em runtime (importlib) - a análise
        # estática do PyInstaller nunca enxerga essas 3 importações sozinha;
        # sem isto, o .exe abre e fecha na hora de criar a janela.
        "webview.platforms.edgechromium",
        "webview.platforms.mshtml",
        "webview.platforms.winforms",
        "cryptography.hazmat.primitives.asymmetric.ed25519",
        "backend.atualizador",
        "backend.rotas_atualizacao",
        "backend.versao",
        "clr_loader",
        "clr",
    ]
)

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("templates", "templates"),
        ("static", "static"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HGhub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # janela nativa do pywebview - sem console atrás, igual ao app.py hoje
    icon="arquivo.ico",
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="HGhub",
)
