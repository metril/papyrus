import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth.oidc import setup_oauth
from app.config import settings
from app.routers import auth, cloud, copy, email, escl, jobs, printer, scanner, smb, system


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    os.makedirs(settings.scan_dir, exist_ok=True)
    os.makedirs(settings.upload_dir, exist_ok=True)
    setup_oauth()
    yield
    # Shutdown: cleanup if needed


app = FastAPI(
    title="Papyrus",
    description="Web-based print and scan server",
    version="0.1.0",
    lifespan=lifespan,
)

# Session middleware for OIDC (must be added before CORS)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)

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

# eSCL scanner protocol (no /api prefix — clients expect /eSCL/ at root)
app.include_router(escl.router, tags=["escl"])

# Serve frontend static files (built React app)
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
