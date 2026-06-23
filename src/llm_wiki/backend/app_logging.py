import logging

from llm_wiki.common import logging_config  # noqa: E402

from .config import ROOT  # noqa: F401

LOG_DIR = logging_config.setup_logging()
logger = logging.getLogger("log_wiki.server")
logger.setLevel(logging.INFO)
access_logger = logging.getLogger("log_wiki.access")
