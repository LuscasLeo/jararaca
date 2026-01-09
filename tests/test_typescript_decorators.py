# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for TypeScript interface generation decorators.
"""

from pydantic import BaseModel

from jararaca.tools.typescript.decorators import (
    ExposeType,
    MutationEndpoint,
    QueryEndpoint,
    SplitInputOutput,
)


class TestSplitInputOutputDecorator:
    """Test suite for @SplitInputOutput decorator."""

    def test_split_input_output_marks_class(self) -> None:
        """Test that @SplitInputOutput marks a class with metadata."""

        @SplitInputOutput()
        class TestModel(BaseModel):
            field: str

        assert hasattr(TestModel, SplitInputOutput._ATTR_NAME)
        assert SplitInputOutput.get_last(TestModel) is not None

    def test_is_split_model_returns_true_for_decorated_class(self) -> None:
        """Test that is_split_model returns True for decorated classes."""

        @SplitInputOutput()
        class SplitModel(BaseModel):
            value: int

        assert SplitInputOutput.is_split_model(SplitModel) is True

    def test_is_split_model_returns_false_for_undecorated_class(self) -> None:
        """Test that is_split_model returns False for non-decorated classes."""

        class NotSplitModel(BaseModel):
            value: int

        assert SplitInputOutput.is_split_model(NotSplitModel) is False

    def test_split_input_output_with_expose_type(self) -> None:
        """Test that @SplitInputOutput works with @ExposeType."""

        @ExposeType()
        @SplitInputOutput()
        class CombinedModel(BaseModel):
            id: str
            name: str

        assert SplitInputOutput.is_split_model(CombinedModel)
        assert ExposeType.is_exposed_type(CombinedModel)


class TestQueryEndpointDecorator:
    """Test suite for @QueryEndpoint decorator."""

    def test_query_endpoint_marks_function(self) -> None:
        """Test that @QueryEndpoint marks a function with metadata."""

        @QueryEndpoint()
        def test_func() -> None:
            pass

        assert hasattr(test_func, QueryEndpoint._ATTR_NAME)

    def test_query_endpoint_with_infinite_query(self) -> None:
        """Test @QueryEndpoint with has_infinite_query parameter."""

        @QueryEndpoint(has_infinite_query=True)
        def test_func() -> None:
            pass

        query_endpoint = QueryEndpoint.get_last(test_func)
        assert query_endpoint is not None
        assert query_endpoint.has_infinite_query is True

    def test_query_endpoint_without_infinite_query(self) -> None:
        """Test @QueryEndpoint without infinite query support."""

        @QueryEndpoint(has_infinite_query=False)
        def test_func() -> None:
            pass

        query_endpoint = QueryEndpoint.get_last(test_func)
        assert query_endpoint is not None
        assert query_endpoint.has_infinite_query is False

    def test_extract_query_endpoint_returns_none_for_undecorated(self) -> None:
        """Test that extract_query_endpoint returns None for undecorated functions."""

        def test_func() -> None:
            pass

        assert QueryEndpoint.get_last(test_func) is None


class TestMutationEndpointDecorator:
    """Test suite for @MutationEndpoint decorator."""

    def test_mutation_endpoint_marks_function(self) -> None:
        """Test that @MutationEndpoint marks a function with metadata."""

        @MutationEndpoint()
        def test_func() -> None:
            pass

        assert hasattr(test_func, MutationEndpoint._ATTR_NAME)

    def test_is_mutation_returns_true_for_decorated_function(self) -> None:
        """Test that is_mutation returns True for decorated functions."""

        @MutationEndpoint()
        def test_func() -> None:
            pass

        assert MutationEndpoint.is_mutation(test_func) is True

    def test_is_mutation_returns_false_for_undecorated_function(self) -> None:
        """Test that is_mutation returns False for undecorated functions."""

        def test_func() -> None:
            pass

        assert MutationEndpoint.is_mutation(test_func) is False
