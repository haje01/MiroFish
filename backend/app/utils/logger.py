"""
로그 설정 모듈
콘솔 및 파일에 동시에 출력하는 통합 로그 관리 제공
"""

import os
import sys
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler


def _ensure_utf8_stdout():
    """
    stdout/stderr가 UTF-8 인코딩을 사용하도록 보장
    Windows 콘솔에서 한글 깨짐 문제 해결
    """
    if sys.platform == 'win32':
        # Windows에서 표준 출력을 UTF-8로 재설정
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')


# 로그 디렉토리
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logs')


def setup_logger(name: str = 'mirofish', level: int = logging.DEBUG) -> logging.Logger:
    """
    로거 설정

    Args:
        name: 로거 이름
        level: 로그 레벨

    Returns:
        설정된 로거
    """
    # 로그 디렉토리 존재 확인
    os.makedirs(LOG_DIR, exist_ok=True)

    # 로거 생성
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 로그가 루트 logger로 전파되지 않도록 방지하여 중복 출력 방지
    logger.propagate = False

    # 이미 핸들러가 있으면 중복 추가하지 않음
    if logger.handlers:
        return logger

    # 로그 형식
    detailed_formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    simple_formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%H:%M:%S'
    )

    # 1. 파일 핸들러 - 상세 로그 (날짜별 파일명, 로테이션 포함)
    log_filename = datetime.now().strftime('%Y-%m-%d') + '.log'
    file_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, log_filename),
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed_formatter)

    # 2. 콘솔 핸들러 - 간결한 로그 (INFO 이상)
    # Windows에서 UTF-8 인코딩을 사용하여 한글 깨짐 방지
    _ensure_utf8_stdout()
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(simple_formatter)

    # 핸들러 추가
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


def get_logger(name: str = 'mirofish') -> logging.Logger:
    """
    로거 가져오기 (없으면 생성)

    Args:
        name: 로거 이름

    Returns:
        로거 인스턴스
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        return setup_logger(name)
    return logger


# 기본 로거 생성
logger = setup_logger()


# 편의 메서드
def debug(msg, *args, **kwargs):
    logger.debug(msg, *args, **kwargs)

def info(msg, *args, **kwargs):
    logger.info(msg, *args, **kwargs)

def warning(msg, *args, **kwargs):
    logger.warning(msg, *args, **kwargs)

def error(msg, *args, **kwargs):
    logger.error(msg, *args, **kwargs)

def critical(msg, *args, **kwargs):
    logger.critical(msg, *args, **kwargs)
