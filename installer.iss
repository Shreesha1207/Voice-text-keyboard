; Xvoice Inno Setup Installer Script
; Produces XVoiceSetup.exe from the single-file PyInstaller output.

#define MyAppName "Xvoice"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "Shreesha M Rao"
#define MyAppURL "https://happy-tiny-glance.lovable.app/"
#define MyAppExeName "xvoice.exe"

[Setup]
AppId={{5D1C7383-6B2A-44E9-B27E-6BD215D4B9E3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=XVoiceSetup
SolidCompression=yes
WizardStyle=modern
; Allow upgrading over existing installation without uninstall
UsePreviousAppDir=yes
CloseApplications=force
CloseApplicationsFilter={#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "Start Xvoice automatically when Windows starts"; GroupDescription: "Startup:"; Flags: checked

[Files]
; Single-file exe produced by PyInstaller onefile mode
Source: "dist\xvoice.exe"; DestDir: "{app}"; Flags: ignoreversion
; Bundle ffmpeg alongside the exe so normalize_audio() can find it
Source: "ffmpeg.exe"; DestDir: "{app}"; Flags: ignoreversion; Check: FileExists(ExpandConstant('{src}\ffmpeg.exe'))

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Auto-start entry (only if user checked the "autostart" task)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Xvoice"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Kill running instance before uninstall so files aren't locked
Filename: "taskkill.exe"; Parameters: "/F /IM {#MyAppExeName}"; Flags: runhidden; RunOnceId: "KillXvoice"

[UninstallDelete]
; Clean up log and config files on full uninstall
Type: filesandsubdirs; Name: "{localappdata}\Xvoice"

[Code]
// Kill any running Xvoice process before installing (upgrade scenario)
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssInstall then
  begin
    Exec('taskkill.exe', '/F /IM {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    // Brief pause to let the port and file handles release
    Sleep(1500);
  end;
end;
