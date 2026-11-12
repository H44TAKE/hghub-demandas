; ============================================================================
; Instalador do HGhub (REDE HG - Sistema de Demandas) - Inno Setup 6
;
; Compilar (feito por tools/build.py e pelo GitHub Actions):
;   ISCC.exe /DMyAppVersion=1.0.0 /DSourceDir=..\dist\HGhub installer\hghub.iss
;
; Decisoes:
;  - Instalacao POR USUARIO em %LOCALAPPDATA%\Programs\HGhub (nao pede administrador). Assim o
;    atualizador consegue substituir os arquivos sem UAC.
;  - NENHUM dado do usuario mora na pasta de instalacao. Os dados do sistema ficam na pasta de rede
;    (Z:/Y:); logs, estado do atualizador, credenciais e copias dos instaladores ficam em
;    %LOCALAPPDATA%\HGhub. Atualizar ou desinstalar nunca apaga isso sem perguntar.
;  - credenciais.json NUNCA vai dentro do instalador (e uma chave privada). O assistente deixa o usuario
;    apontar o arquivo; ele e copiado para %LOCALAPPDATA%\HGhub.
;  - O instalador deixa uma copia de si mesmo em installers\HGhub-Setup-novo.exe; o app faz a rotacao
;    (atual -> anterior) na proxima abertura, o que permite "Voltar a versao anterior".
; ============================================================================

#define MyAppName "REDE HG - Sistema de Demandas"
#define MyAppShort "HGhub"
#define MyAppExe "HGhub.exe"
#define MyAppPublisher "REDE HG Transportadora"
#define MyAppURL "https://github.com/H44TAKE/hghub-demandas"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\HGhub"
#endif

[Setup]
AppId={{8E5B6C1A-4D2F-4B7E-9A31-6F0C2D8E7A54}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\{#MyAppShort}
DefaultGroupName=REDE HG
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
UsePreviousAppDir=yes
OutputDir=..\installer_output
OutputBaseFilename=HGhub-Setup-{#MyAppVersion}
SetupIconFile=..\arquivo.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} - instalador
VersionInfoProductName={#MyAppName}

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar um atalho na Área de Trabalho"; GroupDescription: "Atalhos:"

[Files]
; ignoreversion: permite voltar a uma versao anterior (o Inno, por padrao, nao troca um arquivo por um mais antigo)
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "credenciais.json,config.json,*.log,dados_sistema_hg.json,usuarios.json"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{srcexe}"; DestDir: "{localappdata}\{#MyAppShort}\installers"; DestName: "HGhub-Setup-novo.exe"; Flags: external ignoreversion

[Icons]
Name: "{autoprograms}\REDE HG\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
; instalacao interativa: oferece abrir ao final
Filename: "{app}\{#MyAppExe}"; Description: "Abrir o {#MyAppName}"; Flags: nowait postinstall skipifsilent
; atualizacao automatica (silenciosa, /RELAUNCH=1): reabre o programa sozinho
Filename: "{app}\{#MyAppExe}"; Flags: nowait; Check: DeveReabrir

[Code]
var
  PaginaCredenciais: TInputFileWizardPage;

{ Atualizacao automatica: o app responde ao navegador e so ENTAO se encerra (~1,5 s). Em modo silencioso o Inno
  nao espera um programa aberto (cancela), entao o proprio instalador espera ate 30 s o app liberar o mutex. }
function InitializeSetup: Boolean;
var
  i: Integer;
begin
  Result := True;
  if WizardSilent then
    for i := 1 to 60 do
    begin
      if not CheckForMutexes('HGhubAppMutex') then Break;
      Sleep(500);
    end;
end;

function DeveReabrir: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

function PastaLocal: String;
begin
  Result := ExpandConstant('{localappdata}\{#MyAppShort}');
end;

procedure InitializeWizard;
begin
  PaginaCredenciais := CreateInputFilePage(wpSelectDir,
    'Credenciais do Google (opcional)',
    'Necessárias apenas para o módulo Frota & Manutenção.',
    'Se você já tem o arquivo credenciais.json, selecione-o. Ele será guardado na sua pasta de usuário e NÃO é apagado nem substituído nas atualizações. Pode deixar em branco e configurar depois.');
  PaginaCredenciais.Add('Arquivo credenciais.json:', 'Arquivo JSON|*.json|Todos os arquivos|*.*', '.json');
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if PageID = PaginaCredenciais.ID then
    Result := FileExists(PastaLocal + '\credenciais.json');   { ja configurado: nao pergunta de novo }
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Destino, LegadoNoApp: String;
begin
  if CurStep = ssPostInstall then
  begin
    ForceDirectories(PastaLocal);
    Destino := PastaLocal + '\credenciais.json';
    { migracao: versoes antigas guardavam credenciais.json ao lado do programa }
    LegadoNoApp := ExpandConstant('{app}\credenciais.json');
    if (not FileExists(Destino)) and FileExists(LegadoNoApp) then
      CopyFile(LegadoNoApp, Destino, True);
    { arquivo escolhido no assistente }
    if (not WizardSilent) and (PaginaCredenciais.Values[0] <> '') and FileExists(PaginaCredenciais.Values[0]) then
      CopyFile(PaginaCredenciais.Values[0], Destino, False);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if DirExists(PastaLocal) and (not UninstallSilent) then
      if MsgBox('Deseja também apagar as configurações locais deste computador (credenciais do Google, logs e cópias de instaladores)?' + #13#10 + #13#10 +
                'Os dados do sistema (tarefas, usuários, fotos) ficam na pasta de rede e NÃO serão apagados.' + #13#10 +
                'Se for reinstalar depois, recomenda-se manter.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(PastaLocal, True, True, True);
  end;
end;
