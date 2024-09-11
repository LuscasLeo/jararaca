from urllib.parse import urljoin

import httpx

from jararaca.rpc.http.decorators import (
    HttpRPCAsyncBackend,
    HttpRPCRequest,
    HttpRPCResponse,
    RPCRequestNetworkError,
)


class HTTPXHttpRPCAsyncBackend(HttpRPCAsyncBackend):

    def __init__(self, prefix_url: str = ""):
        self.prefix_url = prefix_url

    async def request(
        self,
        request: HttpRPCRequest,
    ) -> HttpRPCResponse:

        async with httpx.AsyncClient() as client:

            try:
                response = await client.request(
                    method=request.method,
                    url=urljoin(self.prefix_url, request.url),
                    headers=request.headers,
                    params=request.query_params,
                    content=request.body,
                )

                return HttpRPCResponse(
                    status_code=response.status_code,
                    data=response.content,
                )
            except httpx.NetworkError:
                raise RPCRequestNetworkError("Network error")
