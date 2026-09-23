# PyInstaller spec for lilx:  pyinstaller packaging/lilx.spec   (run from the project root)
#
# Icons: logo.png (project root) is the source. packaging/icons/lilx.icns and lilx.ico
# are derived from it by scripts/build_icons.py. logo.png itself is bundled as data so
# the running app finds it through sys._MEIPASS (see lilx/branding.py).
# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 (provided by PyInstaller)
ICONS = ROOT / "packaging" / "icons"
ICON = {"darwin": ICONS / "lilx.icns", "win32": ICONS / "lilx.ico"}.get(sys.platform)

if sys.platform == "darwin":
    # PyInstaller picks the *last* entry of <framework>/Versions; a stray extra folder there
    # (seen once as an empty "Versions/Resources" in a venv) makes it drop QtWebEngineProcess.
    import PySide6

    for framework in (Path(PySide6.__file__).parent / "Qt" / "lib").glob("QtWebEngine*.framework"):
        extra = [p.name for p in (framework / "Versions").iterdir() if p.name not in ("A", "Current")]
        if extra:
            raise SystemExit(f"{framework}/Versions has unexpected entries {extra}; "
                             "reinstall PySide6-Addons (pip install --force-reinstall PySide6-Addons).")

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "lilx_main.py")],
    pathex=[str(ROOT)],
    datas=[
        (str(ROOT / "lilx" / "resources"), "lilx/resources"),
        (str(ROOT / "logo.png"), "."),
    ],
    hiddenimports=["PySide6.QtWebEngineWidgets", "PySide6.QtSvg"],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="lilx",
    console=False,
    icon=str(ICON) if ICON else None,  # .exe icon on Windows
)
coll = COLLECT(exe, a.binaries, a.datas, name="lilx")  # noqa: F821

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name="lilx.app",
        icon=str(ICON),  # Finder, Dock and Cmd+Tab before the app has started
        bundle_identifier="org.lilx.browser",
        info_plist={
            "CFBundleName": "lilx",
            "CFBundleDisplayName": "lilx",
            "NSHighResolutionCapable": True,
            # macOS asks the user for these devices only if the app says why (site permissions).
            "NSCameraUsageDescription": "Websites you allow can use the camera.",
            "NSMicrophoneUsageDescription": "Websites you allow can use the microphone.",
            "NSLocationUsageDescription": "Websites you allow can use your location.",
            "NSLocationWhenInUseUsageDescription": "Websites you allow can use your location.",
        },
    )
