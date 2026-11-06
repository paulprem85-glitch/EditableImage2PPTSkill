# SlideCraft AI Web App

A browser-based MVP for converting 16:9 slide images into editable PowerPoint decks, built on top of the existing EditableImage2PPTSkill scripts.

## Architecture

- FastAPI serves the browser UI and accepts PNG/JPG/WEBP uploads.
- Gemini API analyses each image and returns a structured slide reconstruction.
- The existing `scripts/asset_cropper.py`, `scripts/build_pptx_from_layout.py`, and `scripts/inspect_pptx.py` do the asset extraction, PPTX build, and package QA.
- API keys are read from the server environment only; never expose them in frontend JavaScript.

## Run in GitHub Codespaces

1. Open this repository in Codespaces.
2. In the terminal run:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r webapp/requirements.txt
   ```

3. Set your Gemini API key in the terminal (do not commit it):

   ```bash
   export GEMINI_API_KEY="YOUR_GEMINI_API_KEY"
   ```

4. Start the app:

   ```bash
   uvicorn webapp.app:app --host 0.0.0.0 --port 8000
   ```

5. In Codespaces, open the forwarded port 8000 in the browser.

## MVP scope and limitations

- Accepts up to 20 images per request, 15 MB each by default.
- Accepts 16:9 slide images only; non-16:9 uploads are rejected instead of distorted.
- Generates one editable PPTX deck from the uploaded images.
- Normal text is rebuilt as text boxes; simple geometry as native shapes/lines; photos/logos/complex visuals as separate picture objects.
- Visual fidelity is not guaranteed by schema-valid output alone. Review the downloaded deck in PowerPoint and iterate on the reconstruction prompt/schema.
- Temporary conversion files are stored in the server's temporary directory and are not persisted as a user library.

## Configuration

- `GEMINI_API_KEY`: required.
- `GEMINI_MODEL`: optional; defaults to `gemini-3.8-flash`.
- `MAX_FILES`: optional; defaults to 20.
- `MAX_IMAGE_MB`: optional; defaults to 15.

## Next milestones

1. Add an editable preview and per-slide correction controls.
2. Add background-task processing and progress updates for large batches.
3. Add authenticated user accounts, storage cleanup, and deployment configuration.
4. Add rendered visual QA using LibreOffice or a cloud rendering service.
