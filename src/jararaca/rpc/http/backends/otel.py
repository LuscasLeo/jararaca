from opentelemetry import baggage, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from jararaca.rpc.http.decorators import HttpRPCRequest, RequestMiddleware


class TracedRequestMiddleware(RequestMiddleware):

    def on_request(self, request: HttpRPCRequest) -> HttpRPCRequest:

        span = trace.get_current_span()

        if span is not None:
            ctx = baggage.set_baggage("hello", "world")
            headers: dict[str, str] = {}
            W3CBaggagePropagator().inject(headers, ctx)
            TraceContextTextMapPropagator().inject(headers, ctx)

            for key, value in headers.items():
                request.headers.append((key, value))

        return request
