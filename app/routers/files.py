from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from app.domains.downloads import resolve_download_file_target

router = APIRouter()


@router.get("/thumbnails/{filename:path}")
async def thumbnail(filename: str, request: Request):
    safe_name = Path(filename).name
    if safe_name != filename:
        return JSONResponse(status_code=400, content={"detail": "invalid filename"})

    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        return JSONResponse(status_code=503, content={"detail": "runtime not ready"})

    target = Path(runtime.config.thumbnail_dir) / safe_name
    if not target.exists() or not target.is_file():
        return JSONResponse(status_code=404, content={"detail": "thumbnail not found"})
    return FileResponse(target)


@router.get("/downloads/files/{filename:path}")
async def download_file(
    filename: str,
    request: Request,
    job_id: int | None = Query(default=None, ge=1),
    probe: bool = Query(default=False),
):
    safe_name = Path(filename).name
    if safe_name != filename:
        return JSONResponse(
            status_code=400, content={"detail": "invalid filename", "code": "invalid_filename"}
        )

    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        return JSONResponse(
            status_code=503, content={"detail": "runtime not ready", "code": "runtime_not_ready"}
        )

    target_result = await resolve_download_file_target(
        runtime.db,
        filename=safe_name,
        default_download_dir=runtime.config.download_dir,
        job_id=job_id,
    )
    if not target_result.ok:
        status_code = 400 if target_result.code == "invalid_filename" else 404
        return JSONResponse(
            status_code=status_code,
            content={"detail": target_result.message, "code": target_result.code},
        )
    if probe:
        return {"ok": True, "filename": safe_name}
    if target_result.target is None:
        return JSONResponse(
            status_code=500,
            content={"detail": "download target missing", "code": "download_target_missing"},
        )
    return FileResponse(target_result.target)
