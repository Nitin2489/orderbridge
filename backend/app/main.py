from fastapi import FastAPI

app = FastAPI(
    title="OrderBridge API",
    description="Retailer ordering and intelligent order management platform",
    version="0.1.0",
)


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "application": "OrderBridge API",
    }
