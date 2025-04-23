from pydantic import BaseModel


class DelayedMessageData(BaseModel):
    message_topic: str
    dispatch_time: int
    payload: bytes
