; Windows-установщик поверх electron-builder win-unpacked.
; Сборка: ISCC /DMyAppVersion=... /DSourceDir=...\win-unpacked /DOutputDir=... setup.iss

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef SourceDir
  #error Pass /DSourceDir=<win-unpacked> to ISCC
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

#define MyAppName "AI Smart Tender"
#define MyAppExeName "start.exe"

[Setup]
AppId={{8F3C1A6E-9B24-4D71-A8E2-5C7D91B0E4F3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=AI Smart Tender
AppCopyright=AI Smart Tender
DefaultDirName={localappdata}\Programs\AI Smart Tender
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=AI-Smart-Tender-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
CloseApplications=yes
RestartApplications=no
UsedUserAreasWarning=no
SetupLogging=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Types]
Name: "full"; Description: "Полная установка"
Name: "compact"; Description: "Чат и инструменты"
Name: "custom"; Description: "Выборочная установка"; Flags: iscustom

[Components]
Name: "core"; Description: "Оболочка и чат (Open WebUI)"; Types: full compact custom; Flags: fixed
Name: "tools"; Description: "Инструменты (документы, Excel)"; Types: full compact custom
Name: "agent"; Description: "Поиск закупок (Tender Agent и Chromium)"; Types: full custom
Name: "dashboard"; Description: "Дашборд закупок"; Types: full custom
Name: "models"; Description: "Многоязычная модель знаний"; Types: full custom

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Дополнительно:"; Flags: checkedonce

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; \
  Excludes: "resources\ToolsServer\*,resources\TenderAgent\*,resources\Dashboard\*,resources\models\*"; \
  Components: core
Source: "{#SourceDir}\resources\ToolsServer\*"; DestDir: "{app}\resources\ToolsServer"; \
  Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist; Components: tools
Source: "{#SourceDir}\resources\TenderAgent\*"; DestDir: "{app}\resources\TenderAgent"; \
  Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist; Components: agent
Source: "{#SourceDir}\resources\Dashboard\*"; DestDir: "{app}\resources\Dashboard"; \
  Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist; Components: dashboard
Source: "{#SourceDir}\resources\models\*"; DestDir: "{app}\resources\models"; \
  Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist; Components: models
Source: "write-services.ps1"; Flags: dontcopy

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Params: String;
  Powershell: String;
begin
  if CurStep = ssPostInstall then
  begin
    ExtractTemporaryFile('write-services.ps1');
    Powershell := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
    Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\write-services.ps1') +
      '" -Path "' + ExpandConstant('{app}\resources\services.json') + '"';
    if WizardIsComponentSelected('tools') then
      Params := Params + ' -Tools';
    if WizardIsComponentSelected('agent') then
      Params := Params + ' -Agent';
    if WizardIsComponentSelected('dashboard') then
      Params := Params + ' -Dashboard';
    if not Exec(Powershell, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
      MsgBox('Не удалось записать список модулей (код ' + IntToStr(ResultCode) +
        '). Программа может не запустить выбранные компоненты.', mbError, MB_OK);
  end;
end;
