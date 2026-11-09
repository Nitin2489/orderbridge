"""/api/v1/captures against the dedicated test database.

Sign-in uses the real OTP flow (InMemoryOtpDelivery records the codes). The
seed provides the only data; orders come from submitting captures. Setup is
committed through the session fixture's savepoint mode, so everything is
still rolled back after each test.
"""

import json
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.database import get_db
from app.main import create_app
from app.models import Order, OrderCapture, Product, Retailer, RetailerProductAlias
from app.services import capture_service
from app.services.gemini_interpreter import GeminiInterpreter
from app.services.otp_delivery import InMemoryOtpDelivery
from app.services.retailer_order_service import OrderLineInput, submit_order
from seed import seed_all

DEMO = (
    "bhaiya 5 peti biscuit chhota, 2 peti washing powder 1 kilo, "
    "chai patti 250 wala 1 peti"
)
AUTH = "/api/v1/auth"
CAPTURES = "/api/v1/captures"
TRUSTED_ORIGIN = "http://localhost:3000"
SHARMA_PHONE = "+919000000001"
GUPTA_PHONE = "+919000000002"
DESK_PHONE = "+919000000010"


def make_settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        app_env="testing",
        database_url="postgresql+psycopg://unused:unused@localhost:5432/unused",
        auth_secret_key="capture-test-secret-0123456789abcdef012",
        auth_session_cookie_secure=False,
        auth_debug_otp=False,
        frontend_origins=[TRUSTED_ORIGIN],
        **overrides,
    )


@pytest.fixture
def app(db_session):
    seed_all(db_session)
    db_session.commit()
    application = create_app(make_settings(), otp_delivery=InMemoryOtpDelivery())

    def override_get_db():
        yield db_session

    application.dependency_overrides[get_db] = override_get_db
    return application


def new_client(app, *, raise_server_exceptions: bool = True) -> TestClient:
    return TestClient(
        app,
        base_url="http://testserver",
        headers={"Origin": TRUSTED_ORIGIN},
        raise_server_exceptions=raise_server_exceptions,
    )


def login(client: TestClient, phone: str) -> TestClient:
    requested = client.post(f"{AUTH}/request-otp", json={"phone_number": phone})
    assert requested.status_code == 200, requested.text
    code = client.app.state.otp_delivery.latest_code(phone)
    verified = client.post(
        f"{AUTH}/verify-otp", json={"phone_number": phone, "code": code}
    )
    assert verified.status_code == 200, verified.text
    return client


@pytest.fixture
def desk(app):
    with new_client(app) as client:
        yield login(client, DESK_PHONE)


@pytest.fixture
def sharma(app):
    with new_client(app) as client:
        yield login(client, SHARMA_PHONE)


@pytest.fixture
def gupta(app):
    with new_client(app) as client:
        yield login(client, GUPTA_PHONE)


def retailer_id(db_session, name: str) -> str:
    return str(
        db_session.scalar(select(Retailer.id).where(Retailer.business_name == name))
    )


def product_id(db_session, sku: str) -> str:
    return str(db_session.scalar(select(Product.id).where(Product.sku == sku)))


def create(
    client, db_session=None, message=DEMO, *, for_retailer=None, request_id=None
):
    body = {"client_request_id": str(request_id or uuid4()), "message": message}
    if for_retailer is not None:
        body["retailer_id"] = retailer_id(db_session, for_retailer)
    return client.post(CAPTURES, json=body)


def review(client, capture, line_number, **decision):
    return client.post(
        f"{CAPTURES}/{capture['id']}/lines/{line_number}",
        json={"expected_version": capture["version"], **decision},
    )


def ready_demo(client, db_session, *, for_retailer="Sharma General Store"):
    capture = create(client, db_session, for_retailer=for_retailer).json()
    for number, sku, quantity in ((1, "SKU-BS-075", 5), (2, "SKU-DT-1KG", 2)):
        response = review(
            client,
            capture,
            number,
            action="include",
            product_id=product_id(db_session, sku),
            unit="case",
            quantity=quantity,
        )
        assert response.status_code == 200, response.text
        capture = response.json()
    response = review(
        client, capture, 3, action="exclude", reason="Tea is out of stock."
    )
    assert response.status_code == 200, response.text
    capture = response.json()
    assert capture["status"] == "ready"
    return capture


def submit(client, capture, *, request_id=None):
    return client.post(
        f"{CAPTURES}/{capture['id']}/submit",
        json={
            "expected_version": capture["version"],
            "client_request_id": str(request_id or uuid4()),
        },
    )


def count(db_session, model) -> int:
    return db_session.scalar(select(func.count()).select_from(model))


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


def test_every_route_requires_sign_in(app):
    base = f"{CAPTURES}/{uuid4()}"
    requests = [
        ("post", CAPTURES, {"client_request_id": str(uuid4()), "message": DEMO}),
        ("get", CAPTURES, None),
        ("get", base, None),
        ("post", f"{base}/lines/1", {"expected_version": 1, "action": "reset"}),
        ("post", f"{base}/discard", {"expected_version": 1, "reason": "Duplicate"}),
        (
            "post",
            f"{base}/submit",
            {"expected_version": 1, "client_request_id": str(uuid4())},
        ),
    ]
    with new_client(app) as client:
        for method, path, body in requests:
            kwargs = {"json": body} if body is not None else {}
            assert getattr(client, method)(path, **kwargs).status_code == 401, path


def test_changes_require_a_trusted_origin(sharma, db_session):
    response = sharma.post(
        CAPTURES,
        json={"client_request_id": str(uuid4()), "message": DEMO},
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert count(db_session, OrderCapture) == 0


def test_retailer_captures_only_for_itself(sharma, db_session):
    own = create(sharma)
    assert own.status_code == 201, own.text
    assert own.json()["retailer"]["business_name"] == "Sharma General Store"

    other = create(sharma, db_session, for_retailer="Gupta Kirana Store")
    assert other.status_code == 422


def test_desk_must_name_the_retailer(desk, db_session):
    assert create(desk).status_code == 422
    created = create(desk, db_session, for_retailer="Gupta Kirana Store")
    assert created.status_code == 201
    assert created.json()["retailer"]["business_name"] == "Gupta Kirana Store"

    body = {
        "client_request_id": str(uuid4()),
        "message": DEMO,
        "retailer_id": str(uuid4()),
    }
    assert desk.post(CAPTURES, json=body).status_code == 404


def test_retailers_cannot_see_each_others_captures(sharma, gupta, desk):
    capture = create(sharma).json()

    assert gupta.get(f"{CAPTURES}/{capture['id']}").status_code == 404
    assert review(gupta, capture, 1, action="reset").status_code == 404
    assert gupta.get(CAPTURES).json()["total"] == 0
    assert sharma.get(CAPTURES).json()["total"] == 1
    # The order desk sees every retailer's captures.
    assert desk.get(f"{CAPTURES}/{capture['id']}").status_code == 200
    assert desk.get(CAPTURES).json()["total"] == 1


def test_retailer_filter_is_for_the_desk(sharma, desk, db_session):
    gupta_id = retailer_id(db_session, "Gupta Kirana Store")

    assert sharma.get(CAPTURES, params={"retailer_id": gupta_id}).status_code == 422
    assert desk.get(CAPTURES, params={"retailer_id": gupta_id}).status_code == 200


# ---------------------------------------------------------------------------
# Creation and review
# ---------------------------------------------------------------------------


def test_create_is_rule_based_and_keeps_the_message(desk, db_session):
    request_id = uuid4()
    sharma = "Sharma General Store"
    first = create(desk, db_session, for_retailer=sharma, request_id=request_id)

    assert first.status_code == 201, first.text
    body = first.json()
    assert body["original_message"] == DEMO
    assert body["status"] == "needs_review"
    assert body["interpretation"]["kind"] == "rules"
    assert body["interpretation"]["is_ai_model"] is False
    ignored = [(span["text"], span["reason"]) for span in body["ignored"]]
    assert ignored == [("bhaiya", "greeting")]
    first_line = body["lines"][0]
    assert first_line["original_text"] == "5 peti biscuit chhota"
    assert first_line["unit_text"] == "peti"
    assert first_line["unit"]["confirmed"] is False
    assert first_line["match"]["proposal"]["sku"] == "SKU-BS-075"
    assert first_line["match"]["proposal"]["match_score"] == 60
    assert first_line["match"]["proposal"]["band"] == "possible"
    assert len(first_line["match"]["alternatives"]) == 2
    assert all(a["weak_match"] for a in first_line["match"]["alternatives"])
    assert "confidence" not in str(body).lower()

    again = create(desk, db_session, for_retailer=sharma, request_id=request_id)
    assert again.status_code == 200
    assert again.json()["id"] == body["id"]

    changed = create(
        desk, db_session, "2 peti surf", for_retailer=sharma, request_id=request_id
    )
    assert changed.status_code == 409


def test_candidates_show_current_stock(sharma):
    lines = create(sharma).json()["lines"]

    stock = {
        candidate["sku"]: candidate["stock_cases"]
        for line in lines
        for candidate in [line["match"]["proposal"], *line["match"]["alternatives"]]
        if candidate
    }
    # The seeded stock: tea is out of stock and stays visible as such.
    assert stock == {"SKU-BS-075": 340, "SKU-DT-1KG": 85, "SKU-TE-250": 0}


@pytest.mark.parametrize("message", ["bhaiya namaste", "   ", ""])
def test_messages_without_order_lines_are_refused(sharma, db_session, message):
    assert create(sharma, message=message).status_code == 422
    assert count(db_session, OrderCapture) == 0


def test_review_errors(sharma, db_session):
    capture = create(sharma).json()
    biscuit = product_id(db_session, "SKU-BS-075")

    # A product without a confirmed unit.
    unit_missing = review(sharma, capture, 1, action="include", product_id=biscuit)
    assert unit_missing.status_code == 422
    # An exclusion without a reason.
    assert review(sharma, capture, 3, action="exclude").status_code == 422
    # An unknown line.
    assert review(sharma, capture, 9, action="reset").status_code == 404
    # Unknown fields.
    response = sharma.post(
        f"{CAPTURES}/{capture['id']}/lines/1",
        json={"expected_version": 1, "action": "reset", "status": "ready"},
    )
    assert response.status_code == 422

    # Nothing changed: still version 1, still pending.
    current = sharma.get(f"{CAPTURES}/{capture['id']}").json()
    assert current["version"] == 1
    assert {line["resolution"] for line in current["lines"]} == {"pending"}


def test_unit_and_product_are_separate_decisions(sharma, db_session):
    capture = create(sharma).json()

    confirmed = review(
        sharma, capture, 1, action="confirm_unit", unit="piece", quantity=480
    )
    assert confirmed.status_code == 200
    first = confirmed.json()["lines"][0]
    assert first["unit"] == {
        "confirmed": True,
        "unit": "piece",
        "quantity": 480,
        "quantity_cases": None,
    }
    assert first["resolution"] == "pending"

    included = review(
        sharma,
        confirmed.json(),
        1,
        action="include",
        product_id=product_id(db_session, "SKU-BS-075"),
    )
    assert included.json()["lines"][0]["unit"]["quantity_cases"] == 5  # 480 / 96


def test_stale_version_conflicts(sharma):
    capture = create(sharma).json()
    review(sharma, capture, 1, action="confirm_unit", unit="case", quantity=5)

    stale = review(sharma, capture, 2, action="confirm_unit", unit="case", quantity=2)

    assert stale.status_code == 409
    assert stale.json()["current_version"] == 2


def test_only_the_desk_may_save_aliases(sharma, db_session):
    capture = create(sharma, message="3 peti lal dabba").json()

    response = review(
        sharma,
        capture,
        1,
        action="include",
        product_id=product_id(db_session, "SKU-BS-075"),
        unit="case",
        quantity=3,
        remember_alias=True,
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


def test_submit_then_the_desk_confirms_through_the_existing_workflow(desk, db_session):
    biscuit = db_session.scalar(select(Product).where(Product.sku == "SKU-BS-075"))
    stock_before = biscuit.stock_quantity
    capture = ready_demo(desk, db_session)

    submitted = submit(desk, capture)

    assert submitted.status_code == 201, submitted.text
    body = submitted.json()
    assert body["created"] is True
    assert body["order"]["status"] == "pending_review"
    assert body["capture"]["status"] == "submitted"
    assert body["capture"]["order"]["id"] == body["order"]["id"]
    order = db_session.get(Order, UUID(body["order"]["id"]))
    assert order.source == "ai_text"
    db_session.refresh(biscuit)
    assert biscuit.stock_quantity == stock_before  # nothing deducted yet

    # Repeating the submission returns the same order.
    again = submit(desk, body["capture"])
    assert again.status_code == 200
    assert again.json()["order"]["id"] == body["order"]["id"]
    assert count(db_session, Order) == 1

    # The existing desk confirmation is what deducts stock.
    order_id = body["order"]["id"]
    preview = desk.get(f"/api/v1/desk/orders/{order_id}/preview")
    assert preview.status_code == 200, preview.text
    confirmed = desk.post(
        f"/api/v1/desk/orders/{order_id}/confirm",
        json={"approved_fingerprint": preview.json()["fingerprint"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["outcome"] == "confirmed"
    db_session.refresh(biscuit)
    assert biscuit.stock_quantity == stock_before - 5


def test_out_of_stock_line_is_reported_against_its_capture_line(desk, db_session):
    capture = create(desk, db_session, for_retailer="Sharma General Store").json()
    response = review(
        desk,
        capture,
        3,
        action="include",
        product_id=product_id(db_session, "SKU-TE-250"),
        unit="case",
        quantity=1,
    )
    capture = response.json()
    for number in (1, 2):
        capture = review(
            desk, capture, number, action="exclude", reason="Not needed"
        ).json()

    rejected = submit(desk, capture)

    assert rejected.status_code == 422
    (issue,) = rejected.json()["issues"]
    assert (issue["line_number"], issue["code"]) == (3, "insufficient_stock")
    assert count(db_session, Order) == 0


def test_not_ready_submission_conflicts(sharma):
    capture = create(sharma).json()

    response = submit(sharma, capture)

    assert response.status_code == 409
    assert response.json()["unresolved_lines"] == [1, 2, 3]


def test_no_changes_after_discard(sharma):
    capture = create(sharma).json()

    discarded = sharma.post(
        f"{CAPTURES}/{capture['id']}/discard",
        json={"expected_version": 1, "reason": "Sent twice by mistake"},
    )
    assert discarded.status_code == 200
    assert discarded.json()["status"] == "discarded"

    current = discarded.json()
    assert review(sharma, current, 1, action="reset").status_code == 409
    assert submit(sharma, current).status_code == 409
    listed = sharma.get(CAPTURES, params={"status": "discarded"}).json()
    assert [item["id"] for item in listed["items"]] == [capture["id"]]


def test_failed_submission_rolls_back_everything(app, db_session, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("simulated failure after the order was created")

    # One sign-in only: a second code request within the resend cooldown
    # would issue no new code.
    with new_client(app, raise_server_exceptions=False) as client:
        login(client, DESK_PHONE)
        capture = create(
            client, db_session, "3 peti lal dabba", for_retailer="Sharma General Store"
        ).json()
        capture = review(
            client,
            capture,
            1,
            action="include",
            product_id=product_id(db_session, "SKU-BS-075"),
            unit="case",
            quantity=3,
            remember_alias=True,
        ).json()
        assert capture["status"] == "ready"

        # Fail after the order has been created, before the alias and the
        # capture status are written.
        monkeypatch.setattr(capture_service, "_save_aliases", fail)
        response = submit(client, capture)
        assert response.status_code == 500
        assert "simulated" not in response.text

        # No order, no alias, and the capture is exactly as it was.
        assert count(db_session, Order) == 0
        assert count(db_session, RetailerProductAlias) == 0
        current = client.get(f"{CAPTURES}/{capture['id']}").json()
        assert current["status"] == "ready"
        assert current["version"] == capture["version"]
        assert current["order"] is None


# ---------------------------------------------------------------------------
# The configured interpreter is used through the HTTP endpoint
# ---------------------------------------------------------------------------

GEMINI_KEY = "api-test-gemini-key-not-real-0123456789"
GEMINI_MODEL = "gemini-api-test-model"
GEMINI_LINES = [
    {"line_text": "5 peti biscuit chhota", "product_text": "biscuit chhota",
     "quantity_text": "5", "unit_text": "peti", "catalogue_sku": "SKU-BS-075"},
    {"line_text": "2 peti washing powder 1 kilo", "product_text": "washing powder",
     "quantity_text": "2", "unit_text": "peti", "size_texts": ["1 kilo"],
     "catalogue_sku": "SKU-DT-1KG"},
    {"line_text": "chai patti 250 wala 1 peti", "product_text": "chai patti",
     "quantity_text": "1", "unit_text": "peti", "size_texts": ["250 wala"],
     "catalogue_sku": "SKU-TE-250"},
]  # fmt: skip


def gemini_reply() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": json.dumps({"lines": GEMINI_LINES})}]
                    },
                    "finishReason": "STOP",
                }
            ]
        },
    )


@pytest.fixture
def gemini_desk(db_session, monkeypatch):
    """A desk client of an app configured for Gemini, whose Gemini HTTP calls
    are answered by a fake transport. Returns (client, requests, answers)."""
    requests: list[httpx.Request] = []
    answers: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        answer = answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    original_init = GeminiInterpreter.__init__

    def init_with_fake_transport(self, **kwargs):
        original_init(self, **{**kwargs, "transport": httpx.MockTransport(handler)})

    monkeypatch.setattr(GeminiInterpreter, "__init__", init_with_fake_transport)

    seed_all(db_session)
    db_session.commit()
    application = create_app(
        make_settings(
            capture_interpreter="gemini",
            gemini_api_key=GEMINI_KEY,
            gemini_model=GEMINI_MODEL,
        ),
        otp_delivery=InMemoryOtpDelivery(),
    )

    def override_get_db():
        yield db_session

    application.dependency_overrides[get_db] = override_get_db
    with new_client(application) as client:
        yield login(client, DESK_PHONE), requests, answers


def test_endpoint_uses_this_apps_gemini_settings(gemini_desk, db_session):
    client, requests, answers = gemini_desk
    answers.append(gemini_reply())

    response = create(client, db_session, for_retailer="Sharma General Store")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["interpretation"]["name"] == "gemini@1"
    assert body["interpretation"]["kind"] == "language_model"
    assert body["interpretation"]["is_ai_model"] is True
    assert body["interpretation"]["fallback_note"] is None
    # The request used this application's key and model (not the process's).
    (request,) = requests
    assert request.headers["x-goog-api-key"] == GEMINI_KEY
    assert f"/models/{GEMINI_MODEL}:generateContent" in str(request.url)
    sent = json.loads(json.loads(request.content)["contents"][0]["parts"][0]["text"])
    assert set(sent) == {"catalogue", "message"}
    # Every line is still a pending suggestion with an unconfirmed unit.
    assert [line["match"]["proposal"]["sku"] for line in body["lines"]] == [
        "SKU-BS-075",
        "SKU-DT-1KG",
        "SKU-TE-250",
    ]
    assert all(line["unit"]["confirmed"] is False for line in body["lines"])
    assert body["can_submit"] is False
    assert GEMINI_KEY not in response.text
    capture = db_session.get(OrderCapture, UUID(body["id"]))
    assert capture.interpreter == "gemini@1"
    assert capture.extracted_data["model"] == GEMINI_MODEL


def test_endpoint_falls_back_to_rules_when_gemini_times_out(gemini_desk, db_session):
    client, requests, answers = gemini_desk
    answers.append(httpx.ReadTimeout("slow"))

    response = create(client, db_session, for_retailer="Sharma General Store")

    assert response.status_code == 201, response.text
    body = response.json()
    assert len(requests) == 1  # Gemini was tried
    assert body["interpretation"]["name"] == "rules@1"
    assert body["interpretation"]["is_ai_model"] is False
    assert body["interpretation"]["fallback_note"] == (
        "The AI reader did not answer in time. The rule-based reader was used instead."
    )
    # The deterministic rules produced the usual result.
    first_line = body["lines"][0]
    assert first_line["original_text"] == "5 peti biscuit chhota"
    assert first_line["match"]["proposal"]["sku"] == "SKU-BS-075"
    assert first_line["match"]["proposal"]["match_score"] == 60
    assert [line["original_text"] for line in body["lines"]] == [
        "5 peti biscuit chhota",
        "2 peti washing powder 1 kilo",
        "chai patti 250 wala 1 peti",
    ]
    capture = db_session.get(OrderCapture, UUID(body["id"]))
    assert capture.extracted_data["ai_fallback"] == "timeout"


def test_default_app_settings_use_the_rules_without_calling_gemini(
    desk, db_session, monkeypatch
):
    def no_gemini(*args, **kwargs):
        raise AssertionError(
            "Gemini must not be constructed with the default settings."
        )

    monkeypatch.setattr(GeminiInterpreter, "__init__", no_gemini)

    response = create(desk, db_session, for_retailer="Sharma General Store")

    assert response.status_code == 201, response.text
    assert response.json()["interpretation"]["name"] == "rules@1"
    assert response.json()["interpretation"]["fallback_note"] is None


# ---------------------------------------------------------------------------
# Possible duplicate warning (order desk)
# ---------------------------------------------------------------------------


def test_desk_gets_a_duplicate_warning_and_can_continue(desk, db_session):
    sharma = retailer_id(db_session, "Sharma General Store")
    first = desk.post(
        CAPTURES,
        json={
            "client_request_id": str(uuid4()),
            "message": DEMO,
            "retailer_id": sharma,
        },
    )
    assert first.status_code == 201, first.text
    before = db_session.scalar(select(func.count()).select_from(OrderCapture))

    request_id = str(uuid4())
    warned = desk.post(
        CAPTURES,
        json={
            "client_request_id": request_id,
            "message": DEMO.upper(),
            "retailer_id": sharma,
        },
    )

    assert warned.status_code == 409
    body = warned.json()
    assert body["code"] == "possible_duplicate"
    assert body["detail"].startswith("This retailer already has a capture")
    (duplicate,) = body["duplicates"]
    assert duplicate["capture_id"] == first.json()["id"]
    assert duplicate["status"] == "needs_review"
    assert duplicate["order"] is None
    assert set(duplicate) == {"capture_id", "created_at", "status", "order"}
    assert db_session.scalar(select(func.count()).select_from(OrderCapture)) == before

    # Same request id, now confirmed as a separate order.
    created = desk.post(
        CAPTURES,
        json={
            "client_request_id": request_id,
            "message": DEMO.upper(),
            "retailer_id": sharma,
            "allow_duplicate": True,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["id"] != first.json()["id"]


# ---------------------------------------------------------------------------
# Desk order review links back to the original message
# ---------------------------------------------------------------------------


def test_desk_order_detail_links_to_its_capture(desk, sharma, db_session):
    capture = ready_demo(desk, db_session)
    order_id = submit(desk, capture).json()["order"]["id"]

    detail = desk.get(f"/api/v1/desk/orders/{order_id}")

    assert detail.status_code == 200, detail.text
    assert detail.json()["capture_id"] == capture["id"]
    original = desk.get(f"{CAPTURES}/{detail.json()['capture_id']}")
    assert original.status_code == 200
    assert original.json()["original_message"] == DEMO
    assert original.json()["status"] == "submitted"
    assert [line["resolution"] for line in original.json()["lines"]] == [
        "included",
        "included",
        "excluded",
    ]
    # The desk order endpoint stays desk-only.
    assert sharma.get(f"/api/v1/desk/orders/{order_id}").status_code == 403


def test_an_app_order_has_no_capture_link(desk, db_session):
    sharma = db_session.scalar(
        select(Retailer).where(Retailer.business_name == "Sharma General Store")
    )
    biscuit = db_session.scalar(select(Product).where(Product.sku == "SKU-BS-075"))
    result = submit_order(
        db_session,
        sharma,
        [OrderLineInput(product_id=biscuit.id, quantity=1)],
        client_request_id=uuid4(),
    )
    db_session.commit()

    detail = desk.get(f"/api/v1/desk/orders/{result.order.id}")

    assert detail.status_code == 200, detail.text
    assert detail.json()["source"] == "retailer_app"
    assert detail.json()["capture_id"] is None
