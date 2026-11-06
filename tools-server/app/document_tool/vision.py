"""Shared OpenAI-compatible vision requests with the document index cache."""
from __future__ import annotations
import base64
import hashlib
import json
import os
import httpx

def _vl_config() -> tuple[str, str, str]:
    base = os.getenv("TOOLS_VL_BASE_URL") or os.getenv("AV_PLANNER_BASE_URL") or "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    key = os.getenv("TOOLS_VL_API_KEY") or os.getenv("AV_PLANNER_API_KEY") or os.getenv("QWEN_API_KEY") or ""
    model = os.getenv("TOOLS_VL_MODEL") or os.getenv("AV_PLANNER_MODEL") or "qwen3-vl-plus"
    if not key:
        raise RuntimeError("Vision model is not configured: set AV_PLANNER_API_KEY (Qwen-VL) or TOOLS_VL_* in .env")
    return base.rstrip("/"), key, model



def inspect_images(idx,unit,identity,load_images,prompt,config=None):
    import time

    from ..observe import get_logger

    base,key,model=config or _vl_config()
    token=hashlib.sha256(json.dumps([identity,prompt,base,model],ensure_ascii=False).encode()).hexdigest()
    fresh=False
    def request():
        nonlocal fresh
        fresh=True
        images=load_images()
        if not 1<=len(images)<=4:raise ValueError('Supply 1..4 images')
        content=[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(b).decode()}} for b in images]
        content.append({'type':'text','text':prompt})
        response=httpx.post(base+'/chat/completions',headers={'Authorization':'Bearer '+key},
            json={'model':model,'messages':[{'role':'user','content':content}],'temperature':0,'max_tokens':4096},
            timeout=httpx.Timeout(float(os.getenv('TOOLS_VL_TIMEOUT_SEC','120')),connect=10))
        response.raise_for_status();payload=response.json()
        answer=(payload['choices'][0]['message'].get('content') or '').strip()
        if not answer:raise RuntimeError('Vision returned an empty answer')
        return {'answer':answer,'model':model,'usage':payload.get('usage',{})}
    started = time.perf_counter()
    try:
        result=idx.cached('vision-'+token,unit,request)
    except Exception as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        get_logger("tools.vision").warning("unit=%s model=%s ms=%d error=%s: %s", unit, model, elapsed, type(exc).__name__, exc)
        raise
    elapsed = int((time.perf_counter() - started) * 1000)
    usage = result.get("usage") or {}
    get_logger("tools.vision").info(
        "unit=%s model=%s cached=%s ms=%d tokens=%s",
        unit, model, not fresh, elapsed, usage.get("total_tokens", 0),
    )
    return {**result,'cached':not fresh,'usage':usage if fresh else {}}
