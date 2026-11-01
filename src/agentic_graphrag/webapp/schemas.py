"""Validated HTTP inputs. Password values are redacted from model representations."""

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class EmailInput(InputModel):
    email: str = Field(min_length=3, max_length=254)


class LoginInput(EmailInput):
    password: SecretStr = Field(min_length=1, max_length=128)


class RegisterInput(LoginInput):
    display_name: str = Field(min_length=1, max_length=100)


class ResetInput(InputModel):
    token: str = Field(min_length=1, max_length=256)
    password: SecretStr = Field(min_length=1, max_length=128)


class PasswordInput(InputModel):
    current_password: SecretStr = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=128)


class ProfileInput(InputModel):
    display_name: str = Field(min_length=1, max_length=100)
