"""ComfyUI outputs arrive as filenames; the face shows an image or plays a clip depending on this."""
import comfy


def test_video_extensions_are_recognised():
    for name in ("out.mp4", "clip.webm", "take.mov", "render.mkv", "loop.gif", "anim.webp"):
        assert comfy.ComfyWatcher.is_video(name)


def test_stills_are_not_videos():
    for name in ("z-image_00001_.png", "photo.jpg", "sheet.jpeg", "mask.tiff"):
        assert not comfy.ComfyWatcher.is_video(name)


def test_the_check_is_case_insensitive():
    assert comfy.ComfyWatcher.is_video("CLIP.MP4")
    assert not comfy.ComfyWatcher.is_video("IMAGE.PNG")


def test_a_name_that_merely_contains_an_extension_is_not_a_video():
    assert not comfy.ComfyWatcher.is_video("mp4_reference_sheet.png")
