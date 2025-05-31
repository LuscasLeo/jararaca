import asyncio
import logging
import random
from functools import wraps
from typing import Awaitable, Callable, Optional, ParamSpec, TypeVar

logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


class RetryConfig:
    """Configuration for the retry mechanism."""

    def __init__(
        self,
        max_retries: int = 5,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        backoff_factor: float = 2.0,
        jitter: bool = True,
    ):
        """
        Initialize retry configuration.

        Args:
            max_retries: Maximum number of retry attempts (default: 5)
            initial_delay: Initial delay in seconds between retries (default: 1.0)
            max_delay: Maximum delay in seconds between retries (default: 60.0)
            backoff_factor: Multiplier for the delay after each retry (default: 2.0)
            jitter: Whether to add randomness to the delay to prevent thundering herd (default: True)
        """
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor
        self.jitter = jitter


E = TypeVar("E", bound=Exception)


async def retry_with_backoff(
    fn: Callable[[], Awaitable[T]],
    # args: P.args,
    # kwargs: P.kwargs,
    retry_config: Optional[RetryConfig] = None,
    on_retry_callback: Optional[Callable[[int, E, float], None]] = None,
    retry_exceptions: tuple[type[E], ...] = (),
) -> T:
    """
    Execute a function with exponential backoff retry mechanism.

    Args:
        fn: The async function to execute with retry
        *args: Arguments to pass to the function
        retry_config: Configuration for the retry mechanism
        on_retry_callback: Optional callback function called on each retry with retry count, exception, and next delay
        retry_exceptions: Tuple of exception types that should trigger a retry
        **kwargs: Keyword arguments to pass to the function

    Returns:
        The result of the function if successful

    Raises:
        The last exception encountered if all retries fail
    """
    if retry_config is None:
        retry_config = RetryConfig()

    last_exception = None
    delay = retry_config.initial_delay

    for retry_count in range(retry_config.max_retries + 1):
        try:
            return await fn()
        except retry_exceptions as e:
            last_exception = e

            if retry_count >= retry_config.max_retries:
                logger.error(
                    f"Max retries ({retry_config.max_retries}) exceeded: {str(e)}"
                )
                raise

            # Calculate next delay with exponential backoff
            if retry_count > 0:  # Don't increase delay on the first failure
                delay = min(delay * retry_config.backoff_factor, retry_config.max_delay)

            # Apply jitter if configured (±25% randomness)
            if retry_config.jitter:
                jitter_amount = delay * 0.25
                delay = delay + random.uniform(-jitter_amount, jitter_amount)
                # Ensure delay doesn't go negative due to jitter
                delay = max(delay, 0.1)

            logger.warning(
                f"Retry {retry_count+1}/{retry_config.max_retries} after error: {str(e)}. "
                f"Retrying in {delay:.2f}s"
            )

            # Call the optional retry callback if provided
            if on_retry_callback:
                on_retry_callback(retry_count, e, delay)

            await asyncio.sleep(delay)

    # This should never be reached with the current implementation
    if last_exception:
        raise last_exception
    raise RuntimeError("Unexpected error in retry logic")


def with_retry(
    retry_config: Optional[RetryConfig] = None,
    retry_exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """
    Decorator to wrap an async function with retry logic.

    Args:
        retry_config: Configuration for the retry mechanism
        retry_exceptions: Tuple of exception types that should trigger a retry

    Returns:
        Decorated function with retry mechanism
    """

    def decorator(fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            return await retry_with_backoff(
                lambda: fn(*args, **kwargs),
                retry_config=retry_config,
                retry_exceptions=retry_exceptions,
            )

        return wrapper

    return decorator
