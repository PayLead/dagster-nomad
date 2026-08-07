from __future__ import annotations

import base64
from collections.abc import Generator
from typing import Any, ClassVar, Optional
from enum import StrEnum

import httpx2
from dagster import Field, Map, StringSource
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


class NomadTaskState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DEAD = "dead"


class NomadAuth(httpx2.Auth):
    __slots__ = ("token",)

    def __init__(self, token: str | None = None):
        self.token = token

    def auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response, None]:
        if self.token:
            request.headers["X-Nomad-Token"] = self.token
        yield request


class NomadClient(httpx2.Client):
    __slots__ = ()

    def __init__(self, url: str, token: str | None = None, namespace: str | None = None, **kwargs):
        kwargs.setdefault("auth", NomadAuth(token))
        if namespace:
            kwargs.setdefault("params", {"namespace": namespace})

        kwargs.setdefault("timeout", httpx2.Timeout(10.0))
        kwargs.setdefault("transport", httpx2.HTTPTransport(retries=2))

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

    def get_job_status(self, job_id: str) -> tuple[str, NomadTaskState, bool] | None:
        """Retrieve the status of the provided job id.

        Args:
          job_id: The id of the job

        Returns:
            A tuple with `alloc_id`, `state` and `failed`, or `None` if the job has been
            garbage collected, which Nomad reports either as a 404 or as an empty
            allocation list.
        """

        res = self.get(f"/v1/job/{job_id}/allocations")
        if res.status_code == httpx2.codes.NOT_FOUND:
            return None
        res.raise_for_status()
        allocations = res.json()
        if not allocations:
            return None

        allocation = max(allocations, key=lambda alloc: alloc["CreateIndex"])
        alloc_id: str = allocation["ID"]

        task_states = allocation.get("TaskStates")
        if not task_states:
            # Equals to https://developer.hashicorp.com/nomad/api-docs/allocations#taskstatepending
            return alloc_id, NomadTaskState.PENDING, False

        # There is no reason to have multiple task inside a dagster job (from nomad point of view)
        # so we use the first one
        task = list(task_states)[0]
        state: NomadTaskState = NomadTaskState(task_states[task]["State"])
        failed: bool = task_states[task]["Failed"]

        return alloc_id, state, failed

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
        job_id_mapping: dict[str, str] | None = None,
        url: str,
        token: str | None = None,
        namespace: str | None = None,
    ):
        self._inst_data = inst_data

        self.docker_image = docker_image
        self.nomad_job_id = job_id
        self.nomad_job_id_mapping = job_id_mapping or {}
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
                description="The default Nomad job ID to dispatch, can be override by code location using job_id_mapping.",
            ),
            "job_id_mapping": Field(
                Map(str, str),
                is_required=False,
                description="An optional mapping between code location name and nomad job.",
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

    def _get_nomad_job_id(self, run: DagsterRun) -> str:
        remote_job_origin = run.remote_job_origin
        if remote_job_origin is None:
            return self.nomad_job_id

        return self.nomad_job_id_mapping.get(remote_job_origin.location_name, self.nomad_job_id)

    def launch_run(self, context: LaunchRunContext) -> None:
        run = context.dagster_run
        job_origin = check.not_none(context.job_code_origin)
        docker_image = self._get_docker_image(job_origin)
        nomad_job_id = self._get_nomad_job_id(run)

        args = ExecuteRunArgs(
            job_origin=job_origin,
            run_id=run.run_id,
            instance_ref=self._instance.get_ref(),
        )
        command = self._get_command_args(args, context)
        payload = "\n".join(command)

        meta = {"IMAGE": docker_image}
        dispatched_job_id = self.nomad_client.dispatch_job(nomad_job_id, payload=payload, meta=meta)

        self._instance.report_engine_event(
            message=f"Dispatched a new run for job `{nomad_job_id}` with dispatched_job_id `{dispatched_job_id}`",
            dagster_run=run,
            cls=self.__class__,
        )
        self._instance.add_run_tags(
            run.run_id,
            {self.NOMAD_DISPATCHED_JOB_ID_TAG: dispatched_job_id},
        )

    def terminate(self, run_id: str) -> bool:
        run = check.not_none(self._instance.get_run_by_id(run_id))
        dispatched_job_id = run.tags.get(self.NOMAD_DISPATCHED_JOB_ID_TAG)

        if dispatched_job_id is None:
            self._instance.report_engine_event(
                message="Unable to get nomad `dispatched_job_id` to send termination request.",
                dagster_run=run,
                cls=self.__class__,
            )
            return False

        self._instance.report_run_canceling(run)
        self.nomad_client.stop_job(dispatched_job_id)

        return True

    @property
    def supports_check_run_worker_health(self) -> bool:
        return True

    def check_run_worker_health(self, run: DagsterRun) -> CheckRunHealthResult:
        dispatched_job_id = run.tags.get(self.NOMAD_DISPATCHED_JOB_ID_TAG)
        if dispatched_job_id is None:
            return CheckRunHealthResult(
                WorkerStatus.NOT_FOUND,
                f"Run has no `{self.NOMAD_DISPATCHED_JOB_ID_TAG}` tag, no Nomad job was dispatched.",
            )

        try:
            status = self.nomad_client.get_job_status(dispatched_job_id)
        except httpx2.HTTPError as exc:
            self._instance.report_engine_event(
                message=f"Failed to get run status of dispatched_job_id `{dispatched_job_id}`: `{exc}`",
                dagster_run=run,
                cls=self.__class__,
            )
            return CheckRunHealthResult(
                WorkerStatus.RUNNING,
                f"Could not reach Nomad to check dispatched_job_id `{dispatched_job_id}`: {exc}",
                transient=True,
            )

        if status is None:
            return CheckRunHealthResult(
                WorkerStatus.NOT_FOUND,
                f"Nomad job `{dispatched_job_id}` does not exist anymore, it was likely garbage collected.",
            )

        match status:
            case (alloc_id, NomadTaskState.RUNNING, _):
                self._instance.report_engine_event(
                    message=f"Job is running: `com.hashicorp.nomad.alloc_id: {alloc_id}`",
                    dagster_run=run,
                    cls=self.__class__,
                )
                return CheckRunHealthResult(WorkerStatus.RUNNING)
            case (_, NomadTaskState.PENDING, _):
                return CheckRunHealthResult(WorkerStatus.RUNNING)
            case (alloc_id, NomadTaskState.DEAD, True):
                return CheckRunHealthResult(
                    WorkerStatus.FAILED,
                    f"Nomad task of dispatched_job_id `{dispatched_job_id}` failed "
                    f"(com.hashicorp.nomad.alloc_id: {alloc_id}).",
                )
            # This case is not supposed to be reached, since dagster should detect the end of execution,
            # before calling check_run_worker_health
            case (alloc_id, NomadTaskState.DEAD, False):
                return CheckRunHealthResult(
                    WorkerStatus.FAILED,
                    f"Nomad task of dispatched_job_id `{dispatched_job_id}` exited successfully "
                    f"but the run never reported its completion (com.hashicorp.nomad.alloc_id: {alloc_id}).",
                )
            case (_, state, _):
                return CheckRunHealthResult(WorkerStatus.UNKNOWN, f"Unhandled Nomad task state `{state}`.")

    @property
    def supports_resume_run(self) -> bool:
        return True

    def resume_run(self, context: ResumeRunContext) -> None:
        run = context.dagster_run
        job_origin = check.not_none(context.job_code_origin)
        docker_image = self._get_docker_image(job_origin)
        nomad_job_id = self._get_nomad_job_id(run)

        args = ExecuteRunArgs(
            job_origin=job_origin,
            run_id=run.run_id,
            instance_ref=self._instance.get_ref(),
        )
        command = args.get_command_args()

        payload = "\n".join(command)
        meta = {"IMAGE": docker_image}

        dispatched_job_id = self.nomad_client.dispatch_job(nomad_job_id, payload=payload, meta=meta)

        self._instance.report_engine_event(
            message=(
                f"Dispatched a new resume_run for job `{nomad_job_id}`with dispatched_job_id `{dispatched_job_id}`"
            ),
            dagster_run=run,
            cls=self.__class__,
        )
        self._instance.add_run_tags(
            run.run_id,
            {self.NOMAD_DISPATCHED_JOB_ID_TAG: dispatched_job_id},
        )
