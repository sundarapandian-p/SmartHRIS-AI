"""Integration tests for manager relationships and reporting hierarchy."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from smarthris.core.constants import HIERARCHY_MAX_DEPTH
from tests.conftest import post_employee


@pytest.mark.usefixtures("integration_engine")
def test_manager_null_when_no_manager(api_client: TestClient, sample_department) -> None:
    created = post_employee(
        api_client,
        sample_department.id,
        employee_number="O1001",
        work_email="solo@example.com",
    )
    response = api_client.get(f"/employees/{created['id']}/manager")
    assert response.status_code == 200
    assert response.json() == {"manager": None}


@pytest.mark.usefixtures("integration_engine")
def test_manager_returns_profile(api_client: TestClient, sample_department) -> None:
    boss = post_employee(
        api_client,
        sample_department.id,
        employee_number="O2001",
        first_name="Boss",
        work_email="boss@example.com",
    )
    report = post_employee(
        api_client,
        sample_department.id,
        employee_number="O2002",
        work_email="report@example.com",
        manager_employee_id=boss["id"],
    )
    response = api_client.get(f"/employees/{report['id']}/manager")
    assert response.status_code == 200
    assert response.json()["manager"]["id"] == boss["id"]


@pytest.mark.usefixtures("integration_engine")
def test_manager_missing_employee_404(api_client: TestClient) -> None:
    assert api_client.get("/employees/999999/manager").status_code == 404


@pytest.mark.usefixtures("integration_engine")
def test_direct_reports_list(api_client: TestClient, sample_department) -> None:
    lead = post_employee(
        api_client,
        sample_department.id,
        employee_number="O3001",
        work_email="lead@example.com",
    )
    post_employee(
        api_client,
        sample_department.id,
        employee_number="O3002",
        work_email="dr1@example.com",
        manager_employee_id=lead["id"],
    )
    post_employee(
        api_client,
        sample_department.id,
        employee_number="O3003",
        work_email="dr2@example.com",
        manager_employee_id=lead["id"],
    )

    response = api_client.get(f"/employees/{lead['id']}/direct-reports")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


@pytest.mark.usefixtures("integration_engine")
def test_direct_reports_limit_exceeded(api_client: TestClient, sample_department) -> None:
    lead = post_employee(
        api_client,
        sample_department.id,
        employee_number="O4001",
        work_email="limit.lead@example.com",
    )
    with patch("smarthris.services.employee_service.DIRECT_REPORTS_MAX", 2):
        post_employee(
            api_client,
            sample_department.id,
            employee_number="O4002",
            work_email="limit1@example.com",
            manager_employee_id=lead["id"],
        )
        post_employee(
            api_client,
            sample_department.id,
            employee_number="O4003",
            work_email="limit2@example.com",
            manager_employee_id=lead["id"],
        )
        post_employee(
            api_client,
            sample_department.id,
            employee_number="O4004",
            work_email="limit3@example.com",
            manager_employee_id=lead["id"],
        )
        response = api_client.get(f"/employees/{lead['id']}/direct-reports")
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


@pytest.mark.usefixtures("integration_engine")
def test_reporting_hierarchy_nested_tree(api_client: TestClient, sample_department) -> None:
    root = post_employee(
        api_client,
        sample_department.id,
        employee_number="O5001",
        work_email="root@example.com",
    )
    child = post_employee(
        api_client,
        sample_department.id,
        employee_number="O5002",
        work_email="child@example.com",
        manager_employee_id=root["id"],
    )
    post_employee(
        api_client,
        sample_department.id,
        employee_number="O5003",
        work_email="grandchild@example.com",
        manager_employee_id=child["id"],
    )

    response = api_client.get(f"/employees/{root['id']}/reporting-hierarchy")
    assert response.status_code == 200
    body = response.json()
    assert body["employee_id"] == root["id"]
    assert len(body["direct_reports"]) == 1
    assert body["direct_reports"][0]["employee"]["employee_number"] == "O5002"
    assert len(body["direct_reports"][0]["direct_reports"]) == 1
    assert (
        body["direct_reports"][0]["direct_reports"][0]["employee"]["employee_number"]
        == "O5003"
    )


@pytest.mark.usefixtures("integration_engine")
def test_reporting_hierarchy_excludes_root_employee(
    api_client: TestClient,
    sample_department,
) -> None:
    root = post_employee(
        api_client,
        sample_department.id,
        employee_number="O5101",
        work_email="only.root@example.com",
    )
    post_employee(
        api_client,
        sample_department.id,
        employee_number="O5102",
        work_email="only.child@example.com",
        manager_employee_id=root["id"],
    )

    body = api_client.get(f"/employees/{root['id']}/reporting-hierarchy").json()

    def collect_ids(nodes: list) -> set[int]:
        ids: set[int] = set()
        for node in nodes:
            ids.add(node["employee"]["id"])
            ids.update(collect_ids(node["direct_reports"]))
        return ids

    assert root["id"] not in collect_ids(body["direct_reports"])


@pytest.mark.usefixtures("integration_engine")
def test_reporting_hierarchy_depth_limit_returns_error_not_partial_tree(
    api_client: TestClient,
    sample_department,
) -> None:
    root = post_employee(
        api_client,
        sample_department.id,
        employee_number="O5200",
        work_email="depth.root@example.com",
    )
    manager_id = root["id"]
    for level in range(1, HIERARCHY_MAX_DEPTH + 2):
        report = post_employee(
            api_client,
            sample_department.id,
            employee_number=f"O52{level:02d}",
            work_email=f"depth.level{level}@example.com",
            manager_employee_id=manager_id,
        )
        manager_id = report["id"]

    response = api_client.get(f"/employees/{root['id']}/reporting-hierarchy")
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert "depth" in response.json()["detail"].lower()


@pytest.mark.usefixtures("integration_engine")
def test_hierarchy_node_limit(api_client: TestClient, sample_department) -> None:
    root = post_employee(
        api_client,
        sample_department.id,
        employee_number="O6001",
        work_email="hroot@example.com",
    )
    post_employee(
        api_client,
        sample_department.id,
        employee_number="O6002",
        work_email="hchild1@example.com",
        manager_employee_id=root["id"],
    )
    post_employee(
        api_client,
        sample_department.id,
        employee_number="O6003",
        work_email="hchild2@example.com",
        manager_employee_id=root["id"],
    )
    with patch("smarthris.services.employee_service.HIERARCHY_MAX_NODES", 1):
        response = api_client.get(f"/employees/{root['id']}/reporting-hierarchy")
    assert response.status_code == 400


@pytest.mark.usefixtures("integration_engine")
def test_self_manager_on_update_rejected(api_client: TestClient, sample_department) -> None:
    emp = post_employee(
        api_client,
        sample_department.id,
        employee_number="O7001",
        work_email="self@example.com",
    )
    response = api_client.patch(
        f"/employees/{emp['id']}",
        json={"manager_employee_id": emp["id"]},
    )
    assert response.status_code == 400


@pytest.mark.usefixtures("integration_engine")
def test_reporting_cycle_on_update_rejected(api_client: TestClient, sample_department) -> None:
    manager = post_employee(
        api_client,
        sample_department.id,
        employee_number="O8001",
        work_email="mgr@example.com",
    )
    report = post_employee(
        api_client,
        sample_department.id,
        employee_number="O8002",
        work_email="rep@example.com",
        manager_employee_id=manager["id"],
    )
    response = api_client.patch(
        f"/employees/{manager['id']}",
        json={"manager_employee_id": report["id"]},
    )
    assert response.status_code == 400
    assert "cycle" in response.json()["detail"].lower()


@pytest.mark.usefixtures("integration_engine")
def test_invalid_manager_on_create_returns_404(api_client: TestClient, sample_department) -> None:
    response = api_client.post(
        "/employees",
        json={
            "employee_number": "O9001",
            "first_name": "Bad",
            "last_name": "Manager",
            "work_email": "bad.mgr@example.com",
            "department_id": sample_department.id,
            "manager_employee_id": 999999,
        },
    )
    assert response.status_code == 404


@pytest.mark.usefixtures("integration_engine")
def test_clear_manager_with_null_patch(api_client: TestClient, sample_department) -> None:
    boss = post_employee(
        api_client,
        sample_department.id,
        employee_number="O9101",
        work_email="boss2@example.com",
    )
    report = post_employee(
        api_client,
        sample_department.id,
        employee_number="O9102",
        work_email="report2@example.com",
        manager_employee_id=boss["id"],
    )
    cleared = api_client.patch(
        f"/employees/{report['id']}",
        json={"manager_employee_id": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["manager_employee_id"] is None
