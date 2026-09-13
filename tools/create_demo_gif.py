#!/usr/bin/env python3
"""Generate the StringMorph console demo GIF from a real, inert fixture run."""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont


WIDTH = 1280
HEIGHT = 720
MAX_LINES = 18
LINE_HEIGHT = 27

COLORS = {
    "background_top": (10, 16, 28),
    "background_bottom": (17, 27, 46),
    "terminal": (12, 18, 30),
    "header": (20, 29, 45),
    "border": (47, 66, 91),
    "text": (224, 231, 241),
    "muted": (139, 154, 177),
    "command": (125, 211, 252),
    "heading": (196, 181, 253),
    "success": (110, 231, 183),
    "accent": (251, 191, 36),
}


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/CascadiaMono.ttf",
        "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/DejaVuSansMono.ttf",
        "DejaVuSansMono.ttf",
    ]
    if bold:
        candidates[1:1] = ["C:/Windows/Fonts/CascadiaMono-Bold.ttf", "C:/Windows/Fonts/consolab.ttf"]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def run_cli(cli: Path, working_dir: Path, *arguments: str) -> list[str]:
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, str(cli), *arguments],
        cwd=working_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return [line.expandtabs(4) for line in result.stdout.splitlines() if line.strip()]


def collect_transcript(cli: Path) -> list[tuple[str, str, str, int]]:
    fixture = bytes(range(32)) + b"RuntimeFeature42" + bytes((0, 255, 1, 2)) + b"INERT_TRAILER\0"

    with tempfile.TemporaryDirectory(prefix="stringmorph-demo-") as temporary:
        working_dir = Path(temporary)
        input_path = working_dir / "fixture.bin"
        csv_path = working_dir / "strings.csv"
        output_path = working_dir / "fixture_modified.bin"
        input_path.write_bytes(fixture)

        extract = run_cli(
            cli, working_dir, "fixture.bin", "-k", "Feature", "-v", "-o", "strings.csv"
        )
        csv_lines = csv_path.read_text(encoding="utf-8").splitlines()
        preview = run_cli(
            cli, working_dir, "fixture.bin", "-s", "strings.csv", "-t", "--single-char", "X"
        )
        execute = run_cli(
            cli, working_dir, "fixture.bin", "-s", "strings.csv", "-e", "--single-char", "X"
        )

        original = input_path.read_bytes()
        modified = output_path.read_bytes()
        with csv_path.open(newline="", encoding="utf-8") as csv_file:
            rows = list(csv.DictReader(csv_file))
        if len(rows) != 1:
            raise RuntimeError(f"Expected one selected CSV row, found {len(rows)}")

        start = int(rows[0]["Location"], 16)
        source = rows[0]["String"].encode("ascii")
        end = start + len(source)
        expected = b"".join(
            b"X" if chr(byte).isalpha() else b"4" if chr(byte).isdigit() else bytes((byte,))
            for byte in source
        )
        changed = [index for index, pair in enumerate(zip(original, modified)) if pair[0] != pair[1]]
        checks = {
            "size": len(original) == len(modified),
            "replacement": modified[start:end] == expected,
            "outside": original[:start] == modified[:start] and original[end:] == modified[end:],
            "changes": bool(changed) and all(start <= index < end for index in changed),
        }
        if not all(checks.values()):
            raise RuntimeError(f"Generated output failed byte verification: {checks}")

        events: list[tuple[str, str, str, int]] = []

        def add(text: str, kind: str, phase: str, duration: int = 420) -> None:
            events.append((text, kind, phase, duration))

        add("Safe demo: transforming an inert 66-byte fixture", "muted", "SETUP", 1000)
        add("PS> python StringMorph.py fixture.bin -k Feature -v -o strings.csv", "command", "EXTRACT", 1100)
        for line in extract:
            add(line, "heading" if line.startswith("[+]") else "text", "EXTRACT")
        add("", "text", "EXTRACT", 180)
        add("PS> Get-Content strings.csv", "command", "EXTRACT", 800)
        for line in csv_lines:
            add(line, "accent", "EXTRACT", 550)
        add("", "text", "PREVIEW", 180)
        add("PS> python StringMorph.py fixture.bin -s strings.csv -t --single-char X", "command", "PREVIEW", 1100)
        for line in preview:
            kind = "heading" if line.startswith("[+]") else "success" if "Preview complete" in line else "text"
            add(line, kind, "PREVIEW")
        add("", "text", "WRITE", 180)
        add("PS> python StringMorph.py fixture.bin -s strings.csv -e --single-char X", "command", "WRITE", 1100)
        for line in execute:
            kind = "heading" if line.startswith("[+]") else "success" if "saved as" in line else "text"
            add(line, kind, "WRITE")
        add("", "text", "VERIFY", 180)
        add("CHECK> compare fixture.bin with fixture_modified.bin", "command", "VERIFY", 850)
        add(f"Input: {len(original)} bytes  |  Output: {len(modified)} bytes  |  Same length: YES", "text", "VERIFY")
        add(f"Selected range: {start:#04x}..{end - 1:#04x} ({end - start} bytes)", "text", "VERIFY")
        add("Changed bytes outside selected range: 0", "text", "VERIFY")
        add(f"Replacement bytes: {modified[start:end].decode('ascii')}", "text", "VERIFY")
        add("VERIFICATION PASSED", "success", "VERIFY", 1800)
        return events


def gradient_background() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT))
    pixels = image.load()
    top = COLORS["background_top"]
    bottom = COLORS["background_bottom"]
    for y in range(HEIGHT):
        ratio = y / (HEIGHT - 1)
        color = tuple(round(a + (b - a) * ratio) for a, b in zip(top, bottom))
        for x in range(WIDTH):
            pixels[x, y] = color
    return image


def draw_frame(
    base: Image.Image,
    visible_lines: list[tuple[str, str]],
    phase: str,
    mono: ImageFont.ImageFont,
    label: ImageFont.ImageFont,
    title: ImageFont.ImageFont,
) -> Image.Image:
    image = base.copy()
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((35, 35, 1245, 685), radius=18, fill=COLORS["terminal"], outline=COLORS["border"], width=2)
    draw.rounded_rectangle((36, 36, 1244, 97), radius=17, fill=COLORS["header"])
    draw.rectangle((36, 76, 1244, 97), fill=COLORS["header"])

    for x, color in ((65, (255, 95, 86)), (91, (255, 189, 46)), (117, (39, 201, 63))):
        draw.ellipse((x - 7, 59, x + 7, 73), fill=color)
    draw.text((151, 54), "StringMorph", font=title, fill=COLORS["text"])
    draw.text((326, 59), "INERT FIXTURE DEMO", font=label, fill=COLORS["muted"])

    phase_box = draw.textbbox((0, 0), phase, font=label)
    phase_width = phase_box[2] - phase_box[0] + 30
    draw.rounded_rectangle((1205 - phase_width, 52, 1205, 82), radius=15, fill=(35, 53, 75))
    draw.text((1220 - phase_width, 56), phase, font=label, fill=COLORS["command"])

    y = 116
    for text, kind in visible_lines[-MAX_LINES:]:
        draw.text((67, y), text, font=mono, fill=COLORS[kind])
        y += LINE_HEIGHT

    draw.line((59, 640, 1221, 640), fill=(34, 48, 67), width=1)
    draw.text((67, 653), "SAFE BYTE TRANSFORM", font=label, fill=COLORS["muted"])
    draw.text((1012, 653), "LOOPING GIF", font=label, fill=COLORS["muted"])
    return image


def main() -> None:
    repository = Path(__file__).resolve().parents[1]
    cli = repository / "StringMorph.py"
    output = repository / "docs" / "assets" / "stringmorph-demo.gif"
    if not cli.is_file():
        raise SystemExit(f"StringMorph CLI not found: {cli}")

    events = collect_transcript(cli)
    base = gradient_background()
    mono = load_font(21)
    label = load_font(16, bold=True)
    title = load_font(26, bold=True)

    transcript: list[tuple[str, str]] = []
    frames: list[Image.Image] = []
    durations: list[int] = []
    for text, kind, phase, duration in events:
        transcript.append((text, kind))
        frames.append(draw_frame(base, transcript, phase, mono, label, title))
        durations.append(duration)

    palette = frames[0].quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    indexed = [palette]
    indexed.extend(
        frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames[1:]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    indexed[0].save(
        output,
        save_all=True,
        append_images=indexed[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    with Image.open(output) as rendered:
        rendered_frames = rendered.n_frames
        rendered_duration = 0
        for frame_number in range(rendered_frames):
            rendered.seek(frame_number)
            rendered_duration += rendered.info.get("duration", 0)
    total_seconds = rendered_duration / 1000
    print(f"Created {output}")
    print(f"{rendered_frames} frames, {WIDTH}x{HEIGHT}, {total_seconds:.1f}s, {output.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
