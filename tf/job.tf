resource "nomad_job" "dagster-executor" {
  hcl2 {
    enabled = true
  }
  jobspec = templatefile(
    "dagster-executor.hcl",
    {
      docker_registry = local.docker_registry,
      custom = local.custom
    }
  )
}
