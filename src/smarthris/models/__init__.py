"""ORM models (imported by Alembic for autogenerate metadata)."""

from smarthris.models.course import Course, CourseStatus
from smarthris.models.course_enrolment import CourseEnrolment, CourseEnrolmentStatus
from smarthris.models.department import Department
from smarthris.models.employee import Employee, EmployeeStatus
from smarthris.models.hr_document import (
    HrDocument,
    HrDocumentStatus,
    HrDocumentType,
    HrDocumentVisibility,
)
from smarthris.models.hr_document_chunk import HrDocumentChunk
from smarthris.models.import_error import ImportError
from smarthris.models.import_job import (
    ImportFileType,
    ImportJob,
    ImportJobStatus,
    ImportTarget,
)
from smarthris.models.user import User, UserRole

__all__ = [
    "Course",
    "CourseEnrolment",
    "CourseEnrolmentStatus",
    "CourseStatus",
    "Department",
    "Employee",
    "EmployeeStatus",
    "HrDocument",
    "HrDocumentChunk",
    "HrDocumentStatus",
    "HrDocumentType",
    "HrDocumentVisibility",
    "ImportError",
    "ImportFileType",
    "ImportJob",
    "ImportJobStatus",
    "ImportTarget",
    "User",
    "UserRole",
]
