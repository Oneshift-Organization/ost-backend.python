from pydantic import BaseModel, ConfigDict


class UserClaims(BaseModel):
    model_config = ConfigDict(frozen=True)

    sub: str
    email: str | None = None
    role: str = "authenticated"
