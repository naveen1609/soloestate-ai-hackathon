"""Run from backend: python -m scripts.check_meta. Makes one paid API request."""

import asyncio
import json

from pydantic import ValidationError

from app.config import Settings, EnvironmentFileError
from app.services.meta_model import MetaModelService, ModelServiceError


async def check() -> int:
    try:
        async with MetaModelService(Settings()) as service:
            result = await service.generate("Reply with a short greeting to SoloEstate AI. Do not use tools.")
        print(json.dumps({"status": "ok", **result.model_dump()}, indent=2))
        return 0
    except ModelServiceError as exc:
        print(json.dumps({"status": "error", "code": exc.code, "message": str(exc)}))
    except EnvironmentFileError as exc:
        print(json.dumps({"status": "error", "code": "configuration", "message": str(exc)}))
    except ValidationError:
        print(json.dumps({"status": "error", "code": "configuration", "message": "Invalid backend settings; check backend/.env.example and MODEL_TIMEOUT_SECONDS (greater than 0 and at most 120)."}))
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))
