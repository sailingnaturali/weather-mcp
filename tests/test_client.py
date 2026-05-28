import httpx
import respx

from weather_mcp.client import RateLimitedClient


async def test_limiter_sleeps_when_window_full():
    clock = {"t": 0.0}
    slept = []

    def fake_now():
        return clock["t"]

    async def fake_sleep(secs):
        slept.append(secs)
        clock["t"] += secs

    client = RateLimitedClient(max_calls=2, period=60.0, now=fake_now, sleep=fake_sleep)

    with respx.mock:
        respx.get("https://example.test/x").mock(return_value=httpx.Response(200, json={"ok": True}))
        await client.get("https://example.test/x")
        await client.get("https://example.test/x")
        await client.get("https://example.test/x")

    assert slept == [60.0]
    await client.aclose()


async def test_client_passes_headers_through():
    """Stormglass needs an Authorization header."""
    seen = {}

    with respx.mock:
        def capture(request):
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"ok": True})

        respx.get("https://example.test/h").mock(side_effect=capture)
        client = RateLimitedClient()
        await client.get("https://example.test/h", headers={"Authorization": "sg-key-123"})
        await client.aclose()

    assert seen["auth"] == "sg-key-123"
