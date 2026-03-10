import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routers import auth, cloud, copy, email, jobs, printer, scanner, smb, system


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure storage directories exist
    os.makedirs(settings.scan_dir, exist_ok=True)
    os.makedirs(settings.upload_dir, exist_ok=True)
    yield
    # Shutdown: cleanup if needed


app = FastAPI(
    title="Papyrus",
    description="Web-based print and scan server",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tightened in production via reverse proxy
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(system.router, prefix="/api/system", tags=["system"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(printer.router, prefix="/api/printer", tags=["printer"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(scanner.router, prefix="/api/scanner", tags=["scanner"])
app.include_router(copy.router, prefix="/api/copy", tags=["copy"])
app.include_router(smb.router, prefix="/api/smb", tags=["smb"])
app.include_router(email.router, prefix="/api/email", tags=["email"])
app.include_router(cloud.router, prefix="/api/cloud", tags=["cloud"])

# Serve frontend static files (built React app)
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
