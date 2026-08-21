from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys


runpod_stub = ModuleType("runpod")
runpod_stub.serverless = SimpleNamespace(start=lambda _config: None)
sys.modules.setdefault("runpod", runpod_stub)

import handler


def test_validate_target_content_allows_safe_image(monkeypatch):
    analyser = SimpleNamespace(analyse_image=lambda _path: False)
    monkeypatch.setitem(sys.modules, "facefusion", SimpleNamespace(content_analyser=analyser))

    handler._validate_target_content(Path("target.jpg"), "image")


def test_validate_target_content_rejects_flagged_image(monkeypatch):
    analyser = SimpleNamespace(analyse_image=lambda _path: True)
    monkeypatch.setitem(sys.modules, "facefusion", SimpleNamespace(content_analyser=analyser))

    try:
        handler._validate_target_content(Path("target.jpg"), "image")
    except handler.ContentSafetyError as error:
        assert str(error) == "Target media did not pass FaceFusion's content safety check."
    else:
        raise AssertionError("Expected the content safety check to reject the image")


def test_validate_target_content_skips_video(monkeypatch):
    analyser = SimpleNamespace(
        analyse_image=lambda _path: (_ for _ in ()).throw(AssertionError("unexpected call"))
    )
    monkeypatch.setitem(sys.modules, "facefusion", SimpleNamespace(content_analyser=analyser))

    handler._validate_target_content(Path("target.mp4"), "video")
