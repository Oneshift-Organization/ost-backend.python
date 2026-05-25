"""Cloud Intelligence Platform — backend Python.

Micromonolito FastAPI com Clean Architecture:
- api/            → entrypoint HTTP (FastAPI routers)
- core/           → config, security, logging, exceptions
- domain/         → entidades, value objects, interfaces (zero framework)
- use_cases/      → regras de negócio orquestradas
- infrastructure/ → DB, file ingestion, repositories, (futuro) cloud_clients
- schemas/        → DTOs Pydantic (borda HTTP)
"""
