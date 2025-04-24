resource "nomad_job" "main-dagster-executor" {
  jobspec = templatefile(
    "dagster-executor.hcl",
    {
      name = "main-dagster-executor",
      docker_registry = local.docker_registry,
      custom = local.custom
    }
  )
}

resource "nomad_job" "demo-dagster-executor" {
  jobspec = templatefile(
    "dagster-executor.hcl",
    {
      name = "demo-dagster-executor",
      docker_registry = local.docker_registry,
      custom = local.custom
    }
  )
}
