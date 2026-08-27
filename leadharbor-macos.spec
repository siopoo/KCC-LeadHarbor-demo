# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ["desktop.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("templates", "templates"),
        ("static", "static"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KCC-LeadHarbor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="KCC-LeadHarbor",
)

app = BUNDLE(
    coll,
    name="KCC-LeadHarbor.app",
    bundle_identifier="com.kcc.leadharbor",
    info_plist={
        "CFBundleName": "KCC LeadHarbor",
        "CFBundleDisplayName": "KCC LeadHarbor",
        "CFBundleShortVersionString": "0.1.0",
        "NSHighResolutionCapable": True,
    },
)
