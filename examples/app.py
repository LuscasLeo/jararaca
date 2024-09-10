from fastapi import Request
from pydantic import BaseModel
from jararaca import Microservice, RestController, Get
from jararaca.observability.decorators import TracedFunc
from jararaca.presentation.http_microservice import HttpMicroservice
from jararaca.presentation.server import create_http_server


class HelloResponse(BaseModel):
    message: str


@RestController("/my")
class MyController:

    @TracedFunc("hello")
    @Get("/hello")
    async def hello(self, request: Request) -> HelloResponse:
        return HelloResponse(
            message="Hello %s" % request.query_params.get("name") or "World"
        )


app = Microservice(
    controllers=[MyController],
)


http_app = create_http_server(HttpMicroservice(app))
