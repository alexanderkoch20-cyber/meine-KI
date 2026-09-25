"""Hardware- und Umgebungs-Diagnose.

Beantwortet die Frage "Welcher Video-Provider ist auf dieser Maschine
realistisch nutzbar?" anhand von CPU/RAM/Disk, GPU-Erkennung (nvidia-smi
bzw. torch.cuda) und den Provider-Verfuegbarkeits-Checks. Wird von der CLI
(`--hardware-check`) und `scripts/hardware_check.py` genutzt.

Hinweis: Wenn dieses Kommando in einer Cloud-Sandbox/einem Container laeuft,
beschreibt es NUR diese Umgebung - nicht zwangslaeufig den Rechner, auf dem
die App spaeter tatsaechlich betrieben werden soll.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field


@dataclass
class HardwareReport:
    cpu_count: int
    ram_total_gb: float
    disk_free_gb: float
    platform: str
    gpu_found: bool
    gpu_name: str | None
    gpu_vram_gb: float | None
    gpu_detection_method: str
    notes: list[str] = field(default_factory=list)


def _read_ram_gb() -> float:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / (1024 * 1024), 1)
    except OSError:
        pass
    return 0.0


def _detect_gpu() -> tuple[bool, str | None, float | None, str]:
    """Versucht eine GPU zu erkennen: zuerst nvidia-smi, dann torch.cuda."""
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            out = subprocess.run(
                [nvidia_smi, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            line = out.stdout.strip().splitlines()[0]
            name, mem = [x.strip() for x in line.split(",")]
            vram_gb = float(mem.replace(" MiB", "")) / 1024
            return True, name, round(vram_gb, 1), "nvidia-smi"
        except Exception:
            pass

    try:
        import torch

        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            return True, props.name, round(props.total_memory / 1e9, 1), "torch.cuda"
    except ImportError:
        pass

    return False, None, None, "keine GPU gefunden (nvidia-smi fehlt, torch.cuda nicht verfuegbar/kein Torch installiert)"


def run_hardware_check() -> HardwareReport:
    cpu_count = os.cpu_count() or 1
    ram_gb = _read_ram_gb()
    disk_free_gb = round(shutil.disk_usage("/").free / (1024**3), 1)
    gpu_found, gpu_name, gpu_vram, gpu_method = _detect_gpu()

    notes = []
    if not gpu_found:
        notes.append(
            "Keine GPU erkannt: lokale generative Video-Modelle (Stable Video "
            "Diffusion o.ae.) sind auf dieser Maschine nicht praktikabel - "
            "Parallax Dolly (2.5D, CPU) bleibt die richtige Wahl, alternativ "
            "eine externe API mit eigenem Zugangstoken."
        )
    elif gpu_vram is not None and gpu_vram < 10:
        notes.append(
            f"GPU gefunden ({gpu_name}, {gpu_vram} GB VRAM), aber vermutlich zu "
            "wenig VRAM fuer Stable Video Diffusion (empfohlen: >= 10-16 GB)."
        )
    else:
        notes.append(
            f"GPU gefunden ({gpu_name}, {gpu_vram} GB VRAM) - lokale generative "
            "Video-Modelle sind grundsaetzlich moeglich, siehe "
            "requirements-generative.txt."
        )

    if ram_gb and ram_gb < 8:
        notes.append(f"Nur {ram_gb} GB RAM - auch die CPU-Pipeline sollte mit kleineren Aufloesungen laufen.")

    return HardwareReport(
        cpu_count=cpu_count,
        ram_total_gb=ram_gb,
        disk_free_gb=disk_free_gb,
        platform=platform.platform(),
        gpu_found=gpu_found,
        gpu_name=gpu_name,
        gpu_vram_gb=gpu_vram,
        gpu_detection_method=gpu_method,
        notes=notes,
    )


def format_report(report: HardwareReport) -> str:
    lines = [
        "=== Hardware- & Umgebungs-Check ===",
        f"Plattform     : {report.platform}",
        f"CPU-Kerne     : {report.cpu_count}",
        f"RAM gesamt    : {report.ram_total_gb} GB",
        f"Disk frei     : {report.disk_free_gb} GB",
        f"GPU erkannt   : {'ja' if report.gpu_found else 'nein'} ({report.gpu_detection_method})",
    ]
    if report.gpu_found:
        lines.append(f"GPU           : {report.gpu_name} ({report.gpu_vram_gb} GB VRAM)")
    lines.append("")
    lines.append("Empfehlung:")
    for note in report.notes:
        lines.append(f"  - {note}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_report(run_hardware_check()))
