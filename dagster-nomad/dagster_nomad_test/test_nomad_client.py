import httpx2
import pytest

from dagster_nomad.run_launcher import NomadClient, NomadTaskState

ALLOCATIONS_RESPONSE = [
    {
        "ClientDescription": "All tasks have completed",
        "ClientStatus": "complete",
        "CreateIndex": 19055340,
        "CreateTime": 1784617507710696154,
        "DeploymentStatus": None,
        "DesiredDescription": "",
        "DesiredStatus": "run",
        "DesiredTransition": {
            "ForceReschedule": None,
            "Migrate": None,
            "MigrateDisablePlacement": None,
            "NoShutdownDelay": None,
            "Reschedule": None,
        },
        "EvalID": "71b9dc12-9d5b-2908-0dc1-2f54a94d49e9",
        "FollowupEvalID": "",
        "ID": "c7fda1f4-e05d-e113-cb7e-0f7956fab617",
        "JobID": "dagster-executor-ml/dispatch-1784617507-97b7695f",
        "JobType": "batch",
        "JobVersion": 20,
        "ModifyIndex": 19055347,
        "ModifyTime": 1784617541654898178,
        "Name": "dagster-executor-ml/dispatch-1784617507-97b7695f.dagster-executor-ml[0]",
        "Namespace": "acceptance",
        "NextAllocation": "",
        "NodeID": "3be0515c-122f-4bc0-8c03-07d1607caf84",
        "NodeName": "nomad-scw-03",
        "PreemptedAllocations": None,
        "PreemptedByAllocation": "",
        "RescheduleTracker": None,
        "TaskGroup": "dagster-executor-ml",
        "TaskStates": {
            "knowledge": {
                "Events": [
                    {
                        "Details": {},
                        "DiskLimit": 0,
                        "DisplayMessage": "Task received by client",
                        "DownloadError": "",
                        "DriverError": "",
                        "DriverMessage": "",
                        "ExitCode": 0,
                        "FailedSibling": "",
                        "FailsTask": False,
                        "GenericSource": "",
                        "KillError": "",
                        "KillReason": "",
                        "KillTimeout": 0,
                        "Message": "",
                        "RestartReason": "",
                        "SetupError": "",
                        "Signal": 0,
                        "StartDelay": 0,
                        "TaskSignal": "",
                        "TaskSignalReason": "",
                        "Time": 1784617507754612810,
                        "Type": "Received",
                        "ValidationError": "",
                        "VaultError": "",
                    },
                    {
                        "Details": {"message": "Building Task Directory"},
                        "DiskLimit": 0,
                        "DisplayMessage": "Building Task Directory",
                        "DownloadError": "",
                        "DriverError": "",
                        "DriverMessage": "",
                        "ExitCode": 0,
                        "FailedSibling": "",
                        "FailsTask": False,
                        "GenericSource": "",
                        "KillError": "",
                        "KillReason": "",
                        "KillTimeout": 0,
                        "Message": "Building Task Directory",
                        "RestartReason": "",
                        "SetupError": "",
                        "Signal": 0,
                        "StartDelay": 0,
                        "TaskSignal": "",
                        "TaskSignalReason": "",
                        "Time": 1784617508831539043,
                        "Type": "Task Setup",
                        "ValidationError": "",
                        "VaultError": "",
                    },
                    {
                        "Details": {},
                        "DiskLimit": 0,
                        "DisplayMessage": "Task started by client",
                        "DownloadError": "",
                        "DriverError": "",
                        "DriverMessage": "",
                        "ExitCode": 0,
                        "FailedSibling": "",
                        "FailsTask": False,
                        "GenericSource": "",
                        "KillError": "",
                        "KillReason": "",
                        "KillTimeout": 0,
                        "Message": "",
                        "RestartReason": "",
                        "SetupError": "",
                        "Signal": 0,
                        "StartDelay": 0,
                        "TaskSignal": "",
                        "TaskSignalReason": "",
                        "Time": 1784617510503282729,
                        "Type": "Started",
                        "ValidationError": "",
                        "VaultError": "",
                    },
                    {
                        "Details": {"signal": "0", "oom_killed": "false", "exit_code": "0"},
                        "DiskLimit": 0,
                        "DisplayMessage": "Exit Code: 0",
                        "DownloadError": "",
                        "DriverError": "",
                        "DriverMessage": "",
                        "ExitCode": 0,
                        "FailedSibling": "",
                        "FailsTask": False,
                        "GenericSource": "",
                        "KillError": "",
                        "KillReason": "",
                        "KillTimeout": 0,
                        "Message": "",
                        "RestartReason": "",
                        "SetupError": "",
                        "Signal": 0,
                        "StartDelay": 0,
                        "TaskSignal": "",
                        "TaskSignalReason": "",
                        "Time": 1784617541186143602,
                        "Type": "Terminated",
                        "ValidationError": "",
                        "VaultError": "",
                    },
                ],
                "Failed": False,
                "FinishedAt": "2026-07-21T07:05:41.454884283Z",
                "LastRestart": None,
                "Paused": "",
                "Restarts": 0,
                "StartedAt": "2026-07-21T07:05:10.503341869Z",
                "State": "dead",
            }
        },
    }
]


@pytest.fixture
def allocations_response():
    return ALLOCATIONS_RESPONSE


def allocation(create_index: int, state: str = "running", failed: bool = False) -> dict:
    """Build the subset of an allocation payload `get_job_status` relies on."""
    return {
        "ID": f"allocation-{create_index}",
        "CreateIndex": create_index,
        "TaskStates": {"knowledge": {"State": state, "Failed": failed}},
    }


def client_returning(response: httpx2.Response, expected_path: str = "/v1/job/test_job/allocations") -> NomadClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == expected_path
        return response

    return NomadClient(url="http://nomad.example.com", transport=httpx2.MockTransport(handler))


class TestNomadClientGetJobStatus:
    def test_get_job_status(self, allocations_response):
        client = client_returning(httpx2.Response(200, json=allocations_response))

        alloc_id, state, failed = client.get_job_status("test_job")

        assert alloc_id == "c7fda1f4-e05d-e113-cb7e-0f7956fab617"
        assert state == "dead"
        assert failed is False

    def test_get_job_status_when_job_is_garbage_collected(self):
        """Nomad drops the job itself once `job_gc_threshold` is reached."""
        client = client_returning(httpx2.Response(404, text="job not found"))

        assert client.get_job_status("test_job") is None

    def test_get_job_status_when_allocations_are_garbage_collected(self):
        """`alloc_gc_threshold` is shorter than `job_gc_threshold`, so the job can outlive its allocations."""
        client = client_returning(httpx2.Response(200, json=[]))

        assert client.get_job_status("test_job") is None

    def test_get_job_status_uses_the_most_recent_allocation(self):
        """A rescheduled job keeps its previous allocations, which are not ordered by the API.

        The stale allocations are listed first on purpose: reporting one of them would mark
        a healthy run as failed.
        """
        client = client_returning(
            httpx2.Response(
                200,
                json=[
                    allocation(19055340, state="dead", failed=True),
                    allocation(19055342, state="running"),
                    allocation(19055341, state="dead", failed=True),
                ],
            )
        )

        assert client.get_job_status("test_job") == ("allocation-19055342", NomadTaskState.RUNNING, False)

    def test_get_job_status_when_task_has_not_started_yet(self):
        """An allocation is created before the task is received by a client."""
        client = client_returning(httpx2.Response(200, json=[{"ID": "alloc", "CreateIndex": 1, "TaskStates": None}]))

        assert client.get_job_status("test_job") == ("alloc", NomadTaskState.PENDING, False)

    def test_get_job_status_with_a_state_outside_of_the_documented_ones(self):
        """Nomad documents three task states, a fourth one means the API changed and
        must be raised rather than guessed.
        """
        client = client_returning(httpx2.Response(200, json=[allocation(1, state="something_else")]))

        with pytest.raises(ValueError):
            client.get_job_status("test_job")

    def test_get_job_status_raises_on_server_error(self):
        client = client_returning(httpx2.Response(500, text="rpc error"))

        with pytest.raises(httpx2.HTTPStatusError):
            client.get_job_status("test_job")
