set dotenv-load

export AWS_ACCESS_KEY_ID := "dagsterapp"
export AWS_SECRET_ACCESS_KEY := "dagsterapp"
export DAGSTER_NOMAD_JOB_ID :=  "dagster-executor"
export DAGSTER_NOMAD_URL :=  "http://127.0.0.1:4646"
export DAGSTER_POSTGRES_DSN := "postgresql://dagster:dagster@localhost:5432/dagster"
export DAGSTER_S3_BUCKET := "dagster"
export DAGSTER_S3_ENDPOINT_URL := "http://minio:9000"

### Python Virtual environment Setup ###
setup-dev:
    source .venv/bin/activate
    poetry install

quality-format:
    ruff --fix-only --exit-zero .
    black .

quality-check:
    ruff .
    black . --check

check-pkg-constraints:
	poetry lock --check

### Launch Dagster postgres ###
docker-up:
    docker compose up -d

docker-down:
    docker compose down -v --remove-orphans --rmi all

### Launch Dagster ###
dagster-dev:
    source .venv/bin/activate
    docker compose up -d
    DAGSTER_HOME="$PWD/dagster" dagster dev

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
    terraform apply

terraform-up:
    #!/usr/bin/env bash
    set -euxo pipefail
    cd tf
    terraform apply