; Inno Setup: настоящий Setup.exe вокруг AI_Assistant.exe
#define AppVer GetEnv("APP_VERSION")
[Setup]
AppName=AI Ассистент
AppVersion={#AppVer}
DefaultDirName={autopf}\AI Assistant
DefaultGroupName=AI Ассистент
OutputDir=dist
OutputBaseFilename=AI_Assistant_Setup
SetupIconFile=assets\app.ico
UninstallDisplayIcon={app}\AI_Assistant.exe
PrivilegesRequired=lowest
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
WizardImageFile=assets\wizard_big.bmp
WizardSmallImageFile=assets\wizard_small.bmp
DisableWelcomePage=no
[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
[Tasks]
Name: "desktopicon"; Description: "Ярлык на рабочем столе"
Name: "autostart"; Description: "Запускать вместе с Windows (в трее)"; Flags: unchecked
[Files]
Source: "dist\AI_Assistant.exe"; DestDir: "{app}"
[Icons]
Name: "{autoprograms}\AI Ассистент"; Filename: "{app}\AI_Assistant.exe"
Name: "{autodesktop}\AI Ассистент"; Filename: "{app}\AI_Assistant.exe"; Tasks: desktopicon
Name: "{userstartup}\AI Ассистент"; Filename: "{app}\AI_Assistant.exe"; Parameters: "--background"; Tasks: autostart
[Run]
Filename: "{app}\AI_Assistant.exe"; Description: "Запустить AI Ассистент"; Flags: postinstall nowait skipifsilent
