from fastapi import FastAPI

app = FastAPI(
    title="Akash AI Content OS",
    version="0.1.0"
)


@app.get("/")
def root():
    return {
        "message": "Akash AI Content OS Backend is running!"
    }


@app.get("/health")
def health():
    return {
        "status": "ok"
    }