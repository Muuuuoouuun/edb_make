"""Keep the PDF renderer and its native libraries inside desktop packages."""

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

binaries = collect_dynamic_libs("pymupdf")
hiddenimports = collect_submodules("pymupdf", filter=lambda name: not name.endswith((".__main__", "._build")))
