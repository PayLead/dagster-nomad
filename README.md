# Dagster Nomad

This is an example Dagster project with the dagster-nomad integration.

## Setup

1. Install a Nomad cluster following the [Nomad documentation](https://developer.hashicorp.com/nomad/docs/install).
2. Install Terraform following the [Terraform documentation](https://developer.hashicorp.com/terraform/downloads).
3. Install just for launching commands [Just documentation](https://github.com/casey/just).
4. Install Dagster and dagster-nomad in a python virtual environment:

    ```shell
    python3.11 -m venv .venv
    just setup-dev
    ```

5. Install Docker compose

## Run example

Build an image for the RunLauncher to use and adjust the tag in `tf/dagster-executor.hcl`

```shell
docker build . --tag dagster-nomad-example:local
```

In a terminal run the following

```shell
just docker-up
just nomad-up
```

Create a bucket `dagster` in [minio](http://localhost:9001/)
In another terminal run the following.

```shell
just terraform-setup
just dagster-dev
```

Open in your browser [dagster-webserver](http://127.0.0.1:3000) and [nomad-ui](http://127.0.0.1:4647)

## Multiple Code Locations example

Following the convention for the `DockerRunLauncher` and `K8sRunLauncher`,
the `DAGSTER_CURRENT_IMAGE` environment variable [can used to specify the Docker image](https://docs.dagster.io/concepts/code-locations/workspace-files#specifying-a-docker-image) for the `NomadRunLauncher` to use.

Build the different images for your code locations.

```shell
docker build . --tag dagster-nomad-example:local
```

Launch the multiple code locations:

```shell
DAGSTER_CURRENT_IMAGE=dagster-nomad-example:local  just dagster-grpc
```

Uncomment the `load_from.grpc_server` in the `workspace.yaml` files

```shell
just dagster-dev
```
