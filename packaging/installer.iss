; Inno Setup script for the EchoPortal agent (Windows 10/11, x64).
;
;   pyinstaller --noconfirm packaging/agent.spec
;   iscc /DAppVersion=0.1.0 packaging\installer.iss
;
; Output: dist\installer\EchoPortal-Agent-Setup-<version>.exe

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

#define AppName "EchoPortal Agent"
#define AppPublisher "EchoPortal contributors"
#define AppURL "https://github.com/PartiallyFrozen/EchoPortal"
#define AppExeName "EchoPortalAgent.exe"

[Setup]
AppId={{8F2A6C41-5B9E-4D33-9C2A-ECHOPORTAL0001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
DefaultDirName={autopf}\EchoPortal
DefaultGroupName=EchoPortal
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist\installer
OutputBaseFilename=EchoPortal-Agent-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; The firewall rule needs elevation; everything else would run per-user.
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "Start EchoPortal Agent when I sign in"; GroupDescription: "Startup"
Name: "firewall"; Description: "Allow the Echo Spot to reach the agent on this network (private networks only)"; GroupDescription: "Network"

[Files]
Source: "..\dist\EchoPortalAgent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\packaging\config.example.json"; DestDir: "{app}\packaging"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; DestName: "README.md"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\EchoPortal Agent"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Agent settings (config.json)"; Filename: "notepad.exe"; Parameters: """{app}\config.json"""
Name: "{group}\Uninstall EchoPortal Agent"; Filename: "{uninstallexe}"
Name: "{userstartup}\EchoPortal Agent"; Filename: "{app}\{#AppExeName}"; Tasks: startup

[Run]
; Two rules, inbound only, scoped to private networks: the hub and the media server.
Filename: "{sys}\netsh.exe"; \
  Parameters: "advfirewall firewall add rule name=""EchoPortal agent (hub 8765)"" dir=in action=allow protocol=TCP localport=8765 profile=private program=""{app}\{#AppExeName}"""; \
  Flags: runhidden; StatusMsg: "Adding firewall rule for the hub..."; Tasks: firewall
Filename: "{sys}\netsh.exe"; \
  Parameters: "advfirewall firewall add rule name=""EchoPortal agent (media 8766)"" dir=in action=allow protocol=TCP localport=8766 profile=private program=""{app}\{#AppExeName}"""; \
  Flags: runhidden; StatusMsg: "Adding firewall rule for the media server..."; Tasks: firewall
Filename: "{app}\{#AppExeName}"; Description: "Start the agent now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""EchoPortal agent (hub 8765)"""; Flags: runhidden; RunOnceId: "DelHubRule"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""EchoPortal agent (media 8766)"""; Flags: runhidden; RunOnceId: "DelMediaRule"

[UninstallDelete]
; Transcoded clips and thumbnails the agent generated; config.json is left for the user.
Type: filesandordirs; Name: "{app}\media"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  Src, Dest: string;
begin
  // First install: give the user a config.json to edit instead of only an example.
  if CurStep = ssPostInstall then
  begin
    Src := ExpandConstant('{app}\packaging\config.example.json');
    Dest := ExpandConstant('{app}\config.json');
    if FileExists(Src) and not FileExists(Dest) then
      FileCopy(Src, Dest, False);
  end;
end;
