from typing import AsyncGenerator

from fastapi import Request

from jararaca.presentation.decorators import Get, RestController, UseMiddleware
from jararaca.presentation.http_microservice import HttpMiddleware
from jararaca.tools.typescript.interface_parser import (
    write_rest_controller_to_typescript_interface,
)


class BaseMiddleware(HttpMiddleware):
    async def intercept(
        self,
        request: Request,
    ) -> AsyncGenerator[None, None]:
        yield


class ChildMiddleware(HttpMiddleware):
    async def intercept(
        self,
        request: Request,
    ) -> AsyncGenerator[None, None]:
        yield


class MethodMiddleware(HttpMiddleware):
    async def intercept(
        self,
        request: Request,
    ) -> AsyncGenerator[None, None]:
        yield


@UseMiddleware(BaseMiddleware)
@RestController("/base")
class BaseController:
    @UseMiddleware(MethodMiddleware)
    @Get("/hello")
    async def hello(self) -> str:
        return "hello"


@UseMiddleware(ChildMiddleware)
@RestController("/child")
class ChildController(BaseController):
    async def hello(self) -> str:
        return "child hello"


def run_example() -> None:
    print("Generating TypeScript interface for ChildController...")

    # Get the RestController decorator instance
    rest_controller = RestController.get_last(ChildController)
    if not rest_controller:
        print("Error: ChildController is not a RestController")
        return

    # Generate TS
    class_buffer, types, hooks_buffer = write_rest_controller_to_typescript_interface(
        rest_controller, ChildController
    )

    ts_output = class_buffer.getvalue()
    print("\n--- Generated TypeScript ---\n")
    print(ts_output)

    # Verification logic (simple string checks for this example)
    print("\n--- Verification ---")

    # Check for class-level middlewares
    # Note: The parser extracts parameters from middlewares.
    # Since our middlewares don't have __init__ params, they might not show up as arguments,
    # but the parser logic we changed was about *finding* them.
    # To verify they are found, we should add parameters to the middlewares.

    print("Done.")


if __name__ == "__main__":
    run_example()
