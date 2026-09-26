# PyInstaller spec of the Linux build: one directory holding the window's
# executable, the Qt libraries and the example profile. Run through
# packaging/build-linux.sh, from the project root.
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

analysis = Analysis(
    ["../provision_gui.py"],
    pathex=[".."],
    binaries=[],
    datas=[
        ("../node-profile.example.yaml", "."),
        # esptool's flasher stubs (JSON) and meshtastic's data files.
        *collect_data_files("esptool"),
        *collect_data_files("meshtastic"),
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(analysis.pure, analysis.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="meshtastic-provision",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
collect = COLLECT(exe, analysis.binaries, analysis.zipfiles, analysis.datas, strip=False, upx=False, name="meshtastic-provision")
