; Built on Windows after packaging/windows/build.ps1 has filled .\payload.
; Compile with Inno Setup 6: ISCC.exe skitdesk.iss

#define MyAppName "Kira vs L"
#define MyAppVersion "1.0.6"

[Setup]
AppId={{8F3E2A11-6C4B-4D0E-9A71-0B1C2D3E4F50}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\Kira vs L
DefaultGroupName={#MyAppName}
OutputDir=output
OutputBaseFilename=Kira vs L-Setup
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
DisableProgramGroupPage=yes

[Files]
Source: "payload\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\Kira vs L"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m app.desktop"; WorkingDir: "{app}"
Name: "{autodesktop}\Kira vs L"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m app.desktop"; WorkingDir: "{app}"

[InstallDelete]
; 1.0.2 left a static ffmpeg.exe here. TorchCodec needs the shared build in {app}\ffmpeg.
Type: files; Name: "{app}\ffmpeg.exe"
Type: files; Name: "{app}\ffprobe.exe"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
{ The runtime GUID is built in pieces so a doubled brace cannot change the key name. }
function WebView2Guid: String;
begin
  Result := '{' + 'F3017226-FE2A-4295-8BDF-00C3A9A7E4C5' + '}';
end;

function WebView2Key(const Prefix: String): String;
begin
  Result := Prefix + '\Microsoft\EdgeUpdate\Clients\' + WebView2Guid;
end;

function WebView2VersionUseful(const Version: String): Boolean;
begin
  Result := (Version <> '') and (Version <> '0.0.0.0');
end;

function WebView2RegistryAt(RootKey: Integer; const SubKey: String): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(RootKey, SubKey, 'pv', Version) and WebView2VersionUseful(Version);
end;

function EdgeWebViewExe(const ApplicationDir: String): Boolean;
var
  FindRec: TFindRec;
  Folder: String;
begin
  Result := False;
  Folder := ApplicationDir;
  if (Folder = '') or (not DirExists(Folder)) then
    Exit;
  if FileExists(AddBackslash(Folder) + 'msedgewebview2.exe') then
  begin
    Result := True;
    Exit;
  end;
  if not FindFirst(AddBackslash(Folder) + '*', FindRec) then
    Exit;
  try
    repeat
      if ((FindRec.Attributes and $10) <> 0) and
         (FindRec.Name <> '.') and (FindRec.Name <> '..') and
         FileExists(AddBackslash(Folder) + FindRec.Name + '\msedgewebview2.exe') then
      begin
        Result := True;
        Exit;
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

function WebView2Installed: Boolean;
var
  Location: String;
begin
  { 64-bit install mode reads HKLM as the 64-bit view. This runtime is registered in the 32-bit view. }
  if WebView2RegistryAt(HKLM32, WebView2Key('SOFTWARE')) then
  begin
    Result := True;
    Exit;
  end;
  if WebView2RegistryAt(HKLM32, WebView2Key('SOFTWARE\WOW6432Node')) then
  begin
    Result := True;
    Exit;
  end;
  if WebView2RegistryAt(HKLM64, WebView2Key('SOFTWARE\WOW6432Node')) then
  begin
    Result := True;
    Exit;
  end;
  if WebView2RegistryAt(HKLM64, WebView2Key('SOFTWARE')) then
  begin
    Result := True;
    Exit;
  end;
  if WebView2RegistryAt(HKCU32, WebView2Key('Software')) or
     WebView2RegistryAt(HKCU64, WebView2Key('Software')) then
  begin
    Result := True;
    Exit;
  end;

  Location := '';
  if not RegQueryStringValue(HKLM32, WebView2Key('SOFTWARE'), 'location', Location) then
    if not RegQueryStringValue(HKLM64, WebView2Key('SOFTWARE\WOW6432Node'), 'location', Location) then
      RegQueryStringValue(HKCU32, WebView2Key('Software'), 'location', Location);
  if EdgeWebViewExe(Location) then
  begin
    Result := True;
    Exit;
  end;

  Result :=
    EdgeWebViewExe(ExpandConstant('{pf32}\Microsoft\EdgeWebView\Application')) or
    EdgeWebViewExe(ExpandConstant('{pf64}\Microsoft\EdgeWebView\Application')) or
    EdgeWebViewExe(ExpandConstant('{localappdata}\Microsoft\EdgeWebView\Application'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  if WebView2Installed then
    Exit;

  ExtractTemporaryFile('MicrosoftEdgeWebView2RuntimeInstallerX64.exe');
  if not Exec(
    ExpandConstant('{tmp}\MicrosoftEdgeWebView2RuntimeInstallerX64.exe'),
    '/silent /install',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) then
  begin
    Result := 'Could not start the Microsoft Edge WebView2 Runtime installer.';
    Exit;
  end;

  { 0 = success. 3010 = success, reboot required. An already-installed runtime can return another code. }
  if ResultCode = 3010 then
    NeedsRestart := True;
  if WebView2Installed then
    Exit;
  if (ResultCode <> 0) and (ResultCode <> 3010) then
    Result := 'The Microsoft Edge WebView2 Runtime did not install. Exit code: ' + IntToStr(ResultCode) + '.'
  else
    Result := 'The Microsoft Edge WebView2 Runtime installer finished, but the runtime is still missing.';
end;
