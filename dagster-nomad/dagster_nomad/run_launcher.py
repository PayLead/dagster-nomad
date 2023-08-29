from __future__ import annotations

import base64
from collections.abc import Generator
from typing import Any, ClassVar, Optional

import httpx
from dagster import Field, StringSource
from dagster import _check as check
from dagster._core.instance import T_DagsterInstance
from dagster._core.launcher import LaunchRunContext, RunLauncher
from dagster._core.launcher.base import (
    CheckRunHealthResult,
    ResumeRunContext,
    WorkerStatus,
)
from dagster._core.storage.dagster_run import DagsterRun
from dagster._grpc.types import ExecuteRunArgs
from dagster._serdes import ConfigurableClass
from dagster._serdes.config_class import ConfigurableClassData


class NomadAuth(httpx.Auth):
    __slots__ = ("token",)

    def __init__(self, token: str | None = None):
        self.token = token

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        if self.token:
            request.headers["X-Nomad-Token"] = self.token
        yield request


class NomadClient(httpx.Client):
    __slots__ = ()

    def __init__(self, url: str, token: str | None = None, namespace: str | None = None, **kwargs):
        kwargs.setdefault("auth", NomadAuth(token))
        if namespace:
            kwargs.setdefault("params", {"namespace": namespace})

        super().__init__(headers={"Content-Type": "application/json"}, base_url=url, **kwargs)

    def dispatch_job(self, job_id: str, payload: str | bytes, meta: dict[str, Any]) -> str:
        """Create a new dispatch of the provided job ID.

        Args:
          job_id: ID of Nomad job to dispatch.
          payload: A str or bytes payload to pass to the job.
          meta: A key-value to parametrize the job.

        Returns:
          The dispatched Job ID returned by Nomad.
        """

        if isinstance(payload, str):
            payload = payload.encode()
        encoded_payload = base64.standard_b64encode(payload).decode()

        res = self.post(
            f"/v1/job/{job_id}/dispatch",
            json={"Payload": encoded_payload, "Meta": meta},
        )
        res.raise_for_status()
        return res.json()["DispatchedJobID"]

    def get_job_status(self, job_id: str) -> tuple[str, bool]:
        """Retrieve the status of the provided job id.

        Args:
          job_id: The id of the job

        Returns:
            A tuple with `state` and `failed`
        """

        res = self.get(f"/v1/job/{job_id}/allocations")
        res.raise_for_status()

        data = res.json()
        state: str = data[0]["TaskStates"]["server"]["State"]
        failed: bool = data[0]["TaskStates"]["server"]["Failed"]

        return state, failed

    def stop_job(self, job_id: str) -> None:
        """Stop a job

        Args:
          job_id: The id of the job

        Returns:
            None
        """
        res = self.delete(f"/v1/job/{job_id}")
        res.raise_for_status()


class NomadRunLauncher(RunLauncher[T_DagsterInstance], ConfigurableClass):
    """RunLauncher that starts a job in Nomad for each Dagster job run."""

    NOMAD_DISPATCHED_JOB_ID_TAG: ClassVar[str] = "nomad_dispatched_job_id"

    def __init__(
        self,
        inst_data: Optional[ConfigurableClassData] = None,
        *,
        docker_image: str | None = None,
        job_id: str,
        url: str,
        token: str | None = None,
        namespace: str | None = None,
    ):
        self._inst_data = inst_data

        self.docker_image = docker_image
        self.nomad_job_id = job_id
        self.nomad_client = NomadClient(url, token, namespace)

        super().__init__()

    def __del__(self):
        self.nomad_client.close()

    @property
    def inst_data(self) -> Any:
        return self._inst_data

    @classmethod
    def config_type(cls) -> dict[str, Field]:
        return {
            "docker_image": Field(
                str,
                is_required=False,
                description="The docker image to be used if the repository does not specify one.",
            ),
            "job_id": Field(
                StringSource,
                is_required=True,
                description="The Nomad job ID to dispatch.",
            ),
            "url": Field(
                StringSource,
                is_required=True,
                description="The Nomad HTTP API URL.",
            ),
            "token": Field(
                StringSource,
                is_required=False,
                description="The Nomad token.",
            ),
            "namespace": Field(
                StringSource,
                is_required=False,
                description="The Nomad namespace of the job.",
            ),
        }

    @staticmethod
    def from_config_value(inst_data, config_value) -> NomadRunLauncher:
        return NomadRunLauncher(inst_data=inst_data, **config_value)

    def _get_command_args(self, run_args: ExecuteRunArgs, context: LaunchRunContext):
        return run_args.get_command_args()

    def _get_docker_image(self, job_code_origin):
        docker_image = job_code_origin.repository_origin.container_image

        if not docker_image:
            docker_image = self.docker_image

        if not docker_image:
            raise Exception("No docker image specified by the instance config or repository")

        return docker_image

    def launch_run(self, context: LaunchRunContext) -> None:
        run = context.dagster_run
        job_origin = check.not_none(context.job_code_origin)
        docker_image = self._get_docker_image(job_origin)

        args = ExecuteRunArgs(
            job_origin=job_origin,
            run_id=run.run_id,
            instance_ref=self._instance.get_ref(),
        )
        command = self._get_command_args(args, context)
        payload = "\n".join(command)

        meta = {"IMAGE": docker_image}
        dispatched_job_id = self.nomad_client.dispatch_job(self.nomad_job_id, payload=payload, meta=meta)

        self._instance.report_engine_event(
            message=f"Dispatched a new run for job `{self.nomad_job_id}` with dispatched_job_id `{dispatched_job_id}`",
            dagster_run=run,
            cls=self.__class__,
        )
        self._instance.add_run_tags(
            run.run_id,
            {self.NOMAD_DISPATCHED_JOB_ID_TAG: dispatched_job_id},
        )

    def terminate(self, run_id: str) -> bool:
        dispatched_job_id = None

        run = self._instance.get_run_by_id(run_id)
        run = check.not_none(run)
        if run:
            dispatched_job_id = self._instance.get_run_by_id(run_id)

        if dispatched_job_id is None:
            self._instance.report_engine_event(
                message="Unable to get nomad `dispatched_job_id` to send termination request.",
                dagster_run=run,
                cls=self.__class__,
            )
            return False

        dispatched_job_id = run.tags.get(self.NOMAD_DISPATCHED_JOB_ID_TAG)

        self._instance.report_run_canceling(run)
        self.nomad_client.stop_job(dispatched_job_id)

        return True

    @property
    def supports_check_run_worker_health(self) -> bool:
        return True

    def check_run_worker_health(self, run: DagsterRun) -> CheckRunHealthResult:
        dispatched_job_id = run.tags.get(self.NOMAD_DISPATCHED_JOB_ID_TAG)

        try:
            state, failed = self.nomad_client.get_job_status(dispatched_job_id)
        except httpx.HTTPError:
            return CheckRunHealthResult(WorkerStatus.NOT_FOUND)

        match (state, failed):
            case ("running", _):
                return CheckRunHealthResult(WorkerStatus.RUNNING)
            case ("dead", True):
                return CheckRunHealthResult(WorkerStatus.FAILED)
            case ("dead", False):
                return CheckRunHealthResult(WorkerStatus.SUCCESS)
            case _:
                return CheckRunHealthResult(WorkerStatus.UNKNOWN)

    @property
    def supports_resume_run(self) -> bool:
        return True

    def resume_run(self, context: ResumeRunContext) -> None:
        run = context.dagster_run
        job_origin = check.not_none(context.job_code_origin)
        docker_image = self._get_docker_image(job_origin)

        args = ExecuteRunArgs(
            job_origin=job_origin,
            run_id=run.run_id,
            instance_ref=self._instance.get_ref(),
        )
        command = args.get_command_args()

        payload = "\n".join(command)
        meta = {"IMAGE": docker_image}

        dispatched_job_id = self.nomad_client.dispatch_job(self.nomad_job_id, payload=payload, meta=meta)

        self._instance.report_engine_event(
            message=(
                f"Dispatched a new resume_run for job `{self.nomad_job_id}`"
                f"with dispatched_job_id `{dispatched_job_id}`"
            ),
            dagster_run=run,
            cls=self.__class__,
        )
        self._instance.add_run_tags(
            run.run_id,
            {self.NOMAD_DISPATCHED_JOB_ID_TAG: dispatched_job_id},
        )
