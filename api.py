from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional
import yaml
import os
from services.image_search import ImageSearch

from config.settings import Config
from config.api_settings import load_config

import uvicorn

from middleware.protected_mode import ProtectedModeMiddleware
from middleware.rate_limiter import RateLimitMiddleware

# 初始化核心组件
config = Config()
search_engine = ImageSearch()
api_config = load_config() 
app = FastAPI(title="VVQuest API")

# 注册保护模式中间件
if api_config.protected_mode:
    app.add_middleware(ProtectedModeMiddleware, config=api_config)

if api_config.rate_limit.enabled:
    app.add_middleware(RateLimitMiddleware, config=api_config)

# 数据模型定义
class SearchRequest(BaseModel):
    query: str
    n_results: int = 5

class ConfigUpdate(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None

class ModelDownloadRequest(BaseModel):
    model_id: str

# API 端点
@app.post("/search")
async def search_images(request: SearchRequest):
    """执行图片搜索"""
    try:
        results = search_engine.search(
            request.query,
            request.n_results,
            config.api.embedding_models.api_key
        )
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate-cache")
async def generate_cache(background_tasks: BackgroundTasks):
    """触发缓存生成（后台任务）"""
    if not search_engine.has_cache():
        background_tasks.add_task(search_engine.generate_cache)
        return {"message": "Cache generation started"}
    return {"message": "Cache already exists"}

@app.get("/config")
async def get_config():
    """获取当前配置"""
    return {
        "mode": search_engine.get_mode(),
        "model": search_engine.get_model_name(),
        "api_key": search_engine.embedding_service.api_key,
        "base_url": search_engine.embedding_service.base_url
    }

@app.put("/api-config")
async def update_config(update: ConfigUpdate):
    """更新API配置"""
    try:
        if update.api_key:
            search_engine.embedding_service.api_key = update.api_key
        if update.base_url:
            search_engine.embedding_service.base_url = update.base_url
        config.save()
        return {"message": "Config updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/download-model")
async def download_model(req: ModelDownloadRequest):
    """下载指定模型"""
    try:
        if search_engine.embedding_service.is_model_downloaded(req.model_id):
            return {"message": "Model already exists"}
        
        search_engine.download_model(req.model_id)
        return {"message": "Model download completed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/models")
async def list_models():
    """获取可用模型列表"""
    models = []
    for model_id, info in Config().models.embedding_models.items():
        models.append({
            "id": model_id,
            "performance": info.performance,
            "downloaded": search_engine.embedding_service.is_model_downloaded(model_id)
        })
    return {"models": models}

@app.put("/mode/{mode}")
async def switch_mode(mode: str):
    """切换运行模式"""
    if mode not in ['api', 'local']:
        raise HTTPException(status_code=400, detail="Invalid mode")
    
    try:
        search_engine.set_mode(mode)
        return {"message": f"Switched to {mode} mode"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/model/{model_id}")
async def switch_model(model_id: str):
    """切换本地模型"""
    try:
        # 增加模式验证
        if search_engine.get_mode() != 'local':
            raise HTTPException(
                status_code=400, 
                detail="Must switch to local mode before changing model"
            )

        # 验证模型是否存在
        if model_id not in Config().models.embedding_models:
            raise HTTPException(
                status_code=404,
                detail=f"Model {model_id} not defined in configuration"
            )

        # 验证模型文件
        if not search_engine.embedding_service.is_model_downloaded(model_id):
            raise HTTPException(
                status_code=412,  # 前置条件失败
                detail=f"Model {model_id} not downloaded yet"
            )

        # 为什么mode和model设置放一起了啊......
        search_engine.set_mode('local', model_id)

        return {"message": f"Successfully switched to model {model_id} "}
    
    except HTTPException as he:
        raise he  # 传递已处理的错误
    except Exception as e:
        import traceback
        traceback.print_exc()  # 打印完整堆栈
        raise HTTPException(
            status_code=500,
            detail=f"Model loading failed: {str(e)}"
        )

if __name__ == "__main__":
    if api_config.mode == 'local':
        search_engine.embedding_service.mode = 'local'
        if search_engine.embedding_service.is_model_downloaded(api_config.model)==False:
            search_engine.embedding_service.selected_model = api_config.model
            search_engine.download_model()
        search_engine.set_mode('local', api_config.model)
    elif api_config.mode == 'api':
        search_engine.embedding_service.mode = 'api'
        search_engine.embedding_service.api_key = api_config.api_mode_config.default_api_key
        search_engine.embedding_service.base_url = api_config.api_mode_config.default_base_url
        search_engine.set_mode('api')
    if api_config.generate_cache:
        search_engine.generate_cache()

    uvicorn.run(app, host="0.0.0.0", port=8000)