; Inno Setup 6 script for the Powder installer. Built by packaging\build.ps1:
;   ISCC /DAppVersion=0.1.0 /DNumericVersion=0.1.0 /DSourceDir=<dist\Powder> /DOutputDir=<dist> powder.iss
; Per-user install (no admin rights). Program files go to %LOCALAPPDATA%\Programs\Powder; everything Powder
; generates (progress, game-data pack, local CA, logs) lives in %LOCALAPPDATA%\Powder and is only removed
; when the user says so during uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef NumericVersion
  #define NumericVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Powder"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
AppId={{6C2B1E57-0F4A-4C8E-9A43-2D5F7B1E9C30}
AppName=Powder
AppVersion={#AppVersion}
AppVerName=Powder {#AppVersion}
AppPublisher=Powder
AppComments=Keeps Steep playable without Ubisoft's servers
VersionInfoVersion={#NumericVersion}
DefaultDirName={localappdata}\Programs\Powder
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#SourceDir}\LICENSE.txt
OutputDir={#OutputDir}
OutputBaseFilename=Powder-Setup-v{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Powder.exe
UninstallDisplayName=Powder
; the launcher holds this mutex while open: setup and uninstall ask to close it first
AppMutex=PowderLauncher

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Powder"; Filename: "{app}\Powder.exe"
Name: "{autodesktop}\Powder"; Filename: "{app}\Powder.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Powder.exe"; Description: "Start Powder"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; written next to the TLS helper while playing
Type: files; Name: "{app}\_internal\native\hook_guard.log"
Type: dirifempty; Name: "{app}"

[Code]
function DataDir(): String;
begin
  Result := ExpandConstant('{localappdata}\Powder');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    { Undo what the launcher set up on this PC (hosts entries, the local CA) while Powder.exe still
      exists. Restore system explains every Windows prompt before it appears and can be skipped. }
    if (not UninstallSilent) and FileExists(ExpandConstant('{app}\Powder.exe')) then
    begin
      MsgBox('Powder will now open "Restore system" to undo what it set up on this PC: the hosts-file ' +
             'entries and its certificate. Follow the steps in that window, then close it to finish ' +
             'uninstalling.', mbInformation, MB_OK);
      Exec(ExpandConstant('{app}\Powder.exe'), '--restore --window', '', SW_SHOW, ewWaitUntilTerminated,
           ResultCode);
    end;
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    if DirExists(DataDir()) and (not UninstallSilent) then
      if MsgBox('Keep your Powder data?' + #13#10#13#10 +
                'It holds your progress (level, credits, items), the game-data pack and Powder''s ' +
                'settings, in ' + DataDir() + '.' + #13#10#13#10 +
                'Yes: keep it (a later install continues where you left off).' + #13#10 +
                'No: delete it.', mbConfirmation, MB_YESNO or MB_DEFBUTTON1) = IDNO then
        DelTree(DataDir(), True, True, True);
  end;
end;
