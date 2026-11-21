from logging.config import fileConfig

from alembic import context

from services.api.database import Base, engine

if context.config.config_file_name:
    fileConfig(context.config.config_file_name)


def run():
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata,
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run()
