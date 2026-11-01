"""Integration tests for employee search, filters, sorting, and pagination."""

import pytest
from fastapi.testclient import TestClient

from tests.conftest import post_employee


@pytest.mark.usefixtures("integration_engine")
def test_filter_by_work_email_and_first_name(api_client: TestClient, sample_department) -> None:
    post_employee(
        api_client,
        sample_department.id,
        employee_number="S1001",
        first_name="UniqueFirst",
        last_name="Alpha",
        work_email="unique.alpha@example.com",
    )
    post_employee(
        api_client,
        sample_department.id,
        employee_number="S1002",
        first_name="Other",
        last_name="Beta",
        work_email="other.beta@example.com",
    )

    by_email = api_client.get("/employees", params={"work_email": "unique.alpha"})
    assert by_email.status_code == 200
    assert by_email.json()["total"] == 1

    by_first = api_client.get("/employees", params={"first_name": "UniqueFirst"})
    assert by_first.json()["total"] == 1
    assert by_first.json()["items"][0]["employee_number"] == "S1001"


@pytest.mark.usefixtures("integration_engine")
def test_filter_by_last_name_and_department(
    api_client: TestClient,
    sample_department,
    db_session,
) -> None:
    from smarthris.repositories.department_repository import DepartmentRepository

    other_dept = DepartmentRepository(db_session).create("Sales")
    db_session.flush()

    post_employee(
        api_client,
        sample_department.id,
        employee_number="S2001",
        last_name="FilterLast",
        work_email="eng.filter@example.com",
    )
    post_employee(
        api_client,
        other_dept.id,
        employee_number="S2002",
        last_name="FilterLast",
        work_email="sales.filter@example.com",
    )

    filtered = api_client.get(
        "/employees",
        params={"last_name": "FilterLast", "department_id": sample_department.id},
    )
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["work_email"] == "eng.filter@example.com"


@pytest.mark.usefixtures("integration_engine")
def test_sort_by_last_name_desc(api_client: TestClient, sample_department) -> None:
    post_employee(
        api_client,
        sample_department.id,
        employee_number="S3001",
        last_name="Zulu",
        work_email="zulu@example.com",
    )
    post_employee(
        api_client,
        sample_department.id,
        employee_number="S3002",
        last_name="Alpha",
        work_email="alpha.sort@example.com",
    )

    response = api_client.get(
        "/employees",
        params={"sort_by": "last_name", "sort_order": "desc", "page_size": 100},
    )
    names = [item["last_name"] for item in response.json()["items"]]
    assert names.index("Zulu") < names.index("Alpha")


@pytest.mark.usefixtures("integration_engine")
def test_pagination_metadata(api_client: TestClient, sample_department) -> None:
    for idx in range(3):
        post_employee(
            api_client,
            sample_department.id,
            employee_number=f"S4{idx:03d}",
            work_email=f"page{idx}@example.com",
        )

    page1 = api_client.get("/employees", params={"page": 1, "page_size": 2})
    body = page1.json()
    assert body["total"] == 3
    assert body["pages"] == 2
    assert len(body["items"]) == 2

    page2 = api_client.get("/employees", params={"page": 2, "page_size": 2})
    assert len(page2.json()["items"]) == 1


@pytest.mark.usefixtures("integration_engine")
def test_legacy_name_and_employee_number_filters_still_work(
    api_client: TestClient,
    sample_department,
) -> None:
    post_employee(
        api_client,
        sample_department.id,
        employee_number="LEG-001",
        first_name="Legacy",
        last_name="Match",
        work_email="legacy@example.com",
    )
    by_number = api_client.get("/employees", params={"employee_number": "LEG-0"})
    assert by_number.json()["total"] == 1
    by_name = api_client.get("/employees", params={"name": "Match"})
    assert by_name.json()["total"] == 1
