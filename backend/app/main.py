from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.auth import router as auth_router
from app.api.v1.desk_exports import router as desk_exports_router
from app.api.v1.desk_orders import router as desk_orders_router
from app.api.v1.retailer_catalogue import router as retailer_catalogue_router
from app.api.v1.retailer_orders import router as retailer_orders_router
from app.core.config import Settings, get_settings

API_V1_PREFIX = "/api/v1"


def create_app(app_settings: Settings | None = None) -> FastAPI:
    """Build the API with the given settings (default: environment settings).

    The settings are stored on app.state and read by request dependencies,
    so CORS, cookies and the CSRF origin check always use the same values.
    """
    app_settings = app_settings or get_settings()

    application = FastAPI(
        title="OrderBridge API",
        description="Retailer ordering and intelligent order management platform",
        version="0.1.0",
    )
    application.state.settings = app_settings

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

    application.include_router(auth_router, prefix=API_V1_PREFIX)
    application.include_router(retailer_catalogue_router, prefix=API_V1_PREFIX)
    application.include_router(retailer_orders_router, prefix=API_V1_PREFIX)
    application.include_router(desk_orders_router, prefix=API_V1_PREFIX)
    application.include_router(desk_exports_router, prefix=API_V1_PREFIX)

    @application.get("/health")
    def health_check():
        return {
            "status": "healthy",
            "application": "OrderBridge API",
        }

    return application


app = create_app()
