import sys
import os
from pathlib import Path
from loguru import logger
from app.core.config import BASE_DIR, config

def setup_logger():
    # Clear any default loggers configured by loguru
    logger.remove()

    # Determine log folder and path from settings
    log_dir = Path(config.log_dir)
    if not log_dir.is_absolute():
        log_dir = BASE_DIR / log_dir
        
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "omie_automation.log"

    # In --windowed PyInstaller builds, sys.stderr can be None.
    if sys.stderr is not None:
        logger.add(
            sys.stderr,
            format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            level=os.getenv("LOG_LEVEL", "INFO"),
            colorize=True
        )

    # Configure rotating file sink. On Windows, antivirus/indexers or another
    # process can briefly lock the file; keep the application usable.
    file_format = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}"
    try:
        logger.add(
            str(log_file),
            format=file_format,
            level="INFO",
            rotation="10 MB",
            retention="30 days",
            encoding="utf-8"
        )
    except PermissionError as e:
        fallback_log_file = log_dir / f"omie_automation_{os.getpid()}.log"
        try:
            logger.add(
                str(fallback_log_file),
                format=file_format,
                level="INFO",
                rotation="10 MB",
                retention="7 days",
                encoding="utf-8"
            )
            logger.warning(f"Could not open default log file '{log_file}': {e}. Using '{fallback_log_file}'.")
        except Exception as fallback_error:
            logger.warning(
                f"Could not open log files in '{log_dir}': {fallback_error}. Continuing with console logging only."
            )

    logger.info("Logging configured successfully.")

# Initialize logging immediately upon import
setup_logger()
