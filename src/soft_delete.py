"""
Soft Delete ORM Utilities

Provides query filtering for soft-deleted records using SQLAlchemy.
"""

from sqlalchemy.orm import Query
from sqlalchemy import and_


def filter_deleted(query: Query) -> Query:
    """Filter out soft-deleted records from query"""
    # This will be applied to the model dynamically
    return query.filter_by(is_deleted=0)


def only_deleted(query: Query) -> Query:
    """Filter to show only soft-deleted records"""
    return query.filter_by(is_deleted=1)
