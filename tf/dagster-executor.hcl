job "dagster-executor" {
  datacenters = ["dc1"]
  type = "batch"

  parameterized {
    payload = "required"
    meta_required = ["IMAGE"]
  }

  task "server" {
    driver = "docker"

    dispatch_payload {
      file = "input.txt"
    }

    config {
      image = "$${NOMAD_META_IMAGE}"
      entrypoint = [ "/dagster_job.sh" ]
      args = [ "$${NOMAD_TASK_DIR}/input.txt" ]
    }

    env {
      %{~ for key, value in custom }
      ${key} = "${value}"
      %{ endfor ~}
    }

    resources {
        cpu    = 1000 # MHz
        memory = 2048 # MB
    }
  }
}
