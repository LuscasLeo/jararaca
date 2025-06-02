import time
from urllib.parse import urljoin

import httpx

from jararaca.rpc.http.decorators import (
    HttpRPCAsyncBackend,
    HttpRPCRequest,
    HttpRPCResponse,
    RPCRequestNetworkError,
    TimeoutException,
)


class HTTPXHttpRPCAsyncBackend(HttpRPCAsyncBackend):

    def __init__(self, prefix_url: str = "", default_timeout: float = 30.0):
        self.prefix_url = prefix_url
        self.default_timeout = default_timeout

    async def request(
        self,
        request: HttpRPCRequest,
    ) -> HttpRPCResponse:

        start_time = time.time()

        # Prepare timeout
        timeout = (
            request.timeout if request.timeout is not None else self.default_timeout
        )

        # Prepare request kwargs
        request_kwargs = {
            "method": request.method,
            "url": urljoin(self.prefix_url, request.url),
            "headers": request.headers,
            "params": request.query_params,
            "timeout": timeout,
        }

        # Handle different content types
        if request.form_data and request.files:
            # Multipart form data with files
            request_kwargs["data"] = request.form_data
            request_kwargs["files"] = request.files
        elif request.form_data:
            # Form data only
            request_kwargs["data"] = request.form_data
        elif request.body:
            # Raw body content
            request_kwargs["content"] = request.body

        async with httpx.AsyncClient() as client:
            try:
                response = await client.request(**request_kwargs)  # type: ignore[arg-type]

                elapsed_time = time.time() - start_time

                return HttpRPCResponse(
                    status_code=response.status_code,
                    data=response.content,
                    headers=dict(response.headers),
                    elapsed_time=elapsed_time,
                )
            except httpx.TimeoutException as err:
                raise TimeoutException(f"Request timed out: {err}") from err
            except httpx.NetworkError as err:
                raise RPCRequestNetworkError(
                    request=request, backend_request=err.request
                ) from err
