import logging

from .config import ROOT  # noqa: F401

import logging_config  # noqa: E402

LOG_DIR = logging_config.setup_logging()
logger = logging.getLogger("log_wiki.server")
logger.setLevel(logging.INFO)
access_logger = logging.getLogger("log_wiki.access")
