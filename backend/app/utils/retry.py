"""
API 호출 재시도 메커니즘
LLM 등 외부 API 호출의 재시도 로직 처리용
"""

import time
import random
import functools
from typing import Callable, Any, Optional, Type, Tuple
from ..utils.logger import get_logger

logger = get_logger('mirofish.retry')


def retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[Exception, int], None]] = None
):
    """
    지수 백오프가 있는 재시도 데코레이터

    Args:
        max_retries: 최대 재시도 횟수
        initial_delay: 초기 지연 (초)
        max_delay: 최대 지연 (초)
        backoff_factor: 백오프 인수
        jitter: 랜덤 지터 추가 여부
        exceptions: 재시도할 예외 타입
        on_retry: 재시도 시 콜백 함수 (exception, retry_count)

    Usage:
        @retry_with_backoff(max_retries=3)
        def call_llm_api():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            delay = initial_delay

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)

                except exceptions as e:
                    last_exception = e

                    if attempt == max_retries:
                        logger.error(f"함수 {func.__name__}가 {max_retries}번 재시도 후 실패: {str(e)}")
                        raise

                    # 지연 계산
                    current_delay = min(delay, max_delay)
                    if jitter:
                        current_delay = current_delay * (0.5 + random.random())

                    logger.warning(
                        f"함수 {func.__name__} {attempt + 1}번째 시도 실패: {str(e)}, "
                        f"{current_delay:.1f}초 후 재시도..."
                    )

                    if on_retry:
                        on_retry(e, attempt + 1)

                    time.sleep(current_delay)
                    delay *= backoff_factor

            raise last_exception

        return wrapper
    return decorator


def retry_with_backoff_async(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[Exception, int], None]] = None
):
    """
    비동기 버전의 재시도 데코레이터
    """
    import asyncio

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            delay = initial_delay

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)

                except exceptions as e:
                    last_exception = e

                    if attempt == max_retries:
                        logger.error(f"비동기 함수 {func.__name__}가 {max_retries}번 재시도 후 실패: {str(e)}")
                        raise

                    current_delay = min(delay, max_delay)
                    if jitter:
                        current_delay = current_delay * (0.5 + random.random())

                    logger.warning(
                        f"비동기 함수 {func.__name__} {attempt + 1}번째 시도 실패: {str(e)}, "
                        f"{current_delay:.1f}초 후 재시도..."
                    )

                    if on_retry:
                        on_retry(e, attempt + 1)

                    await asyncio.sleep(current_delay)
                    delay *= backoff_factor

            raise last_exception

        return wrapper
    return decorator


class RetryableAPIClient:
    """
    재시도 가능한 API 클라이언트 래퍼
    """

    def __init__(
        self,
        max_retries: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 30.0,
        backoff_factor: float = 2.0
    ):
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor

    def call_with_retry(
        self,
        func: Callable,
        *args,
        exceptions: Tuple[Type[Exception], ...] = (Exception,),
        **kwargs
    ) -> Any:
        """
        함수 호출을 실행하고 실패 시 재시도

        Args:
            func: 호출할 함수
            *args: 함수 파라미터
            exceptions: 재시도할 예외 타입
            **kwargs: 함수 키워드 파라미터

        Returns:
            함수 반환값
        """
        last_exception = None
        delay = self.initial_delay

        for attempt in range(self.max_retries + 1):
            try:
                return func(*args, **kwargs)

            except exceptions as e:
                last_exception = e

                if attempt == self.max_retries:
                    logger.error(f"API 호출이 {self.max_retries}번 재시도 후 실패: {str(e)}")
                    raise

                current_delay = min(delay, self.max_delay)
                current_delay = current_delay * (0.5 + random.random())

                logger.warning(
                    f"API 호출 {attempt + 1}번째 시도 실패: {str(e)}, "
                    f"{current_delay:.1f}초 후 재시도..."
                )

                time.sleep(current_delay)
                delay *= self.backoff_factor

        raise last_exception

    def call_batch_with_retry(
        self,
        items: list,
        process_func: Callable,
        exceptions: Tuple[Type[Exception], ...] = (Exception,),
        continue_on_failure: bool = True
    ) -> Tuple[list, list]:
        """
        배치 호출 및 각 실패 항목 개별 재시도

        Args:
            items: 처리할 항목 목록
            process_func: 처리 함수, 단일 item을 파라미터로 받음
            exceptions: 재시도할 예외 타입
            continue_on_failure: 단일 항목 실패 후 다른 항목 계속 처리 여부

        Returns:
            (성공 결과 목록, 실패 항목 목록)
        """
        results = []
        failures = []

        for idx, item in enumerate(items):
            try:
                result = self.call_with_retry(
                    process_func,
                    item,
                    exceptions=exceptions
                )
                results.append(result)

            except Exception as e:
                logger.error(f"{idx + 1}번째 항목 처리 실패: {str(e)}")
                failures.append({
                    "index": idx,
                    "item": item,
                    "error": str(e)
                })

                if not continue_on_failure:
                    raise

        return results, failures
