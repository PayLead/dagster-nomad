# Dagster Nomad

This is a Dagster integration for Nomad that allows running Dagster pipelines on Nomad.

## Multiple Nomad clusters

The top-level `url` / `token` / `namespace` / `job_id` / `job_id_mapping` define the `default` cluster.
Additional named clusters can be declared under `clusters`, and `cluster_rules` selects which one runs a given
Dagster run, based on its code location name and/or Dagster job name (shell-style globs, case sensitive).

```yaml
run_launcher:
  module: dagster_nomad
  class: NomadRunLauncher
  config:
    # default cluster
    url: {env: DAGSTER_NOMAD_URL}
    token: {env: DAGSTER_NOMAD_TOKEN}
    job_id: dagster-executor
    clusters:
      gpu:
        url: {env: DAGSTER_NOMAD_GPU_URL}
        token: {env: DAGSTER_NOMAD_GPU_TOKEN}  # never inherited from the default cluster
        namespace: ml
        job_id: dagster-executor-gpu          # defaults to the top-level job_id
        job_id_mapping:                       # per-cluster code location -> nomad job
          knowledge: dagster-executor-ml
    cluster_rules:                            # evaluated in order, first match wins
      - cluster: gpu
        location: "ml-*"
      - cluster: gpu
        location: analytics
        job: "train_*"                        # both location AND job must match
```

A job can also choose its cluster itself with the `dagster-nomad/cluster` tag, which takes precedence over
`cluster_rules` (the tag can also be set on a single run from the launchpad):

```python
@job(tags={"dagster-nomad/cluster": "gpu"})
def train_model(): ...

# or with assets
define_asset_job("train_model", selection=..., tags={"dagster-nomad/cluster": "gpu"})
```

A tag referencing an unknown cluster makes the launch fail instead of silently falling back to `default`.

Runs matching no rule go to the `default` cluster. The selected cluster is stored in the `nomad_cluster`
run tag so that health checks and termination target the right cluster; runs without this tag
(dispatched by older versions) are assumed to be on `default`.
