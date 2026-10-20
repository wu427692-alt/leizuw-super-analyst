"""Authenticated, cached AI-news reads; no public upstream proxy."""
from typing import Literal
import os

from fastapi import APIRouter, Query, Response
from src.services.ai_news_service import get_ai_news_service

router = APIRouter()


@router.get("/feed")
def feed(response: Response, view: Literal["hot", "latest"] = "hot",
         q: str = Query("", max_length=200), topic: str = Query("", max_length=40),
         source: str = Query("", max_length=40), window: Literal["24h", "7d", "30d"] = "7d",
         page: int = Query(1, ge=1, le=10000), page_size: int = Query(24, ge=1, le=100)):
    response.headers["Cache-Control"] = "private, no-store"
    service = get_ai_news_service()
    # Some production releases intentionally disable all application lifespan
    # jobs. Start ONLY this cheap RSS collector, never any investment workers.
    if os.getenv("AI_NEWS_AUTO_START", "true").lower() in {"1", "true", "yes", "on"}:
        service.start()
    return service.feed(view=view, q=q, topic=topic, source=source, window=window,
                        page=page, page_size=page_size)
