# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Example demonstrating Jararaca configuration system.

The const module provides internal configuration controlled via environment variables.
Library users should extend framework classes (like PaginatedFilter) rather than
importing constants directly.
"""

from jararaca import PaginatedFilter, const

# ==========================================
# Library User Patterns (Recommended)
# ==========================================


# Example 1: Extending PaginatedFilter (typical usage)
class ProductFilter(PaginatedFilter):
    """User-defined filter extending framework's PaginatedFilter.

    Automatically respects:
    - JARARACA_PAGINATION_DEFAULT_PAGE_SIZE
    - JARARACA_PAGINATION_MAX_PAGE_SIZE
    - JARARACA_PAGINATION_DEFAULT_PAGE
    """

    search: str | None = None
    category: str | None = None
    min_price: float | None = None
    max_price: float | None = None


# ==========================================
# Framework Inspection (For Reference)
# ==========================================


def print_configuration() -> None:
    """Display current framework configuration.

    This shows the internal configuration values. Library users typically
    don't need to access these directly - they're controlled via environment
    variables and respected by framework classes.
    """
    print("=== Jararaca Internal Configuration ===")
    print("\nPagination (used by PaginatedFilter):")
    print(f"  Default page: {const.PAGINATION_DEFAULT_PAGE}")
    print(f"  Default page size: {const.PAGINATION_DEFAULT_PAGE_SIZE}")
    print(f"  Max page size: {const.PAGINATION_MAX_PAGE_SIZE}")

    print("\nDatabase (used by AIOSqlAlchemySessionInterceptor):")
    print(f"  Pool size: {const.DB_POOL_SIZE}")
    print(f"  Max overflow: {const.DB_MAX_OVERFLOW}")
    print(f"  Pool timeout: {const.DB_POOL_TIMEOUT}s")

    print("\nHTTP RPC (used by HTTPXHttpRPCAsyncBackend):")
    print(f"  Default timeout: {const.HTTP_RPC_DEFAULT_TIMEOUT}s")
    print(f"  Retry attempts: {const.HTTP_RPC_DEFAULT_RETRY_ATTEMPTS}")

    print("\nMessage Bus (used by worker infrastructure):")
    print(f"  Prefetch count: {const.MESSAGEBUS_DEFAULT_PREFETCH_COUNT}")
    print(f"  Connection retry max: {const.MESSAGEBUS_CONNECTION_RETRY_MAX}")

    print("\nScheduler (used by beat worker):")
    print(f"  Max concurrent jobs: {const.SCHEDULER_MAX_CONCURRENT_JOBS}")
    print(f"  Default timezone: {const.DEFAULT_TIMEZONE}")


if __name__ == "__main__":
    print("=== Configuration Example ===\n")

    print("1. Framework Configuration:")
    print_configuration()

    print("\n\n2. User Pattern - Extending PaginatedFilter:")
    filter_obj = ProductFilter(search="laptop", category="electronics")
    print(f"   ProductFilter instance:")
    print(f"   - page: {filter_obj.page}")
    print(
        f"   - page_size: {filter_obj.page_size} (respects JARARACA_PAGINATION_DEFAULT_PAGE_SIZE)"
    )
    print(f"   - search: {filter_obj.search}")
    print(f"   - category: {filter_obj.category}")

    print("\n\n3. Try setting environment variables:")
    print("   export JARARACA_PAGINATION_DEFAULT_PAGE_SIZE=25")
    print("   export JARARACA_PAGINATION_MAX_PAGE_SIZE=200")
    print("   export JARARACA_DEFAULT_TIMEZONE=America/New_York")
    print("   python examples/const_example.py")
