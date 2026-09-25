# CineMotion AI

**CineMotion AI** verwandelt ein einzelnes Foto in eine filmische Kamerafahrt
mit echtem Tiefengefuehl - komplett lokal, ohne Cloud-API und ohne
Nutzungsgebuehren. Aus einem Bild wird eine kurze, cinematische Video-Szene:
die Kamera fliegt durch die geschaetzte 3D-Tiefe des Fotos (Dolly, Orbit,
Ken-Burns-Zoom, Vertigo-Effekt ...), waehrend Farbgrading, Vignette,
Filmkorn, Bloom, chromatische Aberration und Kino-Letterbox das Bild wie aus
einem echten Film aussehen lassen.

> **Neu: KI-Agentensystem** - Master-Agent, sieben Spezial-Agenten, Chief Legal &
> Compliance Agent und QA mit Owner-Freigabe-, Berechtigungs- und Audit-System. Siehe [`agent_system/README.md`](agent_system/README.md).

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

## Architektur: austauschbare Video-Provider

Seit Ausbaustufe 2 ist die Video-Erzeugung hinter einer **Provider-
Schnittstelle** (`app/core/providers/`) organisiert. Ein Provider kapselt
eine komplette Bild-zu-Video-Engine; CLI, Web-App und der Orchestrator
(`app/core/orchestrator.py`) kennen nur das gemeinsame Interface
(`VideoProvider.check_capability()` + `.generate()`), nie die konkrete
Implementierung:

| Provider-ID | Engine | Status |
|---|---|---|
| `parallax_2_5d` | bestehende 2.5D-Parallax-Engine (dieses Dokument, Abschnitt oben) | **immer verfuegbar, Standard & garantierter Fallback** |
| `generative_local_svd` | echtes generatives Diffusionsmodell (Stable Video Diffusion) lokal via `diffusers` | nur mit NVIDIA-GPU (>= 10 GB VRAM), siehe unten |
| `generative_api_replicate` | externe, kostenpflichtige Video-KI-API (Replicate) | nur mit eigenem API-Token, standardmaessig deaktiviert |

**Fallback-Regel:** Wird ein Provider angefragt, der gerade nicht verfuegbar
ist (keine GPU, kein Token, fehlende Abhaengigkeit), generiert der
Orchestrator automatisch mit `parallax_2_5d` und meldet das transparent
zurueck (`fallback_reason` in der API-Antwort, Hinweistext in der Web-UI,
Konsolenausgabe in der CLI). Es wird nie stillschweigend nichts generiert.

Ein weiterer Provider (z.B. eine andere API) wird ergaenzt, indem eine neue
Klasse mit demselben `VideoProvider`-Interface geschrieben und in
`app/core/providers/__init__.py` registriert wird - an CLI, Web-App oder
Orchestrator muss dafuer nichts geaendert werden.

### Hardware-Befund (Entwicklungsumgebung)

```bash
python -m app.cli --hardware-check
# oder: python scripts/hardware_check.py
```

Die Umgebung, in der dieses Projekt entwickelt wurde, hat **keine GPU**
(4 vCPUs, 16 GB RAM, keine `nvidia-smi`/`/dev/dri`) und blockiert per
Netzwerk-Policy ausgehende Verbindungen zu Modell-/API-Hosts wie
`huggingface.co` oder `api.replicate.com`. Ein lokales generatives
Video-Modell ist dort daher weder sinnvoll (CPU-Inferenz braucht Minuten
bis Stunden pro Clip) noch technisch erreichbar (Downloads blockiert) - der
`--hardware-check` erkennt genau das und `generative_local_svd` bleibt
entsprechend deaktiviert, bis die App auf einer Maschine mit GPU laeuft.

### Generative Video spaeter aktivieren

**Lokal (eigene GPU-Maschine, >= 10 GB VRAM):**

```bash
pip install -r requirements-generative.txt
python -m app.cli --image foto.jpg --provider generative_local_svd
```

Der erste Lauf laedt automatisch die Modellgewichte (~9-10 GB) von
huggingface.co. `check_capability()` prueft vorher GPU + VRAM und meldet
klar, falls nicht ausreichend.

**Externe API (Replicate, kostenpflichtig):** bewusst nicht voraktiviert,
keine erfundenen Zugangsdaten. Aktivierung erst nach eigener Entscheidung:

```bash
export REPLICATE_API_TOKEN="dein-eigenes-token"
export REPLICATE_VIDEO_MODEL_VERSION="model-version-hash-deiner-wahl"
python -m app.cli --image foto.jpg --provider generative_api_replicate
```

Ohne beide Variablen bleibt der Provider inaktiv (`check_capability()`
macht dabei nie einen Netzwerkaufruf) und es entstehen keine Kosten.

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

Optional fuer den lokalen generativen Video-Provider (braucht GPU, siehe
oben):

```bash
pip install -r requirements-generative.txt
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
| `--provider` | Video-Engine, Standard: `parallax_2_5d` (siehe `--list-providers`) |
| `--list-providers` | alle Provider mit Live-Verfuegbarkeit anzeigen |
| `--hardware-check` | CPU/RAM/GPU pruefen und Empfehlung ausgeben |

## Nutzung: Web-App

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Dann im Browser `http://localhost:8000` oeffnen: Bild per Drag&Drop
hochladen, zuerst die **Engine** waehlen (Parallax Dolly oder ein
generativer Provider, jeweils mit Live-Verfuegbarkeits-Badge), dann
Kamerafahrt und Effekte, Video generieren, im Player ansehen und
herunterladen. Ist der gewaehlte generative Provider nicht verfuegbar,
erscheint nach der Generierung ein Hinweis, dass automatisch auf Parallax
Dolly ausgewichen wurde.

Zusaetzliche Endpunkte: `/api/providers` (Liste + Verfuegbarkeit) und
`/api/hardware` (CPU/RAM/GPU-Befund als JSON).

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
pruefen sowohl die 2.5D-Engine (jedes Kamera-Preset erzeugt ein gueltiges,
abspielbares MP4) als auch die Provider-Architektur (Registry, Capability-
Checks, automatischer Fallback auf `parallax_2_5d`, kein Netzwerkaufruf in
`check_capability()`).

## Projektstruktur

```
app/
  cli.py              CLI-Einstiegspunkt
  main.py             FastAPI-Webserver
  diagnostics.py      Hardware-/Umgebungs-Check
  static/             Web-UI (HTML/CSS/JS)
  core/
    depth.py          Tiefenschaetzung (heuristisch + optional MiDaS)
    camera.py          Kamerapfad-Presets
    warp.py             2.5D-Parallax-Warp-Engine
    effects.py           Farbgrading, Vignette, Bloom, Grain, ...
    pipeline.py            2.5D-Rendering-Pipeline (von parallax_2_5d genutzt)
    video.py                H.264-Encoding
    orchestrator.py          Provider-Auswahl + automatischer Fallback
    providers/
      base.py                VideoProvider-Interface
      parallax_provider.py   Wrapper um die bestehende 2.5D-Engine (Standard)
      generative_local.py    Stable-Video-Diffusion-Provider (GPU noetig)
      generative_api.py      Replicate-API-Provider (Token noetig)
scripts/make_sample_image.py   erzeugt ein Demo-Bild
scripts/hardware_check.py       Hardware-Diagnose als Standalone-Skript
samples/                        Beispielbild
tests/                           pytest Smoke- & Provider-Tests
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
