# Job Controller

A distributed task execution and job orchestration system designed to receive tasks, manage their lifecycle, and execute them through a reliable, asynchronous workflow.

The project is built around a message-driven architecture using **Python, FastAPI, RabbitMQ, Redis, PostgreSQL, and Docker**.

## 🚧 Project Status

**Work in Progress**

The project is currently under active development and its architecture and APIs may change as the implementation evolves.

The initial development phase is focused on establishing the core execution pipeline, job lifecycle management, messaging infrastructure, persistence, and reliability mechanisms.

### Documentation

The documentation is **coming soon**.

Once the project reaches **Phase 1**, comprehensive documentation covering the architecture, components, configuration, execution flow, API, and development setup will be added to this repository.

> **Note:** This repository is currently intended primarily for development and architectural exploration. Some components, interfaces, and behaviors are not yet considered stable.

## Tech Stack

* **Python** — Core application and task execution
* **FastAPI** — API layer
* **RabbitMQ** — Asynchronous messaging and task distribution
* **Redis** — Fast-access state and coordination
* **PostgreSQL** — Persistent storage
* **Docker** — Containerization and environment isolation

## Architecture

At a high level, the system is designed around asynchronous task processing and explicit job lifecycle management.

```text
Client
  │
  ▼
FastAPI
  │
  ▼
Job Controller
  │
  ├──────────► PostgreSQL
  │
  ├──────────► Redis
  │
  ▼
RabbitMQ
  │
  ▼
Workers / Task Executors
```

The architecture is intentionally designed to separate task submission, job orchestration, message delivery, state management, and task execution.

More detailed architectural documentation will be available after Phase 1.

## Development Roadmap

### Future Phases

Further phases will focus on improving reliability, observability, scalability, operational tooling, and developer experience.

---

**Status:** 🚧 Active Development
**Documentation:** Coming Soon
**Version:** Pre-1.0
