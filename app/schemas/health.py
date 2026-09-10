from pydantic import BaseModel


class DependencyStatus(BaseModel):
    database: bool
    redis: bool
    rabbitmq: bool


class HealthCheckResponse(BaseModel):
    status: str
    dependencies: DependencyStatus