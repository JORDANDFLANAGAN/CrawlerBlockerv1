from fastapi import Request

from tarpit.access_log import AccessLogWriter
from tarpit.markov import MarkovChain
from tarpit.settings import Settings, get_settings


def get_chain(request: Request) -> MarkovChain:
    return request.app.state.chain


def get_log_writer(request: Request) -> AccessLogWriter:
    return request.app.state.log_writer


def get_settings_dep() -> Settings:
    return get_settings()
