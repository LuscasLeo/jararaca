from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import Mapped, mapped_column

from jararaca import (
    CRUDOperations,
    DatedEntity,
    Delete,
    Get,
    Identifiable,
    IdentifiableEntity,
    Paginated,
    PaginatedFilter,
    Patch,
    Post,
    QueryOperations,
    RestController,
    raises_404_on,
    use_session,
)


class ${entityNamePascalCase}Entity(IdentifiableEntity, DatedEntity):

    __tablename__ = "${entityNameSnakeCase}"

    nome: Mapped[str] = mapped_column(nullable=False)

    def update_from_basemodel(self, payload: BaseModel) -> None:

        for field in payload.model_fields_set:
            setattr(self, field, getattr(payload, field))


class ${entityNamePascalCase}MutationSchema(BaseModel):
    nome: str


class ${entityNamePascalCase}Schema(BaseModel):
    nome: str
    created_at: datetime


class ${entityNamePascalCase}Filter(PaginatedFilter): ...


@RestController("/${entityNameKebabCase}")
class ${entityNamePascalCase}Controller:

    def __init__(self):

        self.query_operations = QueryOperations[
            ${entityNamePascalCase}Filter, ${entityNamePascalCase}Entity
        ](${entityNamePascalCase}Entity, use_session, [])

        self.crud_operations = CRUDOperations(${entityNamePascalCase}Entity, use_session)

    @Post("/")
    async def create_${entityNameSnakeCase}(
        self, payload: ${entityNamePascalCase}MutationSchema
    ) -> Identifiable[${entityNamePascalCase}Schema]:
        entity = ${entityNamePascalCase}Entity.from_basemodel(payload)
        entity.id = uuid4()

        use_session().add(entity)
        await use_session().flush()

        return entity.to_identifiable(${entityNamePascalCase}Schema)

    @Get("/")
    async def query_${entityNameSnakeCase}(
        self, filter: Annotated[${entityNamePascalCase}Filter, Depends(${entityNamePascalCase}Filter)]
    ) -> Paginated[Identifiable[${entityNamePascalCase}Schema]]:
        return (await self.query_operations.query(filter)).transform(
            lambda entity: entity.to_identifiable(${entityNamePascalCase}Schema)
        )

    @Patch("/{id}")
    async def update_${entityNameSnakeCase}(
        self, id: UUID, payload: ${entityNamePascalCase}MutationSchema
    ) -> Identifiable[${entityNamePascalCase}Schema]:
        with raises_404_on(NoResultFound):
            entity = await self.crud_operations.get(id)

            entity.update_from_basemodel(payload)

            await use_session().flush()

            return entity.to_identifiable(${entityNamePascalCase}Schema)

    @Delete("/")
    async def deactivate_${entityNameSnakeCase}(self, id: UUID) -> None: ...

    @Get("/{id}")
    async def get_${entityNameSnakeCase}(self, id: UUID) -> Identifiable[${entityNamePascalCase}Schema]:
        with raises_404_on(NoResultFound):
            entity = await self.crud_operations.get(id)
            return entity.to_identifiable(${entityNamePascalCase}Schema)
