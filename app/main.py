"""Router API: classify a prompt into a route (model + config)."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from app.classifier import make_classifier
from app.config import load_routes

state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["cfg"] = load_routes()
    state["classifier"] = make_classifier(state["cfg"])
    yield


app = FastAPI(lifespan=lifespan)


class RouteRequest(BaseModel):
    prompt: str


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/route")
def route(req: RouteRequest):
    return {**state["classifier"].classify(req.prompt).as_dict(), "cfg_version": state["cfg"].version}
