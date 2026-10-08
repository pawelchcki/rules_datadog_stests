"""Evidence-bound SDK defects not yet declared in the pinned upstream manifest."""

BAGGAGE_CASE = "portable_cases.HTTP.baggage_tags[0]"
BAGGAGE_SOURCE_SHA256 = "9e713b387365e0756615ff1890cca22264339134eddd66ed442d4b055ca100aa"
BAGGAGE_REASON = (
    "Python 4.15.5 aiohttp propagates baggage but omits configured baggage.* tags on the server span."
)


def local_failure_reason(name, sdk_version, wire, failure, captures, operations, source_sha256):
    if (name, sdk_version, wire, source_sha256) != (
            BAGGAGE_CASE, "4.15.5", "v0.4", BAGGAGE_SOURCE_SHA256):
        return None
    if failure != dict(type="KeyError", file="portable_cases.py", function="baggage_tags",
                       line=61, message="'baggage.user.id'"):
        return None
    requests = [operation for operation in operations if operation["operation"] == "http_request"]
    if len(requests) != 1 or requests[0]["arguments"] != dict(
            status=200, query={}, headers={"baggage": "user.id=doggo,session.id=controlled-session,other=private"}):
        return None
    receipt = requests[0]["result"]
    baggage = {"user.id": "doggo", "session.id": "controlled-session", "other": "private"}
    if receipt.get("extracted_baggage") != baggage or receipt.get("target_baggage") != baggage:
        return None
    records = [record for snapshot in captures for record in snapshot]
    if not records or any(record["payload"]["wire_version"] != wire for record in records):
        return None
    spans = {span["span_id"]: span for record in records for trace in record["payload"]["traces"] for span in trace}
    control = spans.get(receipt["control"]["span_id"], {})
    server = spans.get(receipt["target"]["span_id"], {})
    client = spans.get(server.get("parent_id"), {})
    trace_id = receipt["control"]["trace_id"] & ((1 << 64) - 1)
    if (receipt["target"]["trace_id"] != receipt["control"]["trace_id"]
            or any(span.get("trace_id") != trace_id for span in (control, client, server))
            or client.get("parent_id") != control.get("span_id")
            or server.get("meta", {}).get("component") != "aiohttp"
            or client.get("meta", {}).get("component") != "aiohttp_client"
            or any(span.get("meta", {}).get("http.status_code") != "200" for span in (client, server))):
        return None
    if any(control.get("meta", {}).get("baggage." + key) != baggage[key] for key in ("user.id", "session.id")):
        return None
    if any("baggage." + key in server.get("meta", {}) for key in baggage):
        return None
    return BAGGAGE_REASON
