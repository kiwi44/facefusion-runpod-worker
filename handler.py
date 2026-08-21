from __future__ import annotations

import base64
import binascii
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import runpod


FACEFUSION_ROOT = Path(os.getenv("FACEFUSION_ROOT", "/facefusion"))
sys.path.insert(0, str(FACEFUSION_ROOT))
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_VIDEO_BYTES = 100 * 1024 * 1024
DOWNLOAD_TIMEOUT = (15, 180)
UPLOAD_TIMEOUT = (15, 300)

CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
}

FACE_SWAPPER_MODELS = {
    "hyperswap_1a_256",
    "ghost_1_256",
    "ghost_3_256",
}


class InputError(ValueError):
    pass


def _https_url(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{field} is required")
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise InputError(f"{field} must be an HTTPS URL")
    return value.strip()


def _extension_from_magic(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return ".mp4"
    return None


def _download(url: str, directory: Path, stem: str, max_bytes: int) -> Path:
    with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > max_bytes:
            raise InputError(f"{stem} exceeds the size limit")

        chunks = response.iter_content(chunk_size=1024 * 1024)
        first_chunk = next(chunks, b"")
        extension = CONTENT_TYPE_EXTENSIONS.get(content_type) or _extension_from_magic(first_chunk)
        if not extension:
            raise InputError(f"unsupported media type for {stem}: {content_type or 'unknown'}")

        path = directory / f"{stem}{extension}"
        size = len(first_chunk)
        if size > max_bytes:
            raise InputError(f"{stem} exceeds the size limit")
        with path.open("wb") as output:
            output.write(first_chunk)
            for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise InputError(f"{stem} exceeds the size limit")
                output.write(chunk)
        if size == 0:
            raise InputError(f"{stem} was empty")
        return path


def _write_data_image(value: Any, directory: Path) -> Path:
    if not isinstance(value, str) or not value.startswith("data:image/"):
        raise InputError("frame_data_url must be an image data URL")
    try:
        header, encoded = value.split(",", 1)
        media_type = header[5:].split(";", 1)[0].lower()
        extension = CONTENT_TYPE_EXTENSIONS.get(media_type)
        if not extension or not extension.startswith("."):
            raise InputError("frame_data_url uses an unsupported image type")
        body = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise InputError("frame_data_url is invalid") from error
    if not body or len(body) > 8 * 1024 * 1024:
        raise InputError("frame_data_url exceeds the size limit")
    path = directory / f"frame{extension}"
    path.write_bytes(body)
    return path


def _upload(path: Path, upload: Any, media_type: str) -> str:
    if not isinstance(upload, dict):
        raise InputError("output_upload is required")
    url = _https_url(upload.get("url"), "output_upload.url")
    key = upload.get("key")
    if not isinstance(key, str) or not key:
        raise InputError("output_upload.key is required")
    raw_headers = upload.get("headers") or {}
    if not isinstance(raw_headers, dict):
        raise InputError("output_upload.headers must be an object")
    headers = {
        str(name): str(value)
        for name, value in raw_headers.items()
        if str(name).lower() in {"content-type", "cache-control", "x-upsert"}
    }
    headers["Content-Type"] = "video/mp4" if media_type == "video" else "image/jpeg"
    with path.open("rb") as body:
        response = requests.put(url, headers=headers, data=body, timeout=UPLOAD_TIMEOUT)
    response.raise_for_status()
    return key


def _run_swap(job_input: dict[str, Any]) -> dict[str, Any]:
    media_type = job_input.get("media_type")
    if media_type not in {"image", "video"}:
        raise InputError("media_type must be image or video")

    source_url = _https_url(job_input.get("source_url"), "source_url")
    target_url = _https_url(job_input.get("target_url"), "target_url")
    face_mode = job_input.get("face_mode", "one")
    if face_mode not in {"one", "reference", "many"}:
        raise InputError("face_mode must be one, reference, or many")

    face_swapper_model = job_input.get("face_swapper_model", "hyperswap_1a_256")
    if face_swapper_model not in FACE_SWAPPER_MODELS:
        raise InputError("face_swapper_model is not supported")

    face_index = job_input.get("face_index", 0)
    reference_frame_number = job_input.get("reference_frame_number", 0)
    if not isinstance(face_index, int) or face_index < 0 or face_index > 20:
        raise InputError("face_index must be an integer from 0 to 20")
    if (
        not isinstance(reference_frame_number, int)
        or reference_frame_number < 0
        or reference_frame_number > 1_000_000
    ):
        raise InputError("reference_frame_number is invalid")

    with tempfile.TemporaryDirectory(prefix="facefusion-") as temp_name:
        temp_dir = Path(temp_name)
        source_path = _download(source_url, temp_dir, "source", MAX_IMAGE_BYTES)
        target_path = _download(
            target_url,
            temp_dir,
            "target",
            MAX_VIDEO_BYTES if media_type == "video" else MAX_IMAGE_BYTES,
        )
        output_path = temp_dir / ("output.mp4" if media_type == "video" else "output.jpg")
        jobs_path = temp_dir / "jobs"
        jobs_path.mkdir()

        command = [
            "python",
            "facefusion.py",
            "headless-run",
            "--jobs-path",
            str(jobs_path),
            "--processors",
            "face_swapper",
            "--face-swapper-model",
            face_swapper_model,
            "--execution-providers",
            "cuda",
            "--source-paths",
            str(source_path),
            "--target-path",
            str(target_path),
            "--output-path",
            str(output_path),
            "--face-selector-mode",
            face_mode,
            "--face-selector-order",
            "left-right",
            "--reference-face-position",
            str(face_index),
            "--reference-frame-number",
            str(reference_frame_number),
            "--log-level",
            "warn",
        ]
        if media_type == "video":
            command.extend(["--output-video-preset", "veryfast"])

        completed = subprocess.run(
            command,
            cwd=FACEFUSION_ROOT,
            capture_output=True,
            text=True,
            timeout=60 * 45,
        )
        if completed.returncode != 0 or not output_path.is_file():
            detail = (completed.stderr or completed.stdout or "unknown error")[-2000:]
            raise RuntimeError(f"FaceFusion failed: {detail}")

        output_key = _upload(output_path, job_input.get("output_upload"), media_type)
        return {
            "status": "succeeded",
            "output_key": output_key,
            "media_type": media_type,
            "bytes": output_path.stat().st_size,
        }


def _configure_detection() -> None:
    from facefusion import state_manager

    settings = {
        "execution_device_ids": [0],
        "execution_providers": ["cuda"],
        "download_providers": ["github"],
        "face_detector_angles": [0],
        "face_detector_model": "yolo_face",
        "face_detector_size": "640x640",
        "face_detector_margin": (0, 0, 0, 0),
        "face_detector_score": 0.5,
        "face_landmarker_model": "2dfan4",
        "face_landmarker_score": 0.5,
    }
    for key, value in settings.items():
        state_manager.init_item(key, value)


def _detect_faces(job_input: dict[str, Any]) -> dict[str, Any]:
    from facefusion.face_creator import get_many_faces
    from facefusion.face_selector import sort_faces_by_order
    from facefusion.vision import read_static_image

    with tempfile.TemporaryDirectory(prefix="facefusion-detect-") as temp_name:
        temp_dir = Path(temp_name)
        if job_input.get("frame_data_url"):
            target_path = _write_data_image(job_input.get("frame_data_url"), temp_dir)
        else:
            target_url = _https_url(job_input.get("target_url"), "target_url")
            target_path = _download(target_url, temp_dir, "target", MAX_IMAGE_BYTES)

        frame = read_static_image(str(target_path))
        if frame is None:
            raise InputError("target image could not be decoded")
        _configure_detection()
        faces = sort_faces_by_order(get_many_faces([frame]), "left-right")
        boxes = []
        for face in faces[:20]:
            left, top, right, bottom = face.bounding_box.tolist()
            boxes.append(
                {
                    "left": max(0, round(float(left))),
                    "top": max(0, round(float(top))),
                    "right": max(0, round(float(right))),
                    "bottom": max(0, round(float(bottom))),
                }
            )
        return {"status": "succeeded", "faces": boxes, "count": len(boxes)}


def handler(event: dict[str, Any]) -> dict[str, Any]:
    job_input = event.get("input")
    if not isinstance(job_input, dict):
        return {"status": "failed", "error": "input must be an object"}
    try:
        operation = job_input.get("operation", "swap")
        if operation == "detect":
            return _detect_faces(job_input)
        if operation == "swap":
            return _run_swap(job_input)
        raise InputError("operation must be detect or swap")
    except InputError as error:
        return {"status": "failed", "error": str(error), "error_type": "input"}
    except requests.RequestException:
        return {
            "status": "failed",
            "error": "media transfer failed",
            "error_type": "transfer",
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "failed",
            "error": "FaceFusion processing timed out",
            "error_type": "timeout",
        }
    except Exception:
        traceback.print_exc()
        return {
            "status": "failed",
            "error": "FaceFusion processing failed",
            "error_type": "processing",
        }


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
