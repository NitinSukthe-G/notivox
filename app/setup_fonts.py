"""
setup_fonts.py - downloads the fonts the PDF needs (run once).

Why? A PDF must carry its own fonts, and normal fonts have no Hindi/Tamil/... letters.
Google's free "Noto" fonts cover every Indian script. We download a Regular and a Bold
file for English and for each Indian script into  app/fonts/.

Run:  python -m app.setup_fonts
(This needs internet, but NOT your API keys.)
"""
from pathlib import Path

import httpx

FONTS_DIR = Path(__file__).resolve().parent / "fonts"
BASE = "https://github.com/notofonts/notofonts.github.io/raw/main/fonts"

# script name -> font families to try (first one that downloads wins)
FAMILIES = {
    "latin": ["NotoSerif"],
    "devanagari": ["NotoSerifDevanagari", "NotoSansDevanagari"],   # Hindi, Marathi
    "bengali": ["NotoSerifBengali", "NotoSansBengali"],
    "tamil": ["NotoSerifTamil", "NotoSansTamil"],
    "telugu": ["NotoSerifTelugu", "NotoSansTelugu"],
    "kannada": ["NotoSerifKannada", "NotoSansKannada"],
    "malayalam": ["NotoSerifMalayalam", "NotoSansMalayalam"],
    "gujarati": ["NotoSerifGujarati", "NotoSansGujarati"],
    "gurmukhi": ["NotoSerifGurmukhi", "NotoSansGurmukhi"],          # Punjabi
    "oriya": ["NotoSerifOriya", "NotoSansOriya"],                   # Odia
}


def download(client: httpx.Client, family: str, style: str) -> bytes | None:
    """Try to download one font file, e.g. NotoSerifTamil-Bold.ttf. Returns the bytes or None."""
    url = f"{BASE}/{family}/hinted/ttf/{family}-{style}.ttf"
    resp = client.get(url, follow_redirects=True)
    return resp.content if resp.status_code == 200 and len(resp.content) > 10_000 else None


def main():
    FONTS_DIR.mkdir(exist_ok=True)
    with httpx.Client(timeout=60) as client:
        for script, families in FAMILIES.items():
            for style in ("Regular", "Bold"):
                target = FONTS_DIR / f"{script}-{style}.ttf"     # saved under a simple name
                if target.exists():
                    print(f"  already have {target.name}")
                    continue
                for family in families:
                    data = download(client, family, style)
                    if data:
                        target.write_bytes(data)
                        print(f"  downloaded {target.name}  ({family}, {len(data) // 1024} KB)")
                        break
                else:
                    print(f"  !! could not download {script} {style}")
    print("Fonts ready in", FONTS_DIR)


if __name__ == "__main__":
    main()
