from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.request_limits import RequestBodyLimitMiddleware
from app.api.v1.auth import router as auth_router
from app.api.v1.captures import router as captures_router
from app.api.v1.desk_exports import router as desk_exports_router
from app.api.v1.desk_orders import router as desk_orders_router
from app.api.v1.desk_retailers import router as desk_retailers_router
from app.api.v1.retailer_catalogue import router as retailer_catalogue_router
from app.api.v1.retailer_orders import router as retailer_orders_router
from app.core.config import Settings, get_settings
from app.services.otp_delivery import OtpDelivery, otp_delivery_for

API_V1_PREFIX = "/api/v1"


def create_app(
    app_settings: Settings | None = None,
    *,
    otp_delivery: OtpDelivery | None = None,
) -> FastAPI:
    """Build the API with the given settings (default: environment settings).

    The settings are stored on app.state and read by request dependencies,
    so CORS, cookies and the CSRF origin check always use the same values.

    otp_delivery defaults to the channel configured for the environment
    (otp_delivery_for); tests pass an InMemoryOtpDelivery.

    In production the interactive documentation and the OpenAPI schema are
    not served (/docs, /redoc and /openapi.json return 404).
    """
    app_settings = app_settings or get_settings()
    serve_docs = app_settings.app_env != "production"

    application = FastAPI(
        title="OrderBridge API",
        description="Retailer ordering and intelligent order management platform",
        version="0.1.0",
        docs_url="/docs" if serve_docs else None,
        redoc_url="/redoc" if serve_docs else None,
        openapi_url="/openapi.json" if serve_docs else None,
    )
    application.state.settings = app_settings
    application.state.otp_delivery = otp_delivery or otp_delivery_for(app_settings)

    # Credentialed CORS only for the configured frontend origins; never "*".
    # CORS is not CSRF protection: state-changing routes also use
    # require_trusted_origin.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.frontend_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    # Added last, so it is the outermost middleware: oversized bodies are
    # refused before anything else reads them.
    application.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=app_settings.max_request_body_bytes,
    )

    application.include_router(auth_router, prefix=API_V1_PREFIX)
    application.include_router(retailer_catalogue_router, prefix=API_V1_PREFIX)
    application.include_router(retailer_orders_router, prefix=API_V1_PREFIX)
    application.include_router(desk_orders_router, prefix=API_V1_PREFIX)
    application.include_router(desk_exports_router, prefix=API_V1_PREFIX)
    application.include_router(desk_retailers_router, prefix=API_V1_PREFIX)
    application.include_router(captures_router, prefix=API_V1_PREFIX)

    @application.get("/health")
    def health_check():
        return {
            "status": "healthy",
            "application": "OrderBridge API",
        }

    return application


app = create_app()
