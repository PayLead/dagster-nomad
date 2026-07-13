resource "nomad_job" "dagster-executor" {
  jobspec = templatefile(
    "dagster-executor.hcl",
    {
      docker_registry = local.docker_registry,
      custom = local.custom
    }
  )
}
