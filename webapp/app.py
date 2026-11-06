from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
STATIC = Path(__file__).parent / "static"
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
MAX_FILES = int(os.getenv("MAX_FILES", "20"))
MAX_BYTES = int(os.getenv("MAX_IMAGE_MB", "15")) * 1024 * 1024

app = FastAPI(title="SlideCraft AI", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class Asset(BaseModel):
    name: str
    box: list[int] = Field(description="[x1,y1,x2,y2] in source pixels")
    mode: Literal["keep", "transparent"] = "keep"


class Element(BaseModel):
    type: Literal["text", "shape", "line", "image"]
    name: str = ""
    box: list[float] | None = None
    text: str | None = None
    font: str | None = "Arial"
    size: float | None = 18
    bold: bool | None = False
    italic: bool | None = False
    color: str | None = "#111111"
    align: Literal["left", "center", "right", "justify"] | None = "left"
    valign: Literal["top", "middle", "bottom"] | None = "middle"
    line_spacing: float | None = 1.05
    shape: Literal["rect", "rounded_rect", "oval", "diamond", "triangle", "trapezoid"] | None = "rect"
    fill: str | None = "transparent"
    line: str | None = "transparent"
    line_width: float | None = 1
    points: list[float] | None = None
    path: str | None = None
    rotation: float | None = None


class SlideAnalysis(BaseModel):
    background: str = "#FFFFFF"
    assets: list[Asset] = []
    elements: list[Element]


PROMPT = """Reconstruct this slide screenshot as an editable PowerPoint slide.
Canvas is 1920x1080 pixels (16:9). Return a complete, ordered list of elements.
Rules:
- Preserve ALL visible wording exactly; do not invent or omit content.
- Normal text must be type=text with a pixel box [left, top, width, height], editable text, font, size in points, bold, color, alignment and valign.
- Simple rectangles/cards/circles must be native type=shape. Simple dividers must be type=line with points [x1,y1,x2,y2].
- Photos, logos, complex icons, detailed illustrations, gradients/textures that cannot be rebuilt simply must be separate assets. Define asset crop boxes [x1,y1,x2,y2] within the image. Use mode=keep unless the crop is clearly a flat-background logo/icon, then transparent.
- For each visual asset, add a matching type=image element with path exactly "assets/page_001/<asset-name>.png" and the image box [left,top,width,height].
- Keep element order back-to-front. Background color goes in background.
- Use generous text boxes and accurate positions. Do not create a full-slide screenshot image or full-slide background asset.
- Use Arial/Aptos-like sans serif if exact font is unclear.
- Coordinates must fit 1920x1080. Return JSON matching the supplied schema only.
- If uncertain about tiny details, prioritize correct text and major geometry over hallucination."""


def normalize_image(src: Path, dst: Path) -> tuple[int, int]:
    with Image.open(src) as im:
        im = im.convert("RGB")
        ratio = im.width / im.height
        if abs(ratio - (16 / 9)) > 0.035:
            raise ValueError(f"{src.name} is not 16:9 (found {im.width}x{im.height}). This MVP currently accepts 16:9 slide images.")
        im.resize((1920, 1080), Image.Resampling.LANCZOS).save(dst, "PNG", optimize=True)
    return 1920, 1080


def analyze(client: genai.Client, image_path: Path) -> SlideAnalysis:
    with Image.open(image_path) as im:
        image = im.convert("RGB")
        response = client.models.generate_content(
            model=MODEL,
            contents=[PROMPT, image],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=SlideAnalysis,
                temperature=0.1,
            ),
        )
    if not response.text:
        raise RuntimeError("Gemini returned an empty response.")
    return SlideAnalysis.model_validate_json(response.text)


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "gemini_configured": bool(os.getenv("GEMINI_API_KEY")), "model": MODEL}


@app.post("/api/convert")
async def convert(files: list[UploadFile] = File(...)):
    if not os.getenv("GEMINI_API_KEY"):
        raise HTTPException(503, "Gemini API key is not configured. Add GEMINI_API_KEY to your environment.")
    if not files or len(files) > MAX_FILES:
        raise HTTPException(400, f"Upload between 1 and {MAX_FILES} images.")
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    work = Path(tempfile.mkdtemp(prefix="slidecraft-"))
    normalized_dir = work / "input"
    assets_dir = work / "assets"
    layouts_dir = work / "layouts"
    output_dir = work / "output"
    for folder in (normalized_dir, assets_dir, layouts_dir, output_dir):
        folder.mkdir(parents=True, exist_ok=True)

    slides = []
    try:
        for index, upload in enumerate(files, start=1):
            suffix = Path(upload.filename or "").suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                raise HTTPException(400, f"Unsupported file type: {upload.filename}")
            data = await upload.read()
            if len(data) > MAX_BYTES:
                raise HTTPException(413, f"{upload.filename} exceeds {MAX_BYTES // (1024*1024)} MB.")
            original = normalized_dir / f"source_{index:03d}{suffix}"
            original.write_bytes(data)
            normalized = normalized_dir / f"slide_{index:03d}.png"
            try:
                normalize_image(original, normalized)
                analysis = analyze(client, normalized)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

            page_assets = assets_dir / f"page_{index:03d}"
            page_assets.mkdir(parents=True, exist_ok=True)
            manifest = {
                "source": str(normalized),
                "assets": [a.model_dump() for a in analysis.assets],
            }
            manifest_path = work / f"page_{index:03d}.assets.json"
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(SCRIPTS / "asset_cropper.py"),
                 "--manifest", str(manifest_path), "--out-dir", str(page_assets), "--contact-sheet"],
                check=True, capture_output=True, text=True, timeout=120,
            )

            elements = []
            for item in analysis.elements:
                el = item.model_dump(exclude_none=True)
                if el.get("type") == "image" and el.get("path"):
                    # Restrict generated paths to the assets folder for this slide.
                    filename = Path(el["path"]).name
                    el["path"] = f"assets/page_{index:03d}/{filename}"
                elements.append(el)
            slides.append({"background": analysis.background, "elements": elements})

        layout = {
            "slide_size": {"width_in": 13.333333, "height_in": 7.5},
            "source_width": 1920,
            "source_height": 1080,
            "slides": slides,
        }
        layout_path = layouts_dir / "combined.layout.json"
        layout_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
        pptx_path = output_dir / "editable_slides.pptx"
        subprocess.run(
            [sys.executable, str(SCRIPTS / "build_pptx_from_layout.py"),
             "--layout", str(layout_path), "--assets-root", str(work), "--out", str(pptx_path)],
            check=True, capture_output=True, text=True, timeout=180,
        )
        report_path = work / "qa_report.json"
        subprocess.run(
            [sys.executable, str(SCRIPTS / "inspect_pptx.py"),
             "--pptx", str(pptx_path), "--report", str(report_path)],
            check=True, capture_output=True, text=True, timeout=60,
        )
        return FileResponse(
            pptx_path,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            filename="SlideCraft_Editable.pptx",
            background=None,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "PPTX processing failed")[-3000:]
        raise HTTPException(500, f"Conversion failed: {detail}") from exc
    except Exception as exc:
        raise HTTPException(500, f"Conversion failed: {exc}") from exc
