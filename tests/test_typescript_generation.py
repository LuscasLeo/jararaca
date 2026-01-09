# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for TypeScript interface generation.
"""

from enum import Enum

from pydantic import BaseModel

from jararaca.tools.typescript.interface_parser import (
    get_field_type_for_ts,
    parse_type_to_typescript_interface,
    pascal_to_camel,
    snake_to_camel,
)


class TestSnakeToCamel:
    """Test suite for snake_to_camel conversion."""

    def test_snake_to_camel_basic(self) -> None:
        """Test basic snake_case to camelCase conversion."""
        assert snake_to_camel("hello_world") == "helloWorld"
        assert snake_to_camel("user_name") == "userName"
        assert snake_to_camel("is_active") == "isActive"

    def test_snake_to_camel_single_word(self) -> None:
        """Test single word (no conversion needed)."""
        assert snake_to_camel("hello") == "hello"
        assert snake_to_camel("user") == "user"

    def test_snake_to_camel_multiple_underscores(self) -> None:
        """Test with multiple underscores."""
        assert snake_to_camel("hello_world_test") == "helloWorldTest"
        assert snake_to_camel("user_profile_image_url") == "userProfileImageUrl"


class TestPascalToCamel:
    """Test suite for pascal_to_camel conversion."""

    def test_pascal_to_camel_basic(self) -> None:
        """Test basic PascalCase to camelCase conversion."""
        assert pascal_to_camel("HelloWorld") == "helloWorld"
        assert pascal_to_camel("UserName") == "userName"
        assert pascal_to_camel("IsActive") == "isActive"

    def test_pascal_to_camel_single_char(self) -> None:
        """Test single character."""
        assert pascal_to_camel("A") == "a"
        assert pascal_to_camel("Z") == "z"

    def test_pascal_to_camel_empty_string(self) -> None:
        """Test empty string."""
        assert pascal_to_camel("") == ""


class TestGetFieldTypeForTs:
    """Test suite for get_field_type_for_ts."""

    def test_primitive_types(self) -> None:
        """Test conversion of primitive Python types to TypeScript."""
        assert get_field_type_for_ts(str) == "string"
        assert get_field_type_for_ts(int) == "number"
        assert get_field_type_for_ts(float) == "number"
        assert get_field_type_for_ts(bool) == "boolean"

    def test_list_types(self) -> None:
        """Test conversion of list types."""
        assert get_field_type_for_ts(list[str]) == "Array<string>"
        assert get_field_type_for_ts(list[int]) == "Array<number>"

    def test_dict_types(self) -> None:
        """Test conversion of dict types."""
        assert get_field_type_for_ts(dict[str, str]) == "{[key: string]: string}"
        assert get_field_type_for_ts(dict[str, int]) == "{[key: string]: number}"

    def test_optional_types(self) -> None:
        """Test conversion of optional types."""
        result = get_field_type_for_ts(str | None)
        assert "string" in result
        assert "null" in result


class TestParseTypeToTypescriptInterface:
    """Test suite for parse_type_to_typescript_interface."""

    def test_simple_model(self) -> None:
        """Test parsing a simple Pydantic model."""

        class SimpleModel(BaseModel):
            id: str
            name: str
            age: int

        mapped_types, interface = parse_type_to_typescript_interface(SimpleModel)

        assert "export interface SimpleModel" in interface
        assert "id: string" in interface
        assert "name: string" in interface
        assert "age: number" in interface

    def test_model_with_optional_fields(self) -> None:
        """Test parsing a model with optional fields."""

        class ModelWithOptional(BaseModel):
            required: str
            optional: str | None = None

        mapped_types, interface = parse_type_to_typescript_interface(ModelWithOptional)

        assert "export interface ModelWithOptional" in interface
        assert "required: string" in interface
        assert "optional?" in interface

    def test_enum_model(self) -> None:
        """Test parsing an Enum."""

        class Status(str, Enum):
            ACTIVE = "active"
            INACTIVE = "inactive"

        mapped_types, interface = parse_type_to_typescript_interface(Status)

        assert "export enum Status" in interface
        assert 'ACTIVE = "active"' in interface
        assert 'INACTIVE = "inactive"' in interface

    def test_nested_model(self) -> None:
        """Test parsing a model with nested types."""

        class Address(BaseModel):
            street: str
            city: str

        class User(BaseModel):
            name: str
            address: Address

        mapped_types, interface = parse_type_to_typescript_interface(User)

        assert "export interface User" in interface
        assert "name: string" in interface
        assert "address: Address" in interface
        assert Address in mapped_types
