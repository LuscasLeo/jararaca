# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for dependency injection (Container and ProviderSpec).
"""

import pytest

from jararaca.core.providers import ProviderSpec, Token


class TestToken:
    """Test suite for Token class."""

    def test_token_creation(self) -> None:
        """Test that Token can be created with type and name."""

        class MyService:
            pass

        token = Token(MyService, "SERVICE_TOKEN")
        assert token.type_ == MyService
        assert token.name == "SERVICE_TOKEN"

    def test_token_frozen_dataclass(self) -> None:
        """Test that Token is immutable (frozen dataclass)."""

        class MyService:
            pass

        token = Token(MyService, "SERVICE_TOKEN")

        with pytest.raises(AttributeError):
            token.type_ = object  # type: ignore

        with pytest.raises(AttributeError):
            token.name = "NEW_NAME"  # type: ignore

    def test_token_equality(self) -> None:
        """Test Token equality comparison."""

        class MyService:
            pass

        token1 = Token(MyService, "SERVICE_TOKEN")
        token2 = Token(MyService, "SERVICE_TOKEN")
        token3 = Token(MyService, "DIFFERENT_TOKEN")

        assert token1 == token2
        assert token1 != token3


class TestProviderSpec:
    """Test suite for ProviderSpec class."""

    def test_provider_spec_with_use_value(self) -> None:
        """Test ProviderSpec with use_value."""

        class MyService:
            pass

        service_instance = MyService()
        spec = ProviderSpec(provide=MyService, use_value=service_instance)

        assert spec.provide == MyService
        assert spec.use_value == service_instance
        assert spec.use_factory is None
        assert spec.use_class is None

    def test_provider_spec_with_use_factory(self) -> None:
        """Test ProviderSpec with use_factory."""

        class MyService:
            pass

        def factory() -> MyService:
            return MyService()

        spec = ProviderSpec(provide=MyService, use_factory=factory)

        assert spec.provide == MyService
        assert spec.use_factory == factory
        assert spec.use_value is None
        assert spec.use_class is None

    def test_provider_spec_with_use_class(self) -> None:
        """Test ProviderSpec with use_class."""

        class MyInterface:
            pass

        class MyImplementation(MyInterface):
            pass

        spec = ProviderSpec(provide=MyInterface, use_class=MyImplementation)

        assert spec.provide == MyInterface
        assert spec.use_class == MyImplementation
        assert spec.use_value is None
        assert spec.use_factory is None

    def test_provider_spec_with_token(self) -> None:
        """Test ProviderSpec with Token."""

        class MyService:
            pass

        token = Token(MyService, "MY_TOKEN")
        service_instance = MyService()

        spec = ProviderSpec(provide=token, use_value=service_instance)

        assert spec.provide == token
        assert spec.use_value == service_instance

    def test_provider_spec_after_interceptors(self) -> None:
        """Test ProviderSpec with after_interceptors flag."""

        class MyService:
            pass

        spec = ProviderSpec(
            provide=MyService, use_value=MyService(), after_interceptors=True
        )

        assert spec.after_interceptors is True

    def test_provider_spec_default_after_interceptors(self) -> None:
        """Test that after_interceptors defaults to False."""

        class MyService:
            pass

        spec = ProviderSpec(provide=MyService, use_value=MyService())

        assert spec.after_interceptors is False
