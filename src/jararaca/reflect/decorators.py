# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from typing import Any, Callable, Self, TypedDict, TypeVar, cast

DECORATED_T = TypeVar("DECORATED_T", bound="Callable[..., Any] | type")


S = TypeVar("S", bound="StackableDecorator")


class DecoratorMetadata(TypedDict):
    decorators: "list[StackableDecorator]"
    decorators_by_type: "dict[Any, list[StackableDecorator]]"


class StackableDecorator:
    _ATTR_NAME: str = "__jararaca_stackable_decorator__"

    def __call__(self, subject: DECORATED_T) -> DECORATED_T:
        self.pre_decorated(subject)
        self.register(subject, self)
        self.post_decorated(subject)
        return subject

    @classmethod
    def decorator_key(cls) -> Any:
        return cls

    @classmethod
    def get_or_set_metadata(cls, subject: Any) -> DecoratorMetadata:
        if cls._ATTR_NAME not in subject.__dict__:
            setattr(
                subject,
                cls._ATTR_NAME,
                DecoratorMetadata(decorators=[], decorators_by_type={}),
            )
        return cast(DecoratorMetadata, getattr(subject, cls._ATTR_NAME))

    @classmethod
    def get_metadata(cls, subject: Any) -> DecoratorMetadata | None:
        if hasattr(subject, cls._ATTR_NAME):
            return cast(DecoratorMetadata, getattr(subject, cls._ATTR_NAME))
        return None

    @classmethod
    def register(cls, subject: Any, decorator: "StackableDecorator") -> None:
        if not cls._ATTR_NAME:
            raise NotImplementedError("Subclasses must define _ATTR_NAME")

        metadata = cls.get_or_set_metadata(subject)
        metadata["decorators"].append(decorator)
        metadata["decorators_by_type"].setdefault(cls.decorator_key(), []).append(
            decorator
        )

    @classmethod
    def get(cls, subject: Any) -> list[Self]:
        metadata = cls.get_metadata(subject)
        if metadata is None:
            return []

        if cls is StackableDecorator:
            return cast(list[Self], metadata["decorators"])
        else:
            return cast(
                list[Self], metadata["decorators_by_type"].get(cls.decorator_key(), [])
            )

    @classmethod
    def extract_list(cls, subject: Any) -> list[Self]:
        metadata = cls.get_metadata(subject)
        if metadata is None:
            return []

        if cls is StackableDecorator:
            return cast(list[Self], metadata["decorators"])
        else:
            return cast(
                list[Self], metadata["decorators_by_type"].get(cls.decorator_key(), [])
            )

    @classmethod
    def get_fisrt(cls, subject: Any) -> Self | None:
        decorators = cls.get(subject)
        if decorators:
            return decorators[0]
        return None

    @classmethod
    def get_last(cls, subject: Any) -> Self | None:
        decorators = cls.get(subject)
        if decorators:
            return decorators[-1]
        return None

    def pre_decorated(self, subject: DECORATED_T) -> None:
        """
        Hook method called before the subject is decorated.
        Can be overridden by subclasses to perform additional setup.
        """

    def post_decorated(self, subject: DECORATED_T) -> None:
        """
        Hook method called after the subject has been decorated.
        Can be overridden by subclasses to perform additional setup.
        """

    @classmethod
    def get_all_from_type(cls, subject_type: type, inherit: bool = True) -> list[Self]:
        """
        Retrieve all decorators of this type from the given class type.
        """
        return resolve_class_decorators(subject_type, cls, inherit)

    @classmethod
    def get_bound_from_type(
        cls, subject_type: type, inherit: bool = True, last: bool = False
    ) -> Self | None:
        """
        Retrieve the first or last decorator of this type from the given class type.
        """
        return resolve_bound_class_decorators(subject_type, cls, inherit, last=last)

    @classmethod
    def get_all_from_method(
        cls, cls_subject_type: type, method_name: str, inherit: bool = True
    ) -> list[Self]:
        """
        Retrieve all decorators of this type from the given method.
        """
        return resolve_method_decorators(cls_subject_type, method_name, cls, inherit)

    @classmethod
    def get_bound_from_method(
        cls,
        cls_subject_type: type,
        method_name: str,
        inherit: bool = True,
        last: bool = True,
    ) -> Self | None:
        """
        Retrieve the first or last decorator of this type from the given method.
        """
        return resolve_bound_method_decorator(
            cls_subject_type, method_name, cls, inherit, last=last
        )


def resolve_class_decorators(
    subject: Any, decorator_cls: type[S], inherit: bool = True
) -> list[S]:
    """
    Resolve decorators for a class or instance, optionally inheriting from base classes.
    """
    if not inherit:
        return decorator_cls.get(subject)

    # If subject is an instance, get its class
    cls = subject if isinstance(subject, type) else type(subject)

    collected: list[S] = []
    # Iterate MRO in reverse to apply base class decorators first
    for base in reversed(cls.mro()):
        collected.extend(decorator_cls.get(base))

    return collected


def resolve_bound_class_decorators(
    subject: Any, decorator_cls: type[S], inherit: bool = True, last: bool = False
) -> S | None:
    """
    Retrieve the first or last decorator of a given type from a class or instance,
    optionally inheriting from base classes.
    """
    decorators = resolve_class_decorators(subject, decorator_cls, inherit)
    if not decorators:
        return None
    return decorators[-1] if last else decorators[0]


def resolve_method_decorators(
    cls: type,
    method_name: str,
    decorator_cls: type[S],
    inherit: bool = True,
) -> list[S]:
    """
    Resolve decorators for a method, optionally inheriting from base classes.
    """
    if not inherit:
        method = getattr(cls, method_name, None)
        if method:
            return decorator_cls.get(method)
        return []

    collected: list[S] = []
    # Iterate MRO in reverse to apply base class decorators first
    for base in reversed(cls.mro()):
        if method_name in base.__dict__:
            method = base.__dict__[method_name]
            # Handle staticmethod/classmethod wrappers if necessary?
            # Usually decorators are on the underlying function or the wrapper.
            # getattr(cls, name) returns the bound/unbound method.
            # base.__dict__[name] returns the raw object (function or descriptor).

            # If it's a staticmethod object, it has no __dict__ usually, but we attach attributes to it?
            # The decorator runs on the function BEFORE it becomes a staticmethod.
            # So the attribute should be on the function object.

            # However, when we access it via base.__dict__[method_name], we might get the staticmethod object.
            # We need to unwrap it if possible.

            if isinstance(method, (staticmethod, classmethod)):
                method = method.__func__

            collected.extend(decorator_cls.get(method))

    return collected


def resolve_bound_method_decorator(
    cls: type,
    method_name: str,
    decorator_cls: type[S],
    inherit: bool = True,
    last: bool = False,
) -> S | None:
    """
    Retrieve the first or last decorator of a given type from a method,
    optionally inheriting from base classes.
    """
    decorators = resolve_method_decorators(cls, method_name, decorator_cls, inherit)
    if not decorators:
        return None
    return decorators[-1] if last else decorators[0]
