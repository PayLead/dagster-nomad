from __future__ import annotations

import base64
from collections.abc import Generator
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any, ClassVar, Optional

import httpx2
from dagster import Array, Field, Map, Shape, StringSource
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

    def get_job_status(self, job_id: str) -> tuple[str, str, bool]:
        """Retrieve the status of the provided job id.

        Args:
          job_id: The id of the job

        Returns:
            A tuple with `state` and `failed`
        """

        res = self.get(f"/v1/job/{job_id}/allocations")
        res.raise_for_status()

        data = res.json()
        # There is no reason to have multiple task inside a dagster job (from nomad point of view)
        # so we use the first one
        task = list(data[0]["TaskStates"])[0]
        state: str = data[0]["TaskStates"][task]["State"]
        failed: bool = data[0]["TaskStates"][task]["Failed"]

        alloc_id = data[0]["ID"]

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


DEFAULT_CLUSTER: str = "default"


class UnknownNomadClusterError(Exception):
    """Raised when a run references a Nomad cluster missing from the configuration."""


@dataclass(frozen=True)
class NomadCluster:
    """Connection profile of a Nomad cluster."""

    name: str
    url: str
    job_id: str
    token: str | None = None
    namespace: str | None = None
    job_id_mapping: dict[str, str] = field(default_factory=dict)

    def resolve_job_id(self, location_name: str | None) -> str:
        if location_name is None:
            return self.job_id
        return self.job_id_mapping.get(location_name, self.job_id)


@dataclass(frozen=True)
class NomadClusterRule:
    """Select a cluster when the code location and/or the Dagster job name match (shell-style globs)."""

    cluster: str
    location: str | None = None
    job: str | None = None

    def matches(self, location_name: str | None, job_name: str | None) -> bool:
        if self.location is not None and (location_name is None or not fnmatchcase(location_name, self.location)):
            return False
        if self.job is not None and (job_name is None or not fnmatchcase(job_name, self.job)):
            return False
        return True


def _cluster_config_type() -> Shape:
    return Shape(
        {
            "url": Field(StringSource, is_required=True, description="The Nomad HTTP API URL of this cluster."),
            "token": Field(StringSource, is_required=False, description="The Nomad token of this cluster."),
            "namespace": Field(StringSource, is_required=False, description="The Nomad namespace of the job."),
            "job_id": Field(
                StringSource,
                is_required=False,
                description="The Nomad job ID to dispatch on this cluster, defaults to the top-level job_id.",
            ),
            "job_id_mapping": Field(
                Map(str, str),
                is_required=False,
                description="An optional mapping between code location name and nomad job for this cluster.",
            ),
        }
    )


class NomadRunLauncher(RunLauncher[T_DagsterInstance], ConfigurableClass):
    """RunLauncher that starts a job in Nomad for each Dagster job run."""

    NOMAD_DISPATCHED_JOB_ID_TAG: ClassVar[str] = "nomad_dispatched_job_id"
    NOMAD_CLUSTER_TAG: ClassVar[str] = "nomad_cluster"
    # Set on a job (or a run) to force its cluster, bypassing cluster_rules.
    CLUSTER_OVERRIDE_TAG: ClassVar[str] = "dagster-nomad/cluster"

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
        clusters: dict[str, dict[str, Any]] | None = None,
        cluster_rules: list[dict[str, Any]] | None = None,
    ):
        self._inst_data = inst_data

        self.docker_image = docker_image

        self.clusters: dict[str, NomadCluster] = {
            DEFAULT_CLUSTER: NomadCluster(
                name=DEFAULT_CLUSTER,
                url=url,
                job_id=job_id,
                token=token,
                namespace=namespace,
                job_id_mapping=job_id_mapping or {},
            )
        }
        for name, cluster_config in (clusters or {}).items():
            if name == DEFAULT_CLUSTER:
                raise ValueError(
                    f"Cluster name `{DEFAULT_CLUSTER}` is reserved, use the top-level configuration instead."
                )
            self.clusters[name] = NomadCluster(
                name=name,
                url=cluster_config["url"],
                job_id=cluster_config.get("job_id") or job_id,
                token=cluster_config.get("token"),
                namespace=cluster_config.get("namespace"),
                job_id_mapping=cluster_config.get("job_id_mapping") or {},
            )

        self.cluster_rules: list[NomadClusterRule] = []
        for rule_config in cluster_rules or []:
            rule = NomadClusterRule(**rule_config)
            if rule.cluster not in self.clusters:
                raise ValueError(f"Cluster rule references unknown cluster `{rule.cluster}`.")
            if rule.location is None and rule.job is None:
                raise ValueError(f"Cluster rule for `{rule.cluster}` must define at least `location` or `job`.")
            self.cluster_rules.append(rule)

        self.nomad_clients: dict[str, NomadClient] = {
            name: NomadClient(cluster.url, cluster.token, cluster.namespace) for name, cluster in self.clusters.items()
        }

        super().__init__()

    def __del__(self):
        for client in getattr(self, "nomad_clients", {}).values():
            client.close()

    @property
    def nomad_client(self) -> NomadClient:
        """Client of the default cluster."""
        return self.nomad_clients[DEFAULT_CLUSTER]

    @nomad_client.setter
    def nomad_client(self, client: NomadClient) -> None:
        self.nomad_clients[DEFAULT_CLUSTER] = client

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
            "clusters": Field(
                Map(str, _cluster_config_type()),
                is_required=False,
                description=(
                    "Additional named Nomad clusters. The top-level configuration is the `default` cluster, "
                    "used when no cluster rule matches."
                ),
            ),
            "cluster_rules": Field(
                Array(
                    Shape(
                        {
                            "cluster": Field(str, is_required=True, description="Name of the cluster to use."),
                            "location": Field(
                                str, is_required=False, description="Glob matched against the code location name."
                            ),
                            "job": Field(
                                str, is_required=False, description="Glob matched against the Dagster job name."
                            ),
                        }
                    )
                ),
                is_required=False,
                description=(
                    "Ordered rules selecting the cluster of a run, the first matching rule wins. "
                    "When a rule defines both `location` and `job`, both must match."
                ),
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

    @staticmethod
    def _get_location_name(run: DagsterRun) -> str | None:
        remote_job_origin = run.remote_job_origin
        return remote_job_origin.location_name if remote_job_origin is not None else None

    def _select_cluster(self, run: DagsterRun) -> NomadCluster:
        """Select the cluster on which a new run must be dispatched.

        Precedence: the `dagster-nomad/cluster` tag, then the first matching cluster rule, then `default`.
        """
        requested = run.tags.get(self.CLUSTER_OVERRIDE_TAG)
        if requested is not None:
            if requested not in self.clusters:
                raise UnknownNomadClusterError(
                    f"Tag `{self.CLUSTER_OVERRIDE_TAG}` requests unknown Nomad cluster `{requested}`, "
                    f"available clusters: {', '.join(sorted(self.clusters))}."
                )
            return self.clusters[requested]

        location_name = self._get_location_name(run)
        for rule in self.cluster_rules:
            if rule.matches(location_name, run.job_name):
                return self.clusters[rule.cluster]
        return self.clusters[DEFAULT_CLUSTER]

    def _get_run_cluster(self, run: DagsterRun) -> str:
        """Return the cluster on which an already dispatched run lives."""
        # Runs dispatched before multi-cluster support have no tag: they live on the default cluster.
        cluster = run.tags.get(self.NOMAD_CLUSTER_TAG, DEFAULT_CLUSTER)
        if cluster not in self.nomad_clients:
            raise UnknownNomadClusterError(f"Run `{run.run_id}` was dispatched on unknown Nomad cluster `{cluster}`.")
        return cluster

    def _dispatch(self, run: DagsterRun, payload: str, docker_image: str, kind: str) -> None:
        cluster = self._select_cluster(run)
        nomad_job_id = cluster.resolve_job_id(self._get_location_name(run))

        meta = {"IMAGE": docker_image}
        dispatched_job_id = self.nomad_clients[cluster.name].dispatch_job(nomad_job_id, payload=payload, meta=meta)

        self._instance.report_engine_event(
            message=(
                f"Dispatched a new {kind} for job `{nomad_job_id}` on cluster `{cluster.name}` "
                f"with dispatched_job_id `{dispatched_job_id}`"
            ),
            dagster_run=run,
            cls=self.__class__,
        )
        self._instance.add_run_tags(
            run.run_id,
            {
                self.NOMAD_DISPATCHED_JOB_ID_TAG: dispatched_job_id,
                self.NOMAD_CLUSTER_TAG: cluster.name,
            },
        )

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

        self._dispatch(run, payload, docker_image, kind="run")

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
        self.nomad_clients[self._get_run_cluster(run)].stop_job(dispatched_job_id)

        return True

    @property
    def supports_check_run_worker_health(self) -> bool:
        return True

    def check_run_worker_health(self, run: DagsterRun) -> CheckRunHealthResult:
        dispatched_job_id = run.tags.get(self.NOMAD_DISPATCHED_JOB_ID_TAG)

        try:
            cluster = self._get_run_cluster(run)
            alloc_id, state, failed = self.nomad_clients[cluster].get_job_status(dispatched_job_id)
        except (httpx2.HTTPError, UnknownNomadClusterError) as exc:
            self._instance.report_engine_event(
                message=f"Failed to get run status of dispatched_job_id `{dispatched_job_id}`: `{exc}`",
                dagster_run=run,
                cls=self.__class__,
            )
            return CheckRunHealthResult(WorkerStatus.NOT_FOUND)

        match (state, failed):
            case ("running", _):
                self._instance.report_engine_event(
                    message=f"Job is running: `com.hashicorp.nomad.alloc_id: {alloc_id}`",
                    dagster_run=run,
                    cls=self.__class__,
                )
                return CheckRunHealthResult(WorkerStatus.RUNNING)
            case ("dead", True):
                return CheckRunHealthResult(WorkerStatus.FAILED)
            case ("dead", False):
                return CheckRunHealthResult(WorkerStatus.SUCCESS)
            case _:
                self._instance.report_engine_event(
                    message=f"Failed to get run status of dispatched_job_id `{dispatched_job_id}`",
                    dagster_run=run,
                    cls=self.__class__,
                )
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

        self._dispatch(run, payload, docker_image, kind="resume_run")
