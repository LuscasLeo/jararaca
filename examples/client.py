from typing import Protocol, runtime_checkable

from examples.schemas import HelloResponse
from jararaca.rpc.http.decorators import Body, Get, Post, Query, RestClient


# @runtime_checkable
@RestClient("/my")
@runtime_checkable
class HelloRPC(Protocol):

    @Get("/hello")
    @Query("gather")
    async def my_hello(self, gather: bool) -> HelloResponse: ...

    @Get("/ping")
    async def ping(self) -> HelloResponse: ...

    @Post("/create-response")
    @Body("response")
    async def create_response(self, response: HelloResponse) -> HelloResponse: ...
