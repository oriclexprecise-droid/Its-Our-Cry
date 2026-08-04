#define MyAppName "It's Our Cry!!!!!"
#define MyAppVersion "2.1.2"
#define MyAppExeName "ItsOurCry.exe"

[Setup]
AppId={{2F7B4C1E-3A9D-4E6B-8C05-1D2E3F4A5B6C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=ORiCale
AppPublisherURL=https://space.bilibili.com/3493294730381924
DefaultDirName={localappdata}\Programs\It's Our Cry
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\release
OutputBaseFilename=It-sOurCry-Setup-Inno
SetupIconFile=app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional tasks:"; Flags: checkedonce

[Files]
Source: "installer\inno_payload\app\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "installer\inno_payload\app\config.yaml"; DestDir: "{app}"; Flags: onlyifdoesntexist

[Icons]
Name: "{autoprograms}\{#MyAppName}\{#MyAppName}.lnk"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Comment: "{#MyAppName} 配音工作台"
Name: "{autodesktop}\{#MyAppName}.lnk"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Comment: "{#MyAppName} 配音工作台"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: dirifempty; Name: "{app}"
