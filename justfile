set dotenv-load

export AWS_ACCESS_KEY_ID := "dagsterapp"
export AWS_SECRET_ACCESS_KEY := "dagsterapp"
export DAGSTER_NOMAD_JOB_ID :=  "dagster-executor"
export DAGSTER_NOMAD_URL :=  "http://127.0.0.1:4646"
export DAGSTER_POSTGRES_DSN := "postgresql://dagster:dagster@localhost:5432/dagster"
export DAGSTER_S3_BUCKET := "dagster"
export DAGSTER_S3_ENDPOINT_URL := "http://minio:9000"
export DAGSTER_CURRENT_IMAGE := "dagster-nomad-example:local"

### Python Virtual environment Setup ###
setup-dev:
    source .venv/bin/activate
    uv sync

quality-format:
    uv run ruff format --fix-only --exit-zero .
    uv run black .

quality-check:
    uv run ruff check .
    uv run black . --check

check-pkg-constraints:
    uv lock --check

### Launch Dagster postgres ###
docker-up:
    docker compose up -d

docker-down:
    docker compose down -v --remove-orphans --rmi all

### Launch Dagster ###
dagster-dev:
    #!/usr/bin/env bash
    source .venv/bin/activate
    docker compose up -d
    DAGSTER_HOME="$PWD/dagster" dagster dev

dagster-grpc:
    source .venv/bin/activate
    dagster api grpc --module-name user_code.defs --host 0.0.0.0 --port 4266

### Launch Nomad Cluster ###
nomad-up:
    nomad agent -dev -node pynomad1

### Launch Terraform ###
terraform-setup:
    #!/usr/bin/env bash
    set -euxo pipefail
    cd tf
    cp local.tf.example local.tf
    terraform init
    terraform apply -auto-approve

terraform-up:
    #!/usr/bin/env bash
    set -euxo pipefail
    cd tf
    terraform apply
