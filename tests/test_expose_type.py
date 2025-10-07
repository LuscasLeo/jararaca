"""
Tests for the @ExposeType decorator.
"""

from pydantic import BaseModel

from jararaca import ExposeType


class TestExposeTypeDecorator:
    """Test suite for @ExposeType decorator functionality."""

    def test_expose_type_decorator_marks_class(self) -> None:
        """Test that @ExposeType marks a class with metadata."""

        @ExposeType()
        class TestModel(BaseModel):
            field: str

        assert hasattr(TestModel, ExposeType.METADATA_KEY)
        assert getattr(TestModel, ExposeType.METADATA_KEY) is True

    def test_expose_type_tracks_in_global_set(self) -> None:
        """Test that @ExposeType adds class to global tracking set."""
        # Clear any existing tracked types
        initial_count = len(ExposeType.get_all_exposed_types())

        @ExposeType()
        class TrackedModel(BaseModel):
            id: str
            name: str

        exposed_types = ExposeType.get_all_exposed_types()
        assert TrackedModel in exposed_types
        assert len(exposed_types) == initial_count + 1

    def test_is_exposed_type_returns_true_for_decorated_class(self) -> None:
        """Test that is_exposed_type returns True for decorated classes."""

        @ExposeType()
        class ExposedModel(BaseModel):
            value: int

        assert ExposeType.is_exposed_type(ExposedModel) is True

    def test_is_exposed_type_returns_false_for_undecorated_class(self) -> None:
        """Test that is_exposed_type returns False for non-decorated classes."""

        class NotExposedModel(BaseModel):
            value: int

        assert ExposeType.is_exposed_type(NotExposedModel) is False

    def test_expose_type_returns_original_class(self) -> None:
        """Test that @ExposeType returns the class unchanged."""

        @ExposeType()
        class OriginalModel(BaseModel):
            field1: str
            field2: int

        # Should be able to instantiate normally
        instance = OriginalModel(field1="test", field2=42)
        assert instance.field1 == "test"
        assert instance.field2 == 42

    def test_expose_type_with_nested_types(self) -> None:
        """Test @ExposeType with models that have nested types."""

        @ExposeType()
        class NestedModel(BaseModel):
            nested_field: str

        @ExposeType()
        class ParentModel(BaseModel):
            id: str
            nested: NestedModel

        exposed = ExposeType.get_all_exposed_types()
        assert NestedModel in exposed
        assert ParentModel in exposed

    def test_multiple_expose_type_decorations(self) -> None:
        """Test that multiple classes can be decorated independently."""

        @ExposeType()
        class Model1(BaseModel):
            field1: str

        @ExposeType()
        class Model2(BaseModel):
            field2: int

        @ExposeType()
        class Model3(BaseModel):
            field3: bool

        exposed = ExposeType.get_all_exposed_types()
        assert Model1 in exposed
        assert Model2 in exposed
        assert Model3 in exposed

    def test_expose_type_with_optional_fields(self) -> None:
        """Test @ExposeType with models containing optional fields."""

        @ExposeType()
        class OptionalModel(BaseModel):
            required: str
            optional: str | None = None
            with_default: int = 42

        # Should work normally with Pydantic
        instance1 = OptionalModel(required="test")
        assert instance1.optional is None
        assert instance1.with_default == 42

        instance2 = OptionalModel(required="test", optional="value", with_default=100)
        assert instance2.optional == "value"
        assert instance2.with_default == 100

    def test_expose_type_with_complex_types(self) -> None:
        """Test @ExposeType with complex type annotations."""

        @ExposeType()
        class ComplexModel(BaseModel):
            list_field: list[str]
            dict_field: dict[str, int]
            union_field: str | int
            nested_list: list[dict[str, str]]

        instance = ComplexModel(
            list_field=["a", "b"],
            dict_field={"key": 1},
            union_field="string",
            nested_list=[{"x": "y"}],
        )
        assert len(instance.list_field) == 2
        assert instance.dict_field["key"] == 1

    def test_get_all_exposed_types_returns_copy(self) -> None:
        """Test that get_all_exposed_types returns a copy, not the original set."""
        exposed1 = ExposeType.get_all_exposed_types()
        exposed2 = ExposeType.get_all_exposed_types()

        # Should be equal but not the same object
        assert exposed1 == exposed2
        assert exposed1 is not exposed2

        # Modifying one shouldn't affect the other
        @ExposeType()
        class NewModel(BaseModel):
            field: str

        exposed3 = ExposeType.get_all_exposed_types()
        assert NewModel in exposed3
        assert NewModel not in exposed1  # Original copy unchanged

    def test_expose_type_tracking_integration(self) -> None:
        """Integration test verifying multiple types are tracked correctly."""

        # Define multiple exposed types
        @ExposeType()
        class ExampleType1(BaseModel):
            field1: str
            field2: int

        @ExposeType()
        class ExampleType2(BaseModel):
            field1: str
            nested: ExampleType1

        # Define a type that should NOT be exposed
        class NotExposedType(BaseModel):
            internal: str

        # Get all exposed types
        exposed = ExposeType.get_all_exposed_types()

        # Verify decorated types are tracked
        assert ExampleType1 in exposed, "ExampleType1 should be exposed"
        assert ExampleType2 in exposed, "ExampleType2 should be exposed"

        # Verify non-decorated types are not tracked
        assert NotExposedType not in exposed, "NotExposedType should not be exposed"

        # Verify metadata is set correctly
        assert ExposeType.is_exposed_type(
            ExampleType1
        ), "ExampleType1 should be marked as exposed"
        assert ExposeType.is_exposed_type(
            ExampleType2
        ), "ExampleType2 should be marked as exposed"
        assert not ExposeType.is_exposed_type(
            NotExposedType
        ), "NotExposedType should not be marked as exposed"
