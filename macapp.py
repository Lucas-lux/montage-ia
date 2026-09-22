"""Montage IA sur macOS : une vraie application, sans fenêtre de terminal.

L'interface est dans le navigateur ; l'application, elle, vit dans le Dock et
dans la barre des menus :

  - clic sur l'icône du Dock (ou nouveau double-clic sur l'app) : rouvre
    l'interface dans le navigateur ;
  - menu de la barre des menus et du Dock : ouvrir, dossier des projets,
    journal, quitter ;
  - ⌘Q, « Quitter » du Dock : arrête le moteur proprement.

App Nap est désactivé tant que l'application tourne : sinon macOS ralentit le
moteur dès que le navigateur passe au premier plan (la transcription et
l'export tourneraient au ralenti).

Nécessite PyObjC (`pyobjc-framework-Cocoa`, dans requirements.txt pour macOS).
Sans lui, `app.py` fait tourner le moteur sans icône.
"""
from __future__ import annotations

import subprocess
import threading
import webbrowser

_KEEP: list = []            # objets Cocoa qui doivent vivre aussi longtemps que l'app


def available() -> bool:
    try:
        import AppKit  # noqa: F401
        import PyObjCTools.AppHelper  # noqa: F401
    except ImportError:
        return False
    return True


def run(server, url: str, work: str, log: str | None = None) -> None:
    """Lance le moteur dans un fil et la boucle Cocoa dans le fil principal."""
    from AppKit import (NSApplication, NSApplicationActivationPolicyRegular, NSImage, NSMenu, NSMenuItem,
                        NSStatusBar, NSVariableStatusItemLength)
    from Foundation import NSObject, NSProcessInfo
    from PyObjCTools import AppHelper

    def open_ui() -> None:
        webbrowser.open(url)

    class Delegate(NSObject):
        # -- NSApplicationDelegate
        def applicationShouldHandleReopen_hasVisibleWindows_(self, app, flag):
            open_ui()
            return True

        def applicationShouldTerminate_(self, app):
            server.should_exit = True
            return 1                      # NSTerminateNow

        def applicationDockMenu_(self, app):
            return _menu(self, dock=True)

        # -- actions des menus
        def openUI_(self, sender):
            open_ui()

        def openWork_(self, sender):
            subprocess.Popen(["open", work])

        def openLog_(self, sender):
            if log:
                subprocess.Popen(["open", "-R", log])

    def _menu(target, dock: bool = False):
        m = NSMenu.alloc().initWithTitle_("Montage IA")
        items = [("Ouvrir Montage IA", "openUI:", "o", target),
                 ("Dossier des projets", "openWork:", "", target)]
        if log and not dock:
            items.append(("Afficher le journal", "openLog:", "", target))
        for title, action, key, tgt in items:
            it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
            it.setTarget_(tgt)
            m.addItem_(it)
        if not dock:                      # le Dock a déjà son « Quitter »
            m.addItem_(NSMenuItem.separatorItem())
            q = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Quitter Montage IA", "terminate:", "q")
            q.setTarget_(app)
            m.addItem_(q)
        _KEEP.append(m)
        return m

    app = NSApplication.sharedApplication()
    delegate = Delegate.alloc().init()
    app.setDelegate_(delegate)
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)

    # menu principal : « Montage IA » avec ⌘O et ⌘Q
    main = NSMenu.alloc().init()
    head = NSMenuItem.alloc().init()
    head.setSubmenu_(_menu(delegate))
    main.addItem_(head)
    app.setMainMenu_(main)

    # icône dans la barre des menus
    item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
    img = None
    try:
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_("film", "Montage IA")
    except AttributeError:             # macOS < 11
        pass
    if img is not None:
        img.setTemplate_(True)
        item.button().setImage_(img)
    else:
        item.button().setTitle_("Montage IA")
    item.button().setToolTip_("Montage IA")
    item.setMenu_(_menu(delegate))

    # pas d'App Nap : le moteur garde toute sa vitesse en arrière-plan
    try:
        from Foundation import NSActivityUserInitiatedAllowingIdleSystemSleep as opts
    except ImportError:
        opts = 0x00FFFFFF & ~(1 << 20)
    activity = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(opts, "Moteur de Montage IA")
    _KEEP.extend([delegate, main, head, item, activity])

    # le moteur dans un fil ; s'il s'arrête, l'application aussi
    engine = threading.Thread(target=server.run, name="moteur", daemon=True)
    engine.start()

    def watch() -> None:
        engine.join()
        AppHelper.callAfter(app.terminate_, None)
    threading.Thread(target=watch, daemon=True).start()

    AppHelper.runEventLoop(installInterrupt=True)


def alert(title: str, message: str) -> None:
    """Boîte d'alerte native (erreur au lancement, sans terminal pour la lire)."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')
    try:
        subprocess.run(["osascript", "-e", f'display alert "{esc(title)}" message "{esc(message)[:900]}" as critical'],
                       timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        pass
