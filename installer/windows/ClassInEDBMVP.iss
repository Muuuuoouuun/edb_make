#ifndef AppName
  #define AppName "ClassInEDBMVP"
#endif
#ifndef AppDisplayName
  #define AppDisplayName "ClassIn EDB"
#endif
#ifndef AppPublisher
  #define AppPublisher "ClassIn EDB"
#endif
#ifndef AppVersion
  #define AppVersion "0.2.3"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\ClassInEDBMVP"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

[Setup]
AppId={{8F3C4B60-7E9A-4B0F-A7F1-EDB0D1E7C001}
AppName={#AppDisplayName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppDisplayName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename={#AppName}-Setup
SetupIconFile=..\..\assets\app_icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#AppName}.exe
UninstallDisplayName={#AppDisplayName}

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; PyInstaller's _internal directory is an immutable application payload. Remove
; it after Restart Manager closes the running app so deleted/renamed DLL and PYD
; files from an older version cannot survive an in-place upgrade. User state is
; stored under the Windows Documents known folder, outside {app}.
; Preserve legacy work folders at {app} too: they may contain user exports.
; Only the immutable _internal payload and named obsolete UI/build files below
; are removed. Never delete {app} as a whole during upgrades or uninstall.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\ui_prototype\app.js"
Type: filesandordirs; Name: "{app}\ui_prototype\prototype_data.js"
Type: filesandordirs; Name: "{app}\ui_prototype\generated_session.js"
Type: filesandordirs; Name: "{app}\ui_prototype\vendor\babel.min.js"
Type: filesandordirs; Name: "{app}\ui_prototype\vendor\babel.min.js.map"
Type: filesandordirs; Name: "{app}\scripts\build_frontend_bundle.mjs"
Type: filesandordirs; Name: "{app}\scripts\vendor"
Type: filesandordirs; Name: "{app}\scripts\verify_frontend_package.py"
Type: filesandordirs; Name: "{app}\scripts\verify_packaged_app.py"
Type: filesandordirs; Name: "{app}\assets\app_icon.svg"
Type: filesandordirs; Name: "{app}\assets\brand_mark.svg"
Type: filesandordirs; Name: "{app}\assets\app_icon.iconset"

[Icons]
Name: "{group}\{#AppDisplayName}"; Filename: "{app}\{#AppName}.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppDisplayName}"; Filename: "{app}\{#AppName}.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "{cm:LaunchProgram,{#StringChange(AppDisplayName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
