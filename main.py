"""Uvicorn entrypoint — keeps `uvicorn main:app` working after package layout."""

from app.web.main import app

__all__ = ["app"]
