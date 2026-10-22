from fastapi import Request

from tarpit.access_log import AccessLogWriter
from tarpit.detection.log import DetectionLogWriter
from tarpit.detection.state import DetectionStateStore
from tarpit.markov import MarkovChain
from tarpit.settings import Settings, get_settings


def get_chain(request: Request) -> MarkovChain:
    return request.app.state.chain


def get_log_writer(request: Request) -> AccessLogWriter:
    return request.app.state.log_writer


def get_settings_dep() -> Settings:
    return get_settings()


def get_detection_store(request: Request) -> DetectionStateStore:
    return request.app.state.detection_store


def get_detection_log_writer(request: Request) -> DetectionLogWriter:
    return request.app.state.detection_log_writer
