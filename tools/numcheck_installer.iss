; Inno Setup script for the one click Windows installer.
; Build the portable app first (tools/build_windows.py), extract it, then run:
;   ISCC.exe /DAppDir="C:\path\to\NumCheck" /DOutDir="dist" tools\numcheck_installer.iss
; The installer needs no administrator rights: it installs for the current user,
; adds Desktop and Start menu shortcuts, and registers an uninstaller.

#ifndef AppDir
  #error Pass /DAppDir= with the extracted NumCheck folder (the one containing NumCheck.exe).
#endif
#ifndef OutDir
  #define OutDir "dist"
#endif
#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif

[Setup]
AppId={{7D3C2A4E-5B1F-4C8E-9A61-2F0B8E4D9C17}
AppName=NumCheck
AppVersion={#AppVersion}
AppVerName=NumCheck {#AppVersion}
AppPublisher=NumCheck
AppPublisherURL=https://github.com/wxgmin/numcheck
AppSupportURL=https://github.com/wxgmin/numcheck/issues
DefaultDirName={localappdata}\Programs\NumCheck
DefaultGroupName=NumCheck
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutDir}
OutputBaseFilename=NumCheck-Setup
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
WizardStyle=modern
UninstallDisplayIcon={app}\NumCheck.exe
UninstallDisplayName=NumCheck
LicenseFile={#AppDir}\LICENSE
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a Desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#AppDir}\*"; DestDir: "{app}"; Excludes: "Start Here.txt"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\NumCheck"; Filename: "{app}\NumCheck.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\NumCheck"; Filename: "{app}\NumCheck.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\NumCheck.exe"; Description: "Open NumCheck now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
