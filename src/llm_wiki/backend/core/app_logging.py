import logging

from llm_wiki.common import logging_config

LOG_DIR = logging_config.setup_logging()
logger = logging.getLogger("log_wiki.server")
logger.setLevel(logging.INFO)
access_logger = logging.getLogger("log_wiki.access")
access_logger.setLevel(logging.INFO)
