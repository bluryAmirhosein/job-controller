# Job Controller

A high-performance asynchronous job processing system built with **FastAPI, PostgreSQL, RabbitMQ, and Redis**.

The project is designed to manage and execute multiple background jobs efficiently while focusing on **performance, concurrency control, reliability, scalability, and maintainable code structure** rather than providing only a basic background task implementation.

It supports job lifecycle management, background processing, real-time status updates, retry handling, rate limiting, caching, idempotent job creation, and role-based authorization.

## Features

* **Asynchronous background job processing**
* **Six-state job lifecycle**

  * `PENDING`
  * `QUEUED`
  * `RUNNING`
  * `COMPLETED`
  * `FAILED`
  * `CANCELLED`
* **JWT-based authentication**
* **Role-based authorization**

  * `Admin`
  * `User`
* **Real-time job status updates via WebSocket**
* **Per-user concurrency control**

  * A maximum of **3 jobs can run concurrently per user**
  * Additional jobs are placed in the `QUEUED` state
* **Automatic queue promotion** when execution capacity becomes available
* **Idempotent job creation** using an `idempotency_key`
* **Up to 3 retry attempts** for retryable job failures
* **Per-user rate limiting**

  * Maximum **10 requests per minute**
* **Detailed job logging**

  * Job lifecycle events and execution stages are recorded for reporting and troubleshooting
* **Redis-based caching** for job listing requests to reduce unnecessary database reads
* **Cursor-based pagination** for efficient pagination of large datasets
* **Centralized error handling**
* **Structured logging and meaningful application logs**
* **Containerized environment with Docker**
* Designed with **clean code principles** and clear separation of responsibilities
* Includes **unit and integration tests**
* Designed with **high-load and future horizontal scaling** in mind

## Job Processing Flow

The general execution flow of a job is:

```text
API
 ↓
PostgreSQL
 ↓
RabbitMQ
 ↓
Worker
 ↓
Execution
 ↓
Status Update
```

Jobs are first persisted in PostgreSQL, ensuring that job creation and state management are handled reliably before processing.

Eligible jobs are then dispatched through RabbitMQ and consumed by background workers. During execution, the job status and related logs are updated, while clients can receive real-time status changes through WebSocket connections.

## Concurrency Control

The system prevents a single user from consuming unlimited worker capacity.

Each user can have a maximum of **3 jobs in the `RUNNING` state** simultaneously.

When the concurrency limit is reached:

```text
New Job
   ↓
Concurrency Limit Reached
   ↓
QUEUED
```

Once a running job finishes, fails, or is cancelled and capacity becomes available, the next eligible queued job can be promoted for execution.

This approach provides controlled resource usage and helps maintain fair processing across users.

## Technology Stack

* **Python**
* **FastAPI**
* **PostgreSQL**
* **Redis**
* **RabbitMQ**
* **Docker**
* **Docker Compose**
* **JWT Authentication**
* **WebSocket**
* **SQLAlchemy**
* **Git**
* **Pytest**

## Project Structure

```text
job_controller/
├── app/
│   ├── api/                 # API routes and endpoints
│   ├── core/                # Configuration, security, dependencies, pagination,
│   │                        # rate limiting, and WebSocket authentication
│   ├── infrastructure/      # Database, Redis, and RabbitMQ integrations
│   ├── models/              # Database models
│   ├── repositories/        # Data access layer
│   ├── schemas/             # Request and response schemas
│   ├── services/            # Application and business logic
│   ├── workers/             # Background workers and task execution
│   └── main.py              # FastAPI application entry point
│
├── scripts/                 # Utility and management scripts
│
├── tests/
│   ├── unit/                # Unit tests
│   └── integration/         # Integration tests
│
├── Dockerfile
├── docker-compose.yml
└── example.env
```

## Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/bluryAmirhosein/job-controller.git
```

```bash
cd job-controller
```

### 2. Create the Environment File

Copy the example environment file:

```bash
cp example.env .env
```

Update the environment variables in `.env` if required.

### 3. Build and Start the Application

For the first run, build the images and start all services with:

```bash
docker compose up -d --build
```

This starts the application and its required infrastructure services in isolated Docker containers.

### Stop the Application

```bash
docker compose down
```

### View Container Logs

```bash
docker compose logs -f
```

## API Documentation

Once the application is running, interactive API documentation is available through FastAPI's built-in documentation interface.

```text
http://localhost:8000/docs
```

## Testing

The project includes both unit and integration tests.

To run the tests:

```bash
pytest
```

## Design Goals

This project focuses on building a reliable job execution system that can handle multiple asynchronous workloads while maintaining controlled concurrency and efficient resource usage.

Key goals include:

* High-performance job processing
* Controlled concurrency per user
* Reliable job state management
* Efficient database access through caching and cursor pagination
* Protection against duplicate job creation
* Real-time visibility into job execution
* Clear error handling and observability
* Scalability for increasing workloads and load
* A maintainable and testable codebase

For deeper architectural decisions, concurrency mechanisms, job state transitions, reliability guarantees, and other implementation details, see the project's `DESIGN.md`.
