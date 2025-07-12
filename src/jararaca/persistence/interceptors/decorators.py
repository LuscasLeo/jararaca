from jararaca.persistence.interceptors.constants import DEFAULT_CONNECTION_NAME
from jararaca.reflect.metadata import SetMetadata

INJECT_PERSISTENCE_SESSION_METADATA_TEMPLATE = (
    "inject_persistence_template_{connection_name}"
)


def set_use_persistence_session(
    inject: bool, connection_name: str = DEFAULT_CONNECTION_NAME
) -> SetMetadata:
    """
    Set whether to inject the connection metadata for the given connection name.
    This is useful when you want to control whether the connection metadata
    should be injected into the context or not.
    """

    return SetMetadata(
        INJECT_PERSISTENCE_SESSION_METADATA_TEMPLATE.format(
            connection_name=connection_name
        ),
        inject,
    )


def uses_persistence_session(
    connection_name: str = DEFAULT_CONNECTION_NAME,
) -> SetMetadata:
    """
    Use connection metadata for the given connection name.
    This is useful when you want to inject the connection metadata into the context,
    for example, when you are using a specific connection for a specific operation.
    """
    return set_use_persistence_session(True, connection_name=connection_name)


def skip_persistence_session(
    connection_name: str = DEFAULT_CONNECTION_NAME,
) -> SetMetadata:
    """
    Decorator to skip using connection metadata for the given connection name.
    This is useful when you want to ensure that the connection metadata is not injected
    into the context, for example, when you are using a different connection for a specific operation.
    """
    return set_use_persistence_session(False, connection_name=connection_name)
