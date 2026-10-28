import uvicorn

from app.config import exposed, host, password, port


def main() -> None:
    if exposed() and not password():
        raise SystemExit(
            "Refusing to listen beyond this machine without SKIT_PASSWORD. "
            "Set a password, or keep SKIT_HOST=127.0.0.1."
        )
    uvicorn.run("app.main:app", host=host(), port=port(), reload=False)


if __name__ == "__main__":
    main()
