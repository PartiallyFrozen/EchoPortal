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
; Per-user install: %LocalAppData%\Programs is writable, so the agent can create agent.log,
; media/, and the weather library without administrator rights. A copy under Program Files
; cannot, and exits immediately.
DefaultDirName={userpf}\EchoPortal
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
; No elevation for the install itself. The firewall rule is added separately, with a single
; UAC prompt, from [Code]; if that is declined the install still succeeds and the user can
; run allow-firewall.cmd later.
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "Start EchoPortal Agent when I sign in"; GroupDescription: "Startup"
Name: "firewall"; Description: "Allow the Echo Spot to reach the agent on this network (private networks only; asks for administrator once)"; GroupDescription: "Network"

[Files]
Source: "..\dist\EchoPortalAgent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\packaging\media\*"; DestDir: "{app}\media"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\packaging\config.example.json"; DestDir: "{app}\packaging"; Flags: ignoreversion
Source: "..\packaging\allow-firewall.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\packaging\allow-firewall.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\packaging\status.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\packaging\status.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\packaging\doctor.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\packaging\doctor.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; DestName: "README.md"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\EchoPortal Agent"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Agent settings (config.json)"; Filename: "notepad.exe"; Parameters: """{app}\config.json"""
Name: "{group}\Allow through firewall (run as administrator)"; Filename: "{app}\allow-firewall.cmd"
Name: "{group}\Status (is it working?)"; Filename: "{app}\status.cmd"
Name: "{group}\Doctor (check the install)"; Filename: "{app}\doctor.cmd"
Name: "{group}\Uninstall EchoPortal Agent"; Filename: "{uninstallexe}"
Name: "{userstartup}\EchoPortal Agent"; Filename: "{app}\{#AppExeName}"; Tasks: startup

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Start the agent now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Transcoded clips and thumbnails the agent generated; config.json is left for the user.
Type: filesandordirs; Name: "{app}\media"

[Code]
const
  HubRule = 'EchoPortal agent (hub 8765)';
  MediaRule = 'EchoPortal agent (media 8766)';
  HexDigits = '0123456789ABCDEF';
  TokenBytes = 32;
  ProxRsaFull = 1;
  CryptVerifyContext = $F0000000;

{ Windows CSPRNG: no PowerShell dependency and no static token in the shipped example. }
function CryptAcquireContextW(var Prov: THandle; Container, Provider: String;
  ProvType, Flags: DWORD): BOOL;
external 'CryptAcquireContextW@advapi32.dll stdcall';

function CryptGenRandom(Prov: THandle; Len: DWORD; var Buffer: Byte): BOOL;
external 'CryptGenRandom@advapi32.dll stdcall';

function CryptReleaseContext(Prov: THandle; Flags: DWORD): BOOL;
external 'CryptReleaseContext@advapi32.dll stdcall';

function HexByte(B: Byte): String;
begin
  Result := Copy(HexDigits, (B shr 4) + 1, 1) + Copy(HexDigits, (B and $0F) + 1, 1);
end;

function GenerateInstallToken(): String;
var
  Prov: THandle;
  B: Byte;
  I: Integer;
begin
  Result := '';
  Prov := 0;
  if not CryptAcquireContextW(Prov, '', '', ProxRsaFull, CryptVerifyContext) then
  begin
    Log('Token generation: CryptAcquireContextW failed');
    Exit;
  end;
  try
    for I := 0 to TokenBytes - 1 do
    begin
      if not CryptGenRandom(Prov, 1, B) then
      begin
        Log('Token generation: CryptGenRandom failed');
        Result := '';
        Exit;
      end;
      Result := Result + HexByte(B);
    end;
  finally
    CryptReleaseContext(Prov, 0);
  end;
end;

function WriteConfigWithToken(const Src, Dest: String): Boolean;
var
  Raw: AnsiString;
  Text: String;
  Token: String;
  Changed: Integer;
begin
  Result := False;
  Token := GenerateInstallToken();
  if Length(Token) <> (TokenBytes * 2) then
  begin
    Log('Token generation failed; refusing to create an unauthenticated config.json');
    Exit;
  end;
  if not LoadStringFromFile(Src, Raw) then
  begin
    Log('Could not read config.example.json');
    Exit;
  end;
  Text := Utf8Decode(Raw);
  if Length(Text) > 0 then
    if Ord(Text[1]) = $FEFF then
      Delete(Text, 1, 1);
  Changed := StringChangeEx(Text, '"api_token": ""', '"api_token": "' + Token + '"', True);
  if Changed <> 1 then
  begin
    Log('config.example.json did not contain exactly one empty api_token; refusing to create config.json');
    Exit;
  end;
  if not SaveStringToFile(Dest, Utf8Encode(Text), False) then
  begin
    Log('Could not write config.json');
    Exit;
  end;
  Result := True;
end;

function FirewallRuleExists(const Name: string): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{sys}\netsh.exe'),
    'advfirewall firewall show rule name="' + Name + '"', '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode)
    and (ResultCode = 0);
end;

{ Port rules rather than program-scoped ones: they keep working if the install folder moves. }
procedure RunElevatedNetsh(const Params: string);
var
  ResultCode: Integer;
begin
  ShellExec('runas', ExpandConstant('{cmd}'), '/c ' + Params, '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode);
end;

procedure AddFirewallRules();
begin
  RunElevatedNetsh(
    'netsh advfirewall firewall delete rule name="' + HubRule + '" >nul 2>&1 & ' +
    'netsh advfirewall firewall delete rule name="' + MediaRule + '" >nul 2>&1 & ' +
    'netsh advfirewall firewall add rule name="' + HubRule + '" dir=in action=allow protocol=TCP localport=8765 profile=private & ' +
    'netsh advfirewall firewall add rule name="' + MediaRule + '" dir=in action=allow protocol=TCP localport=8766 profile=private');
end;

procedure RemoveFirewallRules();
var
  ResultCode: Integer;
begin
  { Best effort, deliberately not elevated: an uninstall should not raise a UAC prompt.
    Leftover port rules are inert once nothing listens, and the next install replaces them. }
  Exec(ExpandConstant('{sys}\netsh.exe'),
    'advfirewall firewall delete rule name="' + HubRule + '"', '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\netsh.exe'),
    'advfirewall firewall delete rule name="' + MediaRule + '"', '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Src, Dest: string;
begin
  if CurStep = ssPostInstall then
  begin
    // First install: create a config.json with a unique token instead of copying an
    // example that would start unauthenticated. Never overwrite an existing config.
    Src := ExpandConstant('{app}\packaging\config.example.json');
    Dest := ExpandConstant('{app}\config.json');
    if FileExists(Src) and not FileExists(Dest) then
    begin
      if not WriteConfigWithToken(Src, Dest) then
        RaiseException('Could not create a secured config.json with a generated api_token. ' +
          'The install cannot continue with an unauthenticated agent.');
      Log('Created config.json with a generated per-install api_token');
    end;

    if WizardIsTaskSelected('startup') then
      // An older tray build created a .vbs; leaving it alongside the installer's .lnk
      // starts two tray processes that fight over ports 8765/8766 after an upgrade.
      DeleteFile(ExpandConstant('{userstartup}\EchoPortal Agent.vbs'));

    if WizardIsTaskSelected('firewall') then
    begin
      AddFirewallRules();
      if not FirewallRuleExists(HubRule) then
        MsgBox('The firewall rule was not added (the administrator prompt may have been declined).' + #13#10 +
               'Run "Allow through firewall" from the Start menu, or the Spot cannot connect.',
               mbInformation, MB_OK);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveFirewallRules();
end;
