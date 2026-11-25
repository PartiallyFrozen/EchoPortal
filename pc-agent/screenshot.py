"""Desktop screenshot for the assistant's vision tools.

Capture is read-only: the focused window title is sampled for context, but nothing here moves a
window, changes focus or touches the clipboard. The image is returned as bare base64 JPEG (no
``data:`` prefix) so it can be attached directly to a model request.
"""
import base64
import io

from PIL import Image, ImageGrab

try:
    import typer
except Exception:  # pywin32 may be missing on a development machine
    typer = None

RESAMPLE = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)


def _window_title() -> str:
    if typer is None:
        return ""
    try:
        return str(typer.focused_window()[0] or "")
    except Exception:
        return ""


def capture(quality: int = 78, max_width: int = 1280) -> dict:
    """Grab the desktop, downscale it and return a JSON-safe base64 JPEG result."""
    try:
        img = ImageGrab.grab()
        if img is None:
            raise RuntimeError("ImageGrab.grab returned no image")
        width, height = int(img.size[0]), int(img.size[1])
        limit = max(1, int(max_width))
        if width > limit:
            height = max(1, int(round(height * limit / float(width))))
            width = limit
            img = img.resize((width, height), RESAMPLE)
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=max(1, min(100, int(quality))), optimize=True)
        return {
            "ok": True,
            "image": base64.b64encode(buf.getvalue()).decode("ascii"),
            "width": width,
            "height": height,
            "window_title": _window_title(),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
