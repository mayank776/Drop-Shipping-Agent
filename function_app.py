import logging

import azure.durable_functions as df
import azure.functions as func

from foundation.runtime import services
from foundation.webhook import SECRET_HEADER

app = df.DFApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@app.route(route="telegram/webhook", methods=["POST"])
@app.durable_client_input(client_name="client")
async def telegram_webhook(req: func.HttpRequest, client: df.DurableOrchestrationClient) -> func.HttpResponse:
    # Anonymous by design: Telegram can't send a function key. The secret-token header
    # checked in TelegramWebhook.handle is the authentication.
    result = services().webhook.handle(req.headers.get(SECRET_HEADER), req.get_body())
    if result.event:
        try:
            await client.raise_event(result.event.instance_id, result.event.name, result.event.data)
        except Exception:
            # The decision is stored; the orchestration can still read it from the store.
            logging.exception("Failed to raise %s on %s", result.event.name, result.event.instance_id)
    return func.HttpResponse(status_code=result.status)


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("ok")
