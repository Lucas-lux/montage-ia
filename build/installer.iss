; Installeur de Montage IA — produit MontageIA-Setup.exe.
;
; Appelé par build\build.py, qui passe le dossier à empaqueter :
;     ISCC /DAppSource=...\dist\MontageIA /O...\dist  installer.iss
;
; Installation SANS droits administrateur, dans %LOCALAPPDATA%\Programs :
; l'application doit pouvoir écrire à côté d'elle (cache du modèle) et
; l'utilisateur ne doit pas avoir à valider une élévation pour monter une
; vidéo. Compression rapide : le dossier pèse plus de 2 Go, une compression
; maximale ferait durer la fabrication des heures pour quelques pourcents.

#ifndef AppSource
  #define AppSource "..\dist\MontageIA"
#endif

#define AppName "Montage IA"
#define AppVersion "0.1.0"
#define AppExe "MontageIA.exe"

[Setup]
AppId={{7E2C4A18-9F3B-4D6E-8C21-1B5A9D0E7F44}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Montage IA
DefaultDirName={localappdata}\Programs\MontageIA
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=MontageIA-Setup
Compression=lzma2/fast
SolidCompression=no
LZMANumBlockThreads=4
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExe}
DisableDirPage=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; \
  GroupDescription: "Raccourcis :"

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; \
  Flags: recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
; Une mise à jour repart d'un dossier propre : d'anciennes DLL laissées à côté
; des nouvelles (cuDNN de versions différentes) font planter la transcription.
; Les projets de l'utilisateur sont ailleurs (%LOCALAPPDATA%\MontageIA).
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\cuda"
Type: filesandordirs; Name: "{app}\ffmpeg"
Type: filesandordirs; Name: "{app}\models\translate"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Désinstaller {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Lancer {#AppName}"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Le dossier de travail (projets, aperçus) reste : on ne supprime pas le
; travail de l'utilisateur en désinstallant l'outil.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"
