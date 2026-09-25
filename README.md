# CineMotion AI

**CineMotion AI** verwandelt ein einzelnes Foto in eine filmische Kamerafahrt
mit echtem Tiefengefuehl - komplett lokal, ohne Cloud-API und ohne
Nutzungsgebuehren. Aus einem Bild wird eine kurze, cinematische Video-Szene:
die Kamera fliegt durch die geschaetzte 3D-Tiefe des Fotos (Dolly, Orbit,
Ken-Burns-Zoom, Vertigo-Effekt ...), waehrend Farbgrading, Vignette,
Filmkorn, Bloom, chromatische Aberration und Kino-Letterbox das Bild wie aus
einem echten Film aussehen lassen.

## Wie es funktioniert

1. **Tiefenschaetzung** (`app/core/depth.py`) - eine schnelle, komplett
   offline laufende Heuristik (Bildschaerfe, vertikale Position,
   Zentrums-Prior) erzeugt eine Tiefenkarte des Fotos. Optional kann ein
   echtes neuronales Tiefenmodell (MiDaS) zugeschaltet werden.
2. **2.5D-Parallax-Warp** (`app/core/warp.py`) - eine virtuelle Kamera
   bewegt sich durch die Szene; nahe Bereiche verschieben sich staerker als
   ferne (`backward warping` per `cv2.remap`, kein GPU noetig).
3. **Kamerapfade** (`app/core/camera.py`) - fuenf handgebaute, ge-easte
   Bewegungsprofile: Parallax Dolly, Orbit Drift, Ken Burns+, Vertigo Zoom,
   Cinematic Drift.
4. **Filmische Effekte** (`app/core/effects.py`) - Teal&Orange-Grading,
   Vignette, Bloom/Glow, Filmkorn, chromatische Aberration,
   Bewegungsunschaerfe, Kino-Letterbox.
5. **Encoding** (`app/core/video.py`) - H.264-MP4 via `imageio` +
   mitgeliefertem `ffmpeg`-Binary (kein System-ffmpeg noetig).

Das Ergebnis ist kein diffusionsbasiertes "Video-Generieren" (das braucht
GPUs mit vielen GB VRAM und Minuten pro Sekunde Video) - stattdessen ein
schneller, algorithmischer 2.5D-Ansatz, der auf jeder CPU in Sekunden ein
ueberzeugendes, filmisches Kamerafahrt-Ergebnis liefert.

## Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Optional fuer den echten AI-Tiefenschaetzer (MiDaS, ca. 20-200 MB Download
je nach Variante, braucht Internetzugang):

```bash
pip install -r requirements-ai.txt
```

## Nutzung: Kommandozeile

```bash
python -m app.cli --image dein_foto.jpg --out szene.mp4 \
    --style parallax_dolly --duration 6 --resolution 1280
```

Alle verfuegbaren Kamera-Stile anzeigen:

```bash
python -m app.cli --list-styles
```

Wichtige Optionen:

| Option | Beschreibung |
|---|---|
| `--style` | `parallax_dolly`, `parallax_orbit`, `ken_burns`, `vertigo`, `drift` |
| `--duration` | Laenge des Clips in Sekunden |
| `--resolution` | maximale Kantenlaenge in Pixel |
| `--intensity` | Staerke von Zoom/Parallaxe (0.5-2.0) |
| `--color-grade` | `cinematic_teal_orange`, `warm_film`, `cold_thriller`, `none` |
| `--ai-depth` | echtes MiDaS-Tiefenmodell statt Heuristik |
| `--no-vignette` / `--no-grain` / `--no-bloom` / `--no-chromatic-aberration` / `--no-letterbox` / `--no-motion-blur` | einzelne Effekte abschalten |

## Nutzung: Web-App

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Dann im Browser `http://localhost:8000` oeffnen: Bild per Drag&Drop
hochladen, Kamerafahrt und Effekte waehlen, Video generieren, im Player
ansehen und herunterladen.

## Beispiel

`samples/sample_input.jpg` ist eine synthetisch erzeugte
Sonnenuntergangsszene (`scripts/make_sample_image.py`) zum sofortigen
Ausprobieren:

```bash
python -m app.cli --image samples/sample_input.jpg \
    --out samples/demo.mp4 --style parallax_dolly --duration 6
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Die Tests laufen komplett offline (nutzen die schnelle Tiefenheuristik) und
pruefen, dass jedes Kamera-Preset ein gueltiges, abspielbares MP4 mit der
erwarteten Framezahl erzeugt.

## Projektstruktur

```
app/
  cli.py              CLI-Einstiegspunkt
  main.py             FastAPI-Webserver
  static/             Web-UI (HTML/CSS/JS)
  core/
    depth.py          Tiefenschaetzung (heuristisch + optional MiDaS)
    camera.py          Kamerapfad-Presets
    warp.py             2.5D-Parallax-Warp-Engine
    effects.py           Farbgrading, Vignette, Bloom, Grain, ...
    pipeline.py            Orchestrierung
    video.py                H.264-Encoding
scripts/make_sample_image.py   erzeugt ein Demo-Bild
samples/                        Beispielbild
tests/                           pytest Smoke-Tests
```

## Grenzen & Ehrlichkeit

Dies ist ein eigenstaendiger, lokal laufender Algorithmus - kein Aufruf
einer fremden Video-KI. Er erzeugt keine neuen Bildinhalte (kein "Inpainting"
von verdeckten Bereichen, keine generative Bewegung wie bei
Diffusionsmodellen), sondern eine ueberzeugende Kamerafahrt durch die
geschaetzte Tiefe eines Einzelbilds - dieselbe Grundidee, die auch
"3D-Foto"-Funktionen grosser Plattformen nutzen, hier aber mit staerkerem
Kino-Grading kombiniert. Fuer beste Ergebnisse eignen sich Fotos mit klarem
Vordergrund/Hintergrund (Portraits, Landschaften, Architektur).
