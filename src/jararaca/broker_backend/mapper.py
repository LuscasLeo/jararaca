from jararaca.broker_backend import MessageBrokerBackend


def get_message_broker_backend_from_url(url: str) -> MessageBrokerBackend:
    """
    Factory function to create a message broker backend instance from a URL.
    Currently, only Redis is supported.
    """
    if (
        url.startswith("redis://")
        or url.startswith("rediss://")
        or url.startswith("redis-socket://")
        or url.startswith("rediss+socket://")
    ):
        from jararaca.broker_backend.redis_broker_backend import (
            RedisMessageBrokerBackend,
        )

        return RedisMessageBrokerBackend(url)
    else:
        raise ValueError(f"Unsupported message broker backend URL: {url}")
