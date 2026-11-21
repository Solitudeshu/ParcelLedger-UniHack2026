"""Authenticated, bounded local GPU service. Has no database or chain authority."""
import base64
import hmac
import io
import json
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from ml.inference.research_pipeline import ResearchPipeline

MAX_BODY = 18_000_000
lock = threading.Lock()
pipeline = None


class EncodedImage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    asset_id: UUID
    image: str = Field(max_length=2_000_000,min_length=10)


class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: EncodedImage
    references: list[EncodedImage] = Field(min_length=1,max_length=8)
    region: list[tuple[int,int]] | None = None
    expected_identity: dict[str,str]


def decode(value):
    try:
        raw = base64.b64decode(value,validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != 'JPEG' or max(image.size)>1024 or min(image.size)<32 or getattr(image,'n_frames',1)!=1:
                raise ValueError('INVALID_IMAGE')
            image.load()
            import numpy as np
            return np.asarray(image.convert('RGB'))
    except Exception:
        raise HTTPException(422,'INVALID_RESEARCH_IMAGE') from None


@asynccontextmanager
async def lifespan(app):
    global pipeline
    pipeline = ResearchPipeline(os.environ['RESEARCH_TRANSFORMER_PATH'],os.environ['RESEARCH_VISION_POLICY_PATH'])
    yield
    pipeline = None


app = FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)


@app.get('/health')
def health():
    return {'status':'ready' if pipeline else 'starting','mode':'SHADOW_ONLY'}


def evaluate(payload):
    if not lock.acquire(blocking=False):
        raise HTTPException(429,'GPU_BUSY_RETRY_LATER')
    try:
        if pipeline is None:
            raise HTTPException(503,'GPU_NOT_READY')
        if payload.expected_identity != pipeline.identity:
            raise HTTPException(409,'MODEL_IDENTITY_MISMATCH')
        ids = [str(r.asset_id) for r in payload.references]
        if len(set(ids)) != len(ids) or str(payload.query.asset_id) in ids:
            raise HTTPException(422,'DUPLICATE_OR_SELF_REFERENCE')
        result = pipeline.compare(str(payload.query.asset_id),decode(payload.query.image),
                                  [(str(r.asset_id),decode(r.image)) for r in payload.references],payload.region)
        return result
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503,'RESEARCH_INFERENCE_UNAVAILABLE') from None
    finally:
        lock.release()


@app.post('/v1/evaluate')
async def endpoint(request: Request):
    token = Path(os.environ['RESEARCH_VISION_TOKEN_FILE']).read_text().strip()
    if len(token)<32 or not hmac.compare_digest(request.headers.get('X-Vision-Token',''),token):
        raise HTTPException(401,'UNAUTHORIZED')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>MAX_BODY:
            raise HTTPException(413,'PAYLOAD_TOO_LARGE')
    try:
        payload = Payload.model_validate(json.loads(body))
    except (ValidationError,ValueError):
        raise HTTPException(422,'INVALID_REQUEST') from None
    if payload.region is not None and (not 3<=len(payload.region)<=100 or any(not 0<=v<=10000 for p in payload.region for v in p)):
        raise HTTPException(422,'INVALID_REGION')
    return await run_in_threadpool(evaluate,payload)
