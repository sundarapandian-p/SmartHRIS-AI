"""Integration tests for employee CRUD (require MySQL test database)."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.usefixtures("integration_engine")
def test_create_and_get_employee(api_client: TestClient, sample_department) -> None:
    payload = {
        "employee_number": "E1001",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "work_email": "ada.lovelace@example.com",
        "department_id": sample_department.id,
        "job_title": "Engineer",
        "status": "active",
    }
    create_response = api_client.post("/employees", json=payload)
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["employee_number"] == "E1001"
    assert created["department_id"] == sample_department.id

    get_response = api_client.get(f"/employees/{created['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["work_email"] == "ada.lovelace@example.com"


@pytest.mark.usefixtures("integration_engine")
def test_list_employees_with_filters(api_client: TestClient, sample_department) -> None:
    api_client.post(
        "/employees",
        json={
            "employee_number": "E2001",
            "first_name": "Grace",
            "last_name": "Hopper",
            "work_email": "grace.hopper@example.com",
            "department_id": sample_department.id,
            "status": "active",
        },
    )
    api_client.post(
        "/employees",
        json={
            "employee_number": "E2002",
            "first_name": "Alan",
            "last_name": "Turing",
            "work_email": "alan.turing@example.com",
            "department_id": sample_department.id,
            "status": "inactive",
        },
    )

    by_name = api_client.get("/employees", params={"name": "Hopper"})
    assert by_name.status_code == 200
    body = by_name.json()
    assert body["total"] == 1
    assert body["items"][0]["last_name"] == "Hopper"

    by_status = api_client.get("/employees", params={"status": "inactive"})
    assert by_status.json()["total"] == 1
    assert by_status.json()["items"][0]["employee_number"] == "E2002"


@pytest.mark.usefixtures("integration_engine")
def test_patch_employee(api_client: TestClient, sample_department) -> None:
    created = api_client.post(
        "/employees",
        json={
            "employee_number": "E3001",
            "first_name": "Katherine",
            "last_name": "Johnson",
            "work_email": "katherine.johnson@example.com",
            "department_id": sample_department.id,
        },
    ).json()

    patch_response = api_client.patch(
        f"/employees/{created['id']}",
        json={"job_title": "Mathematician", "status": "inactive"},
    )
    assert patch_response.status_code == 200
    updated = patch_response.json()
    assert updated["job_title"] == "Mathematician"
    assert updated["status"] == "inactive"


@pytest.mark.usefixtures("integration_engine")
def test_duplicate_employee_number_returns_409(
    api_client: TestClient,
    sample_department,
) -> None:
    body = {
        "employee_number": "E4001",
        "first_name": "Duplicate",
        "last_name": "One",
        "work_email": "dup.one@example.com",
        "department_id": sample_department.id,
    }
    assert api_client.post("/employees", json=body).status_code == 201
    body["work_email"] = "dup.two@example.com"
    conflict = api_client.post("/employees", json=body)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "conflict"


@pytest.mark.usefixtures("integration_engine")
def test_duplicate_work_email_returns_409_and_single_row_persisted(
    api_client: TestClient,
    sample_department,
) -> None:
    first = {
        "employee_number": "E4001A",
        "first_name": "Duplicate",
        "last_name": "Email",
        "work_email": "shared.email@example.com",
        "department_id": sample_department.id,
    }
    second = {
        "employee_number": "E4001B",
        "first_name": "Another",
        "last_name": "Person",
        "work_email": "shared.email@example.com",
        "department_id": sample_department.id,
    }
    assert api_client.post("/employees", json=first).status_code == 201
    conflict = api_client.post("/employees", json=second)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "conflict"

    listed = api_client.get("/employees", params={"work_email": "shared.email"})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["employee_number"] == "E4001A"


@pytest.mark.usefixtures("integration_engine")
def test_get_missing_employee_returns_404(api_client: TestClient) -> None:
    response = api_client.get("/employees/999999")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


@pytest.mark.usefixtures("integration_engine")
def test_create_with_unknown_department_returns_404(api_client: TestClient) -> None:
    response = api_client.post(
        "/employees",
        json={
            "employee_number": "E5001",
            "first_name": "No",
            "last_name": "Dept",
            "work_email": "no.dept@example.com",
            "department_id": 999999,
        },
    )
    assert response.status_code == 404
