from typing import ClassVar

from pydantic import BaseModel


class WebSocketMessageBase(BaseModel):

    MESSAGE_ID: ClassVar[str] = "__UNSET__"
