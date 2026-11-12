# Como gerar e publicar uma versão

## Uma vez só (já feito neste repositório)

1. **Chave de assinatura das atualizações**: `python tools/atualizacao.py gerar-chaves --saida ~/.hghub-signing`.
   A chave **pública** impressa vai em `backend/versao.py` (`CHAVES_PUBLICAS_ATUALIZACAO`). A **privada**
   (`update_ed25519_private.pem`) fica fora do repositório. **Guarde uma cópia offline**: sem ela não dá para publicar
   atualizações que os PCs aceitem (nesse caso só reinstalando manualmente uma versão com chave nova).
2. **Segredo no GitHub** (`Settings → Secrets and variables → Actions → New repository secret`) ou pela CLI:
   `gh secret set UPDATE_SIGNING_KEY < ~/.hghub-signing/update_ed25519_private.pem`
3. `REPO_RELEASES = "H44TAKE/hghub-demandas"` em `backend/versao.py`.

## A cada versão

1. Edite `backend/versao.py` → `__version__ = "1.0.1"` (SemVer: PATCH correção, MINOR funcionalidade, MAJOR ruptura).
2. Rode os testes: `python -m pytest tests`.
3. `git commit -am "release 1.0.1"` e `git push origin main`.
4. `git tag v1.0.1 && git push origin v1.0.1`.
5. O workflow **Release** (`.github/workflows/release.yml`) roda: testes → confere que a tag = `versao.py` → compila →
   gera `HGhub-Setup-1.0.1.exe`, `update-manifest.json`, `update-manifest.json.sig`, `SHA256SUMS.txt` → **assina e
   auto-confere** → cria o release. **Se algum teste falhar, nada é publicado.**
6. Os PCs com o app instalado passam a ver a versão nova em até 6 h (ou em *Atualizações → Verificar agora*).

> Só tags `vX.Y.Z` publicam. Commits na `main` rodam apenas o CI (testes), nunca distribuem nada.

## Compilar localmente

```powershell
python tools/build.py            # PyInstaller + Inno Setup  ->  installer_output\HGhub-Setup-X.Y.Z.exe
python tools/build.py --so-exe   # só o executável (dist\HGhub)
```
Requer Inno Setup 6 (`winget install JRSoftware.InnoSetup`). O build **aborta** se algum segredo/dado entrar no pacote.

## Publicar manualmente (sem Actions)

```powershell
cd installer_output
python ..\tools\atualizacao.py manifesto --versao 1.0.1 --instalador HGhub-Setup-1.0.1.exe --repo H44TAKE/hghub-demandas --notas ..\notas.md
python ..\tools\atualizacao.py assinar --manifesto update-manifest.json --chave $HOME\.hghub-signing\update_ed25519_private.pem
gh release create v1.0.1 HGhub-Setup-1.0.1.exe update-manifest.json update-manifest.json.sig --title "HGhub 1.0.1" --latest
```

## Recuperar / retirar uma versão ruim

- **Retirar**: `gh release delete v1.0.1 --yes --cleanup-tag`; o "último release" volta a ser o anterior (os PCs
  que já atualizaram continuam nela; publique uma versão **maior** com a correção — o app nunca faz downgrade sozinho).
- **No PC**: *Atualizações → Voltar à versão anterior*.

## Trocar a chave de assinatura (rotação)

1. Gere o novo par; coloque a nova pública **além** da antiga em `CHAVES_PUBLICAS_ATUALIZACAO`.
2. Publique uma versão assinada com a chave **antiga** (que já traz as duas públicas).
3. Depois que todos atualizaram, troque o segredo `UPDATE_SIGNING_KEY` pela nova e remova a pública antiga.
