#define AppName "ClassInEDBMVP"
#define AppDisplayName "ClassIn EDB"
#define AppVersion "0.1.0"
#define SourceDir "..\..\dist\ClassInEDBMVP"
#define OutputDir "..\..\dist"

[Setup]
AppId={{8F3C4B60-7E9A-4B0F-A7F1-EDB0D1E7C001}
AppName={#AppDisplayName}
AppVersion={#AppVersion}
AppPublisher=ClassIn EDB
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppDisplayName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename={#AppName}-Setup
SetupIconFile=..\..\assets\app_icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppName}.exe

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppDisplayName}"; Filename: "{app}\{#AppName}.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppDisplayName}"; Filename: "{app}\{#AppName}.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "{cm:LaunchProgram,{#StringChange(AppDisplayName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
