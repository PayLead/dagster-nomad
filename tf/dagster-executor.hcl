job "dagster-executor" {
  datacenters = ["dc1"]
  type = "batch"

  parameterized {
    payload = "required"
  }

  task "server" {
    driver = "docker"

    dispatch_payload {
      file = "input.txt"
    }

    config {
      image = "dagster-nomad-example:local"
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
