from sqlalchemy import Table, Column, Integer, String, TIMESTAMP, MetaData, ForeignKey

metadata = MetaData()

stands = Table(
    "stands",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String, nullable=False),
    Column("storage_name", String, nullable=False),
    Column("ip", String, nullable=False),
    Column("user_admin", String, nullable=False),
    Column("pass_admin", String, nullable=False)
)

versions = Table(
    'versions',
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String, nullable=False),
    Column("digit_name", String, nullable=False)
)

snapshots = Table(
    'snapshots',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('name', String, nullable=False),
    Column('version_id', Integer, ForeignKey('versions.id')),
    Column("stand_id", Integer, ForeignKey("stands.id"))
)

repos = Table(
    'repos',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('link', String, nullable=False),
    Column('version_id', Integer, ForeignKey('versions.id'))
)