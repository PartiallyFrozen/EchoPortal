"""Screenshot capture is monkeypatched everywhere: no real desktop grab is ever attempted."""
import base64
import io

from PIL import Image

import screenshot
import typer


def test_capture_downscales_and_returns_bare_base64_jpeg(monkeypatch):
    image = Image.new("RGB", (2000, 1000), (40, 90, 160))
    monkeypatch.setattr(screenshot.ImageGrab, "grab", lambda: image)
    monkeypatch.setattr(typer, "focused_window", lambda: ("Notepad", "notepad.exe"))

    result = screenshot.capture()

    assert result["ok"] is True
    assert result["width"] == 1280
    assert result["height"] == 640
    assert result["window_title"] == "Notepad"
    assert not result["image"].startswith("data:")
    with Image.open(io.BytesIO(base64.b64decode(result["image"]))) as loaded:
        assert loaded.format == "JPEG"
        assert loaded.size == (1280, 640)


def test_small_images_are_not_upscaled_and_missing_typer_is_harmless(monkeypatch):
    image = Image.new("RGBA", (320, 180), (0, 0, 0, 255))
    monkeypatch.setattr(screenshot.ImageGrab, "grab", lambda: image)
    monkeypatch.setattr(screenshot, "typer", None)

    result = screenshot.capture(quality=50, max_width=1280)

    assert result["ok"] is True
    assert (result["width"], result["height"]) == (320, 180)
    assert result["window_title"] == ""


def test_capture_reports_failures_instead_of_raising(monkeypatch):
    def boom():
        raise OSError("no display")

    monkeypatch.setattr(screenshot.ImageGrab, "grab", boom)
    result = screenshot.capture()
    assert result["ok"] is False
    assert "no display" in result["error"]
