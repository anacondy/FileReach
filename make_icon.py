"""
Generate platform icons from assets/icon.png.

  * assets/icon.ico  -> used by the Windows build (multi-size .ico)
  * assets/icon.icns -> used by the macOS build (best-effort; needs Pillow + macOS)

Run this once before `pyinstaller filereach.spec`. It is a no-op if icon.png
is missing (the build will simply have no icon, which is fine).
"""
import os
import sys
from PIL import Image

PNG = os.path.join("assets", "icon.png")
ICO = os.path.join("assets", "icon.ico")
ICNS = os.path.join("assets", "icon.icns")


def main():
    if not os.path.exists(PNG):
        print("make_icon: assets/icon.png not found — skipping icon generation.")
        return 0
    img = Image.open(PNG).convert("RGBA")

    # Windows .ico — multiple sizes embedded
    try:
        img.save(ICO, format="ICO",
                 sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        print("make_icon: wrote", ICO)
    except Exception as e:
        print("make_icon: .ico failed:", e)

    # macOS .icns (Pillow can write icns only from a 512x512+ RGBA image)
    try:
        big = img.resize((512, 512), Image.LANCZOS)
        big.save(ICNS, format="ICNS")
        print("make_icon: wrote", ICNS)
    except Exception as e:
        print("make_icon: .icns skipped:", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
